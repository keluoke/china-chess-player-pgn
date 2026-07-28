#!/usr/bin/env python3
"""Manifest-driven API fallback for delivering an outbox release bundle.

This is the deterministic fallback when every Git route to github.com fails.
It consumes an existing, immutable outbox bundle (exact release manifest plus
hashed file contents) and force-moves the single-writer ``local-data`` ref via
the GitHub Git Database API. The same ``run_manager.validate_manifest`` policy
gate is applied — there is no second, wider allowlist. Ad-hoc ``--path``
publication is permanently retired: without a validated bundle this tool
fails closed.
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import hashlib
import json
import pathlib
import re
import subprocess
import sys

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent))

import run_manager  # noqa: E402


def repository_name() -> str:
    remote = subprocess.run(
        ["git", "remote", "get-url", "origin"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    match = re.search(r"github\.com[/:]([^/]+/[^/]+?)(?:\.git)?$", remote)
    if not match:
        raise SystemExit(f"cannot infer GitHub repository from {remote}")
    return match.group(1)


def api(repository: str, method: str, path: str, payload=None):
    command = ["gh", "api", "--method", method, f"repos/{repository}{path}"]
    encoded = None
    if payload is not None:
        command += ["--input", "-"]
        encoded = json.dumps(payload).encode("utf-8")
    result = subprocess.run(command, input=encoded, cwd=ROOT, check=True, capture_output=True, timeout=360)
    return json.loads(result.stdout or b"{}")


def load_bundle(run_id: str | None) -> tuple[pathlib.Path, dict, dict]:
    entries = run_manager.outbox_entries("pending")
    if run_id:
        entries = [item for item in entries if item.get("runId") == run_id]
    if not entries:
        raise SystemExit(
            "API_DELIVERY_BLOCKED: outbox 没有待投递发布包。此工具已退役任意 --path 发布；"
            "请先通过 refresh.sh registry/bulk 生成经过校验的 release 包。"
        )
    delivery = entries[0]
    entry = pathlib.Path(delivery["path"])
    manifest = json.loads((entry / "manifest.json").read_text(encoding="utf-8"))
    return entry, manifest, delivery


def git_blob_oid_for_bytes(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content).hexdigest()


def remote_tree_oids(repository: str, tree_sha: str) -> dict[str, str]:
    """Read the current main tree once instead of one API call per file."""
    payload = api(repository, "GET", f"/git/trees/{tree_sha}?recursive=1")
    if payload.get("truncated"):
        raise SystemExit("API_DELIVERY_TREE_TRUNCATED: GitHub 返回的递归树不完整，已停止投递。")
    return {
        str(item["path"]): str(item["sha"])
        for item in payload.get("tree") or []
        if item.get("type") == "blob" and item.get("path") and item.get("sha")
    }


def upload_blobs(
    repository: str,
    entry: pathlib.Path,
    prepared: list[tuple[dict, bytes | None, str | None]],
    workers: int = 4,
) -> dict[str, str]:
    """Upload missing blobs concurrently and checkpoint deterministic OIDs."""
    cache_path = entry / "uploaded-blobs.json"
    cache_payload = run_manager.read_json(cache_path)
    cache = {
        str(key): str(value)
        for key, value in (cache_payload.get("blobs") or {}).items()
        if key and value
    }
    pending = {
        str(candidate_oid): content
        for item, content, candidate_oid in prepared
        if item.get("operation") == "upsert"
        and content is not None
        and candidate_oid
        and candidate_oid not in cache
    }

    def upload(candidate_oid: str, content: bytes) -> tuple[str, str]:
        blob = api(repository, "POST", "/git/blobs", {
            "content": base64.b64encode(content).decode("ascii"),
            "encoding": "base64",
        })
        remote_oid = str(blob.get("sha") or "")
        if remote_oid != candidate_oid:
            raise SystemExit(
                f"RELEASE_HASH_MISMATCH: GitHub blob OID 与候选不一致：{candidate_oid}"
            )
        return candidate_oid, remote_oid

    if pending:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
            futures = {
                executor.submit(upload, oid, content): oid
                for oid, content in pending.items()
            }
            for future in concurrent.futures.as_completed(futures):
                oid, remote_oid = future.result()
                cache[oid] = remote_oid
                run_manager.atomic_json(cache_path, {
                    "schemaVersion": 1,
                    "blobs": cache,
                })
    return cache


def release_conflicts(base_oid: str | None, current_oid: str | None, candidate_oid: str | None) -> bool:
    return current_oid != base_oid and current_oid != candidate_oid


def local_blob_facts(repo: pathlib.Path, commit: str, path: str) -> tuple[str | None, str | None]:
    """Read a path's immutable Git baseline without touching the worktree."""
    commit_check = subprocess.run(
        ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
        cwd=repo,
        capture_output=True,
    )
    if commit_check.returncode != 0:
        raise SystemExit(
            f"API_DELIVERY_BASELINE_MISSING: 本机缺少 legacy 发布基线 commit {commit}"
        )
    resolved = subprocess.run(
        ["git", "rev-parse", "--verify", f"{commit}:{path}"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if resolved.returncode != 0:
        return None, None
    oid = resolved.stdout.strip()
    kind = subprocess.run(
        ["git", "cat-file", "-t", oid],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if kind != "blob":
        raise SystemExit(f"API_DELIVERY_BASELINE_MISSING: legacy 基线路径不是普通文件：{path}")
    content = subprocess.run(
        ["git", "cat-file", "blob", oid],
        cwd=repo,
        check=True,
        capture_output=True,
    ).stdout
    return oid, hashlib.sha256(content).hexdigest()


def hydrate_legacy_baseline(
    repo: pathlib.Path,
    manifest: dict,
    files: list[dict],
) -> bool:
    """Upgrade a legacy bundle in memory from its immutable baseCommit.

    The outbox files remain untouched. Missing or inconsistent local history
    still fails closed; remote divergence is checked later by the same
    three-way guard used for newly generated manifests.
    """
    legacy = [item for item in files if "baseBlobOid" not in item and "baseSha256" not in item]
    if not legacy:
        return False
    base_commit = str(manifest.get("baseCommit") or "")
    if not re.fullmatch(r"[0-9a-f]{40,64}", base_commit):
        raise SystemExit("API_DELIVERY_BASELINE_MISSING: legacy 发布包缺少有效 baseCommit")
    for item in legacy:
        oid, digest = local_blob_facts(repo, base_commit, item["path"])
        item["baseBlobOid"] = oid
        item["baseSha256"] = digest
    run_manager.validate_manifest(manifest)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outbox", metavar="RUN_ID", help="deliver this pending outbox bundle (default: oldest)")
    parser.add_argument("--data-branch", default="local-data")
    parser.add_argument("--base-branch", default="main")
    args = parser.parse_args()

    entry, manifest, _delivery = load_bundle(args.outbox)
    # Fail closed on anything outside the shared machine-release policy.
    files = run_manager.validate_manifest(manifest)
    if hydrate_legacy_baseline(ROOT, manifest, files):
        print(f"legacy 发布包已从 baseCommit {manifest['baseCommit']} 恢复逐文件基线。", file=sys.stderr)
    run_id = str(manifest.get("runId"))

    repository = repository_name()
    parent = api(repository, "GET", f"/git/ref/heads/{args.base_branch}")["object"]["sha"]
    base_tree = api(repository, "GET", f"/git/commits/{parent}")["tree"]["sha"]
    current_tree = remote_tree_oids(repository, base_tree)

    prepared: list[tuple[dict, bytes | None, str | None]] = []
    conflicts: list[str] = []
    for item in files:
        path = item["path"]
        content = None
        candidate_oid = None
        if item["operation"] == "upsert":
            source = entry / "files" / path
            if not source.is_file():
                raise SystemExit(f"API_DELIVERY_BLOCKED: outbox 缺少文件内容：{path}")
            content = source.read_bytes()
            if hashlib.sha256(content).hexdigest() != item.get("sha256"):
                raise SystemExit(f"RELEASE_HASH_MISMATCH: outbox 文件与 manifest 哈希不符：{path}")
            candidate_oid = git_blob_oid_for_bytes(content)

        current_oid = current_tree.get(path)
        if release_conflicts(item.get("baseBlobOid"), current_oid, candidate_oid):
            conflicts.append(path)

        item["deliveryBaseBlobOid"] = current_oid
        if current_oid == item.get("baseBlobOid"):
            item["deliveryBaseSha256"] = item.get("baseSha256")
        elif current_oid == candidate_oid and item["operation"] == "upsert":
            item["deliveryBaseSha256"] = item.get("sha256")
        else:
            item["deliveryBaseSha256"] = None
        prepared.append((item, content, candidate_oid))

    if conflicts:
        raise SystemExit(
            "RELEASE_BASE_CONFLICT: main 已修改以下路径，API 候选已隔离且未创建远端对象："
            + ", ".join(conflicts[:8])
        )

    manifest["deliveryBaseCommit"] = parent
    # Re-run the shared validator after adding the delivery baseline.
    files = run_manager.validate_manifest(manifest)

    uploaded_blobs = upload_blobs(repository, entry, prepared)
    tree_entries: list[dict] = []
    for item, content, candidate_oid in prepared:
        path = item["path"]
        if item["operation"] == "delete":
            tree_entries.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
            continue
        assert content is not None
        assert candidate_oid is not None
        tree_entries.append({
            "path": path,
            "mode": "100644",
            "type": "blob",
            "sha": uploaded_blobs[candidate_oid],
        })
    # The manifest itself rides along so cloud ingest can validate it.
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    manifest_blob = api(repository, "POST", "/git/blobs", {
        "content": base64.b64encode(manifest_bytes).decode("ascii"),
        "encoding": "base64",
    })
    tree_entries.append({
        "path": run_manager.MANIFEST_PATH, "mode": "100644", "type": "blob", "sha": manifest_blob["sha"],
    })

    tree = api(repository, "POST", "/git/trees", {"base_tree": base_tree, "tree": tree_entries})
    commit = api(repository, "POST", "/git/commits", {
        "message": f"Deliver validated local release {run_id} via API",
        "tree": tree["sha"],
        "parents": [parent],
    })
    ref_path = f"/git/refs/heads/{args.data_branch}"
    existing = subprocess.run(
        ["gh", "api", f"repos/{repository}/git/ref/heads/{args.data_branch}"], cwd=ROOT, capture_output=True
    )
    if existing.returncode == 0:
        api(repository, "PATCH", ref_path, {"sha": commit["sha"], "force": True})
    else:
        api(repository, "POST", "/git/refs", {"ref": f"refs/heads/{args.data_branch}", "sha": commit["sha"]})
    run_manager.outbox_update(run_id, "pushed", commit["sha"], "api", None)
    print(json.dumps({
        "branch": args.data_branch,
        "commit": commit["sha"],
        "runId": run_id,
        "files": len(files),
        "transport": "github-git-database-api",
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
