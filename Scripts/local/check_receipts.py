#!/usr/bin/env python3
"""Advance outbox delivery states with real cloud receipts.

"pushed" is not "published". For every outbox bundle past pending this tool
queries the GitHub Actions API (via the authenticated ``gh`` CLI) for the
ingest / rebuild / deploy workflow conclusions, then verifies one released
file's SHA-256 on the live Cloudflare Pages site. Each confirmation advances
the bundle's delivery state:

    pushed → ingested-to-main → indexes-rebuilt → deployed → online-verified

Nothing here touches a data source. Ordinary failed checks never roll a state
back. The one recovery exception is a ``pushed`` bundle whose exact ingest run
was canceled, or never appeared before a bounded timeout: that immutable
bundle returns to ``pending`` for a safe resend without re-scraping.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from typing import Any

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent))

import run_manager  # noqa: E402

SITE_URL = os.environ.get("CHINA_CHESS_SITE_URL", "https://china-chess-player-pgn.pages.dev").rstrip("/")
STAGE_ORDER = ["pushed", "ingested-to-main", "indexes-rebuilt", "deployed", "online-verified"]
STAGE_WORKFLOWS = {
    "ingested-to-main": "ingest-local-data.yml",
    "indexes-rebuilt": "rebuild-indexes.yml",
    "deployed": "deploy.yml",
}
INGEST_RECEIPT_TIMEOUT_SECONDS = 15 * 60
DEPLOY_TITLE_RE = re.compile(r"^Deploy exact ([0-9a-f]{40})$")


def repository_name() -> str:
    remote = subprocess.run(
        ["git", "remote", "get-url", "origin"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    match = re.search(r"github\.com[/:]([^/]+/[^/]+?)(?:\.git)?$", remote)
    if not match:
        raise SystemExit(f"cannot infer GitHub repository from {remote}")
    return match.group(1)


def gh_api(path: str) -> Any:
    result = subprocess.run(
        ["gh", "api", path], cwd=ROOT, capture_output=True, timeout=60
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or b"gh api failed").decode("utf-8", "replace")[:300])
    return json.loads(result.stdout or b"{}")


def parse_time(value: Any) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def workflow_runs(repository: str, workflow: str, head_sha: str | None = None) -> list[dict[str, Any]]:
    query = f"?per_page=10" + (f"&head_sha={head_sha}" if head_sha else "")
    payload = gh_api(f"repos/{repository}/actions/workflows/{workflow}/runs{query}")
    return payload.get("workflow_runs") or []


def successful_run_after(runs: list[dict[str, Any]], after: dt.datetime | None) -> dict[str, Any] | None:
    for run in runs:
        if run.get("status") != "completed" or run.get("conclusion") != "success":
            continue
        created = parse_time(run.get("created_at"))
        if after and created and created < after:
            continue
        return run
    return None


def ingest_retry_reason(
    delivery: dict[str, Any],
    runs: list[dict[str, Any]],
    *,
    checked_at: dt.datetime | None = None,
    timeout_seconds: int = INGEST_RECEIPT_TIMEOUT_SECONDS,
) -> str | None:
    """Return a retry code only when the exact push has no active ingest."""
    if delivery.get("status") != "pushed":
        return None
    if successful_run_after(runs, None):
        return None
    # A queued/in-progress run blocks duplicate delivery. Completed failures
    # other than cancellation keep their ordinary diagnostic state.
    if any(run.get("status") != "completed" for run in runs):
        return None
    conclusions = {str(run.get("conclusion") or "") for run in runs}
    if conclusions and conclusions <= {"cancelled", "canceled"}:
        return "INGEST_RUN_CANCELLED"
    if runs:
        return None
    anchor = parse_time(
        delivery.get("pushedAt")
        or delivery.get("createdAt")
        or delivery.get("updatedAt")
    )
    if anchor is None:
        return None
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=dt.timezone.utc)
    current = checked_at or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    if (current - anchor).total_seconds() >= timeout_seconds:
        return "INGEST_RECEIPT_TIMEOUT"
    return None


def deploy_target_sha(run: dict[str, Any]) -> str:
    """Return the exact checked-out SHA encoded in the deploy receipt."""
    title = str(run.get("display_title") or "")
    match = DEPLOY_TITLE_RE.fullmatch(title)
    if match:
        return match.group(1)
    return str(run.get("head_sha") or "")


def fetch_online_bytes(url: str) -> bytes:
    """Fetch through the platform trust store when curl is available.

    Maintainer macOS installations frequently lack a Python certificate bundle
    even though the system trust store is healthy. Curl uses that trust store
    and keeps online verification deterministic; urllib remains the portable
    fallback for minimal environments.
    """
    curl = shutil.which("curl")
    if curl:
        result = subprocess.run(
            [
                curl,
                "--fail",
                "--silent",
                "--show-error",
                "--location",
                "--max-time",
                "30",
                "--header",
                "Cache-Control: no-cache",
                "--user-agent",
                "ChinaChessPlayerPGN/ReceiptCheck",
                url,
            ],
            capture_output=True,
            timeout=35,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or b"curl failed").decode("utf-8", "replace")[:200])
        return result.stdout
    request = urllib.request.Request(url, headers={
        "User-Agent": "ChinaChessPlayerPGN/ReceiptCheck",
        "Cache-Control": "no-cache",
    })
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def remote_file_bytes(repository: str, ref: str, path: str) -> bytes:
    encoded_path = urllib.parse.quote(path, safe="/")
    encoded_ref = urllib.parse.quote(ref, safe="")
    payload = gh_api(f"repos/{repository}/contents/{encoded_path}?ref={encoded_ref}")
    content = str(payload.get("content") or "").replace("\n", "")
    if payload.get("encoding") != "base64" or not content:
        raise RuntimeError(f"远端文件内容不可用：{ref}:{path}")
    return base64.b64decode(content)


def verify_release_reachable_from_snapshot(
    repository: str,
    snapshot_body: bytes,
    release_run_id: str,
) -> dict[str, Any]:
    """Prove that the snapshot input commit includes the release in its history.

    The current local-release manifest is intentionally replaced by every new
    ingest, so comparing only its latest bytes would make older releases
    unverifiable. GitHub's path history, anchored at the immutable snapshot
    input commit, lets us find the release's run-id even after later ingests.
    """
    try:
        snapshot = json.loads(snapshot_body)
        input_commit = str(snapshot.get("inputCommit") or "")
    except (AttributeError, json.JSONDecodeError, UnicodeDecodeError):
        input_commit = ""
    if not re.fullmatch(r"[0-9a-f]{40,64}", input_commit):
        return {
            "ok": False,
            "releaseRunId": release_run_id,
            "error": "deployed snapshot does not record a valid input commit",
        }
    query = urllib.parse.urlencode({
        "sha": input_commit,
        "path": "data/generated/local-release-manifest.json",
        "per_page": 100,
    })
    try:
        commits = gh_api(f"repos/{repository}/commits?{query}")
        for commit in commits if isinstance(commits, list) else []:
            sha = str(commit.get("sha") or "")
            message = str((commit.get("commit") or {}).get("message") or "")
            if release_run_id in message:
                return {
                    "ok": True,
                    "releaseRunId": release_run_id,
                    "inputCommit": input_commit,
                    "ingestCommit": sha,
                }
            try:
                release_manifest = json.loads(remote_file_bytes(
                    repository,
                    sha,
                    "data/generated/local-release-manifest.json",
                ))
            except (RuntimeError, AttributeError, json.JSONDecodeError, UnicodeDecodeError):
                continue
            if str(release_manifest.get("runId") or "") == release_run_id:
                return {
                    "ok": True,
                    "releaseRunId": release_run_id,
                    "inputCommit": input_commit,
                    "ingestCommit": sha,
                }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "releaseRunId": release_run_id,
            "inputCommit": input_commit,
            "error": str(exc)[:200],
        }
    return {
        "ok": False,
        "releaseRunId": release_run_id,
        "inputCommit": input_commit,
        "error": "release is not reachable from the deployed snapshot input commit",
    }


def verify_online_file(
    manifest: dict[str, Any],
    fallback: tuple[str, bytes] | None = None,
    release_input_proof: dict[str, Any] | None = None,
    bundle_root: pathlib.Path | None = None,
) -> dict[str, Any]:
    """Fetch one released public file from its serving tier and compare hashes.

    Event archives are deliberately excluded from Pages and served from R2;
    their bundle receipt is therefore authoritative for the public URL.
    Other ``docs/`` files are served from the Pages site root.
    """
    event_items = []
    for item in manifest.get("files") or []:
        path = str(item.get("path") or "")
        if item.get("operation") == "upsert" and re.fullmatch(
            r"data/generated/chess-results-event-pgn/tnr\d+\.pgn", path
        ):
            event_items.append(item)
    if event_items and bundle_root is not None:
        receipt_path = (
            bundle_root / "files" / "data" / "generated" /
            "r2-object-receipts" / "events--chess-results.json"
        )
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {"ok": False, "error": f"R2 event receipt unavailable: {exc}"}
        objects = {
            str(row.get("key") or ""): row
            for row in receipt.get("objects") or []
            if isinstance(row, dict)
        }
        item = event_items[0]
        name = pathlib.PurePosixPath(str(item["path"])).name
        key = f"events/chess-results/{name}"
        remote = objects.get(key)
        expected = str(item.get("sha256") or "")
        if not remote or remote.get("sha256") != expected or not remote.get("publicURL"):
            return {
                "ok": False,
                "error": f"R2 event receipt does not certify {key}",
                "expected": expected,
            }
        url = str(remote["publicURL"])
        try:
            body = fetch_online_bytes(url)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "url": url, "error": str(exc)[:200]}
        actual = hashlib.sha256(body).hexdigest()
        return {
            "ok": actual == expected,
            "url": url,
            "expected": expected,
            "actual": actual,
            "verification": "r2-event-object",
        }
    for item in manifest.get("files") or []:
        path = str(item.get("path") or "")
        if (
            item.get("operation") != "upsert"
            or not path.startswith("docs/")
            or path.startswith("docs/data/pgn/")
        ):
            continue
        url = f"{SITE_URL}/{path[len('docs/'):]}"
        try:
            body = fetch_online_bytes(url)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "url": url, "error": str(exc)[:200]}
        digest = hashlib.sha256(body).hexdigest()
        return {
            "ok": digest == item.get("sha256"),
            "url": url,
            "expected": item.get("sha256"),
            "actual": digest,
        }
    if fallback:
        path, expected_body = fallback
        url = f"{SITE_URL}/{path[len('docs/'):] if path.startswith('docs/') else path}"
        try:
            body = fetch_online_bytes(url)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "url": url, "error": str(exc)[:200]}
        expected = hashlib.sha256(expected_body).hexdigest()
        actual = hashlib.sha256(body).hexdigest()
        release_proof_ok = release_input_proof is None or release_input_proof.get("ok") is True
        return {
            "ok": actual == expected and release_proof_ok,
            "url": url,
            "expected": expected,
            "actual": actual,
            "verification": "deployed-snapshot",
            **({"releaseInputProof": release_input_proof} if release_input_proof else {}),
            **({"error": release_input_proof.get("error")}
               if release_input_proof and not release_proof_ok else {}),
        }
    return {"ok": False, "error": "manifest 中没有可在线校验的 docs/ 文件"}


def advance(delivery: dict[str, Any], stage_results: dict[str, dict[str, Any]]) -> str:
    """Pure state-advance: walk the stage order, stop at the first failure."""
    status = str(delivery.get("status") or "pending")
    if status not in STAGE_ORDER:
        return status
    index = STAGE_ORDER.index(status)
    for stage in STAGE_ORDER[index + 1:]:
        result = stage_results.get(stage) or {}
        if not result.get("ok"):
            break
        status = stage
    return status


def online_error_code(receipts: dict[str, Any]) -> str | None:
    """Return a durable, non-retriable code for a proved online hash mismatch."""
    online = receipts.get("online") or {}
    if (
        online.get("ok") is False
        and online.get("expected")
        and online.get("actual")
        and online.get("expected") != online.get("actual")
    ):
        return "ONLINE_HASH_MISMATCH"
    return None


def check_entry(repository: str, delivery: dict[str, Any]) -> dict[str, Any]:
    run_id = str(delivery.get("runId"))
    remote_sha = delivery.get("remoteSHA") or delivery.get("commit")
    entry = pathlib.Path(delivery["path"])
    manifest = json.loads((entry / "manifest.json").read_text(encoding="utf-8"))

    receipts: dict[str, Any] = dict(delivery.get("receipts") or {})
    stage_results: dict[str, dict[str, Any]] = {}

    ingest_runs = workflow_runs(repository, STAGE_WORKFLOWS["ingested-to-main"], head_sha=remote_sha)
    ingest = successful_run_after(ingest_runs, None)
    if ingest:
        stage_results["ingested-to-main"] = {"ok": True}
        receipts["ingest"] = {
            "runId": ingest.get("id"), "url": ingest.get("html_url"),
            "conclusion": ingest.get("conclusion"), "createdAt": ingest.get("created_at"),
        }
        # Ingest dispatches rebuild before its own post-job cleanup completes,
        # so the child workflow can legitimately be created before ingest's
        # updated_at timestamp. created_at is the stable lower bound.
        ingest_started = parse_time(ingest.get("created_at"))
        rebuild = successful_run_after(
            workflow_runs(repository, STAGE_WORKFLOWS["indexes-rebuilt"]), ingest_started
        )
        if rebuild:
            stage_results["indexes-rebuilt"] = {"ok": True}
            receipts["rebuild"] = {
                "runId": rebuild.get("id"), "url": rebuild.get("html_url"),
                "conclusion": rebuild.get("conclusion"), "createdAt": rebuild.get("created_at"),
            }
            deploy = successful_run_after(
                workflow_runs(repository, STAGE_WORKFLOWS["deployed"]),
                parse_time(rebuild.get("created_at")),
            )
            if deploy:
                deployed_sha = deploy_target_sha(deploy)
                stage_results["deployed"] = {"ok": True}
                receipts["deploy"] = {
                    "runId": deploy.get("id"), "url": deploy.get("html_url"),
                    "conclusion": deploy.get("conclusion"), "createdAt": deploy.get("created_at"),
                    "headSHA": deploy.get("head_sha"),
                    "targetSHA": deployed_sha,
                }
                has_public_file = any(
                    item.get("operation") == "upsert"
                    and str(item.get("path") or "").startswith("docs/")
                    and not str(item.get("path") or "").startswith("docs/data/pgn/")
                    for item in manifest.get("files") or []
                )
                fallback = None
                if not has_public_file and deployed_sha:
                    snapshot_path = "docs/data/snapshot.json"
                    fallback = (
                        snapshot_path,
                        remote_file_bytes(repository, deployed_sha, snapshot_path),
                    )
                release_input_proof = None
                if fallback:
                    release_input_proof = verify_release_reachable_from_snapshot(
                        repository,
                        fallback[1],
                        str(manifest.get("runId") or run_id),
                    )
                online = verify_online_file(
                    manifest,
                    fallback=fallback,
                    release_input_proof=release_input_proof,
                    bundle_root=entry,
                )
                receipts["online"] = {**online, "checkedAt": run_manager.now()}
                if online.get("ok"):
                    stage_results["online-verified"] = {"ok": True}
    else:
        failed = next((run for run in ingest_runs if run.get("conclusion") not in {None, "success"}), None)
        if failed:
            receipts["ingest"] = {
                "runId": failed.get("id"), "url": failed.get("html_url"),
                "conclusion": failed.get("conclusion"), "createdAt": failed.get("created_at"),
            }
        retry_code = ingest_retry_reason(delivery, ingest_runs)
        if retry_code:
            receipts["ingestRecovery"] = {
                "reason": retry_code,
                "checkedAt": run_manager.now(),
                "previousRemoteSHA": remote_sha,
            }
            run_manager.outbox_update(run_id, "pending", None, None, retry_code)
            entry_delivery = run_manager.read_json(entry / "delivery.json")
            entry_delivery["receipts"] = receipts
            run_manager.atomic_json(entry / "delivery.json", entry_delivery)
            return {
                "runId": run_id,
                "status": "pending",
                "requeued": True,
                "reason": retry_code,
                "receipts": receipts,
            }

    new_status = advance(delivery, stage_results)
    updated = run_manager.outbox_update(
        run_id,
        new_status if new_status != delivery.get("status") else None,
        None,
        None,
        online_error_code(receipts),
    )
    updated["receipts"] = receipts
    entry_delivery = run_manager.read_json(entry / "delivery.json")
    entry_delivery["receipts"] = receipts
    run_manager.atomic_json(entry / "delivery.json", entry_delivery)
    return {"runId": run_id, "status": entry_delivery.get("status"), "receipts": receipts}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", help="only check this outbox bundle")
    args = parser.parse_args()
    entries = [
        item for item in run_manager.outbox_entries()
        if item.get("status") in set(STAGE_ORDER[:-1])
        and (not args.run_id or item.get("runId") == args.run_id)
    ]
    if not entries:
        print(json.dumps({"checked": 0, "message": "没有等待云端回执的发布包"}, ensure_ascii=False))
        return 0
    repository = repository_name()
    results = []
    errors = []
    for delivery in entries:
        try:
            results.append(check_entry(repository, delivery))
        except Exception as exc:  # noqa: BLE001
            errors.append({"runId": delivery.get("runId"), "error": str(exc)[:300]})
    print(json.dumps({"checked": len(results), "results": results, "errors": errors}, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
