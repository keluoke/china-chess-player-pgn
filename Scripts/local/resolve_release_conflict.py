#!/usr/bin/env python3
"""Create a new immutable successor for terminal machine-release conflicts.

The source outbox bundles remain untouched.  Candidate files are selected in
the supplied run order (later runs win duplicate paths), explicit rejected
paths are either omitted or converted to reviewed deletes, and the successor
records the reviewed production-main baseline.  This
command never contacts a chess source.
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
import secrets
import subprocess
import sys
import time
from typing import Any

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent))

import publish_data_via_api  # noqa: E402
import run_manager  # noqa: E402
from source_policy import local_state_root  # noqa: E402


class SuccessorError(RuntimeError):
    pass


def default_run_id() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(4)


def github_get(repository: str, path: str, attempts: int = 4) -> dict[str, Any]:
    last_error: subprocess.CalledProcessError | None = None
    for attempt in range(1, attempts + 1):
        try:
            return publish_data_via_api.api(repository, "GET", path)
        except subprocess.CalledProcessError as error:
            last_error = error
            if attempt < attempts:
                time.sleep(attempt * 2)
    assert last_error is not None
    detail = (last_error.stderr or b"").decode("utf-8", errors="replace").strip()
    raise SuccessorError(f"GITHUB_API_READ_FAILED: {detail or path}") from last_error


def github_blob_bytes(repository: str, oid: str) -> bytes:
    payload = github_get(repository, f"/git/blobs/{oid}")
    if payload.get("encoding") != "base64" or not payload.get("content"):
        raise SuccessorError("PRODUCTION_BASE_CONTENT_MISSING")
    return base64.b64decode(str(payload["content"]).encode("ascii"), validate=False)


def load_sources(
    state_root: pathlib.Path,
    run_ids: list[str],
) -> list[tuple[str, pathlib.Path, dict[str, Any]]]:
    sources: list[tuple[str, pathlib.Path, dict[str, Any]]] = []
    source_name: str | None = None
    for run_id in run_ids:
        entry = state_root / "outbox" / run_id
        manifest_path = entry / "manifest.json"
        if not manifest_path.is_file():
            raise SuccessorError(f"SOURCE_OUTBOX_MISSING: {run_id}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        run_manager.validate_manifest(manifest)
        if manifest.get("runId") != run_id:
            raise SuccessorError(f"SOURCE_RUN_ID_MISMATCH: {run_id}")
        current_source = str((manifest.get("source") or {}).get("source") or "")
        if current_source not in {"Chess-Results", "Lichess Broadcasts"}:
            raise SuccessorError(f"SOURCE_RELEASE_UNSUPPORTED: {run_id}")
        if source_name is not None and current_source != source_name:
            raise SuccessorError("MIXED_SOURCE_SUCCESSOR_FORBIDDEN")
        source_name = current_source
        sources.append((run_id, entry, manifest))
    return sources


def select_candidates(
    sources: list[tuple[str, pathlib.Path, dict[str, Any]]],
    dropped_paths: set[str],
    deleted_paths: set[str] | None = None,
) -> dict[str, tuple[str, pathlib.Path, dict[str, Any]]]:
    deleted_paths = deleted_paths or set()
    overlap = dropped_paths & deleted_paths
    if overlap:
        raise SuccessorError(f"PATH_DECISION_AMBIGUOUS: {sorted(overlap)[0]}")
    selected: dict[str, tuple[str, pathlib.Path, dict[str, Any]]] = {}
    seen_paths: set[str] = set()
    for run_id, entry, manifest in sources:
        for item in run_manager.validate_manifest(manifest):
            path = str(item["path"])
            seen_paths.add(path)
            selected[path] = (run_id, entry, item)
    unknown = sorted((dropped_paths | deleted_paths) - seen_paths)
    if unknown:
        raise SuccessorError(f"DECISION_PATH_NOT_IN_SOURCES: {unknown[0]}")
    for path in dropped_paths:
        selected.pop(path, None)
    for path in deleted_paths:
        selected[path] = (
            "human-reviewed-delete",
            pathlib.Path(),
            {"path": path, "operation": "delete", "sha256": None, "bytes": 0},
        )
    return selected


def candidate_content(entry: pathlib.Path, item: dict[str, Any]) -> bytes | None:
    if item["operation"] == "delete":
        return None
    path = str(item["path"])
    source = entry / "files" / path
    if not source.is_file():
        raise SuccessorError(f"SOURCE_OUTBOX_FILE_MISSING: {path}")
    content = source.read_bytes()
    if len(content) != int(item["bytes"]) or hashlib.sha256(content).hexdigest() != item["sha256"]:
        raise SuccessorError(f"SOURCE_OUTBOX_HASH_MISMATCH: {path}")
    return content


def reject_partial_event_candidates(
    selected: dict[str, tuple[str, pathlib.Path, dict[str, Any]]],
) -> None:
    prefix = "data/generated/chess-results-event-details/"
    for path, (_run_id, entry, item) in selected.items():
        if not path.startswith(prefix) or item["operation"] != "upsert":
            continue
        try:
            payload = json.loads(candidate_content(entry, item) or b"{}")
        except json.JSONDecodeError as error:
            raise SuccessorError(f"EVENT_DETAIL_JSON_INVALID: {path}") from error
        if str(payload.get("captureStatus") or "complete") != "complete":
            raise SuccessorError(f"PARTIAL_EVENT_CANDIDATE_FORBIDDEN: {path}")


def build_manifest(
    *,
    run_id: str,
    sources: list[tuple[str, pathlib.Path, dict[str, Any]]],
    selected: dict[str, tuple[str, pathlib.Path, dict[str, Any]]],
    dropped_paths: set[str],
    deleted_paths: set[str],
    production_commit: str,
    production_oids: dict[str, str],
    production_contents: dict[str, bytes],
) -> tuple[dict[str, Any], dict[str, bytes]]:
    files: list[dict[str, Any]] = []
    contents: dict[str, bytes] = {}
    for path in sorted(selected):
        source_run_id, entry, source_item = selected[path]
        content = candidate_content(entry, source_item)
        current_oid = production_oids.get(path)
        current_content = production_contents.get(path)
        if current_oid and current_content is None:
            raise SuccessorError(f"PRODUCTION_BASE_CONTENT_MISSING: {path}")
        current_sha = hashlib.sha256(current_content).hexdigest() if current_content is not None else None
        item = {
            "path": path,
            "operation": source_item["operation"],
            "sha256": source_item.get("sha256"),
            "bytes": int(source_item.get("bytes") or 0),
            "baseBlobOid": current_oid,
            "baseSha256": current_sha,
            "sourceRunId": source_run_id,
        }
        files.append(item)
        if content is not None:
            contents[path] = content
    manifest = {
        "schemaVersion": 1,
        "runId": run_id,
        "command": "conflict-successor",
        "baseCommit": production_commit,
        "createdAt": run_manager.now(),
        "source": dict(sources[-1][2].get("source") or {}),
        "resolution": {
            "kind": "human-reviewed-conflict-successor",
            "sourceRunIds": [item[0] for item in sources],
            "droppedPaths": sorted(dropped_paths),
            "deletedPaths": sorted(deleted_paths),
            "pathDecision": "explicit-per-path",
        },
        "files": files,
    }
    run_manager.validate_manifest(manifest)
    return manifest, contents


def write_bundle(
    state_root: pathlib.Path,
    manifest: dict[str, Any],
    contents: dict[str, bytes],
) -> pathlib.Path:
    run_id = str(manifest["runId"])
    entry = state_root / "outbox" / run_id
    if entry.exists():
        raise SuccessorError(f"SUCCESSOR_ALREADY_EXISTS: {run_id}")
    files_root = entry / "files"
    files_root.mkdir(parents=True)
    os.chmod(entry, 0o700)
    for path, content in contents.items():
        target = files_root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        temporary.write_bytes(content)
        os.replace(temporary, target)
        os.chmod(target, 0o600)
    run_manager.atomic_json(entry / "manifest.json", manifest)
    run_manager.atomic_json(entry / "result.json", {
        "schemaVersion": 1,
        "runId": run_id,
        "result": "conflict-successor-prepared",
        "resolution": manifest["resolution"],
        "files": len(manifest["files"]),
        "bytes": sum(int(item.get("bytes") or 0) for item in manifest["files"]),
    })
    run_manager.atomic_json(entry / "delivery.json", {
        "schemaVersion": 1,
        "runId": run_id,
        "commit": manifest["baseCommit"],
        "status": "pending",
        "attempts": 0,
        "route": None,
        "remoteSHA": None,
        "lastError": None,
        "createdAt": run_manager.now(),
        "updatedAt": run_manager.now(),
    })
    return entry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", action="append", required=True)
    parser.add_argument("--drop-path", action="append", default=[])
    parser.add_argument("--delete-path", action="append", default=[])
    parser.add_argument("--run-id", default=default_run_id())
    parser.add_argument("--state-root", type=pathlib.Path, default=local_state_root())
    parser.add_argument("--prepare", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{8}", args.run_id):
        parser.error("--run-id must use YYYYMMDD-HHMMSS-xxxxxxxx")
    try:
        state_root = args.state_root.resolve()
        sources = load_sources(state_root, args.source_run)
        dropped_paths = {str(path) for path in args.drop_path}
        deleted_paths = {str(path) for path in args.delete_path}
        selected = select_candidates(sources, dropped_paths, deleted_paths)
        reject_partial_event_candidates(selected)

        repository = publish_data_via_api.repository_name()
        production_commit = github_get(repository, "/git/ref/heads/main")["object"]["sha"]
        tree_sha = github_get(repository, f"/git/commits/{production_commit}")["tree"]["sha"]
        tree_payload = github_get(repository, f"/git/trees/{tree_sha}?recursive=1")
        if tree_payload.get("truncated"):
            raise SuccessorError("API_DELIVERY_TREE_TRUNCATED")
        production_oids = {
            str(item["path"]): str(item["sha"])
            for item in (tree_payload.get("tree") or [])
            if item.get("type") == "blob" and item.get("path") and item.get("sha")
        }
        production_contents: dict[str, bytes] = {}
        for path in selected:
            oid = production_oids.get(path)
            if not oid:
                continue
            source_item = selected[path][2]
            content = candidate_content(selected[path][1], source_item)
            if content is not None and publish_data_via_api.git_blob_oid_for_bytes(content) == oid:
                production_contents[path] = content
            else:
                production_contents[path] = github_blob_bytes(repository, oid)

        manifest, contents = build_manifest(
            run_id=args.run_id,
            sources=sources,
            selected=selected,
            dropped_paths=dropped_paths,
            deleted_paths=deleted_paths,
            production_commit=str(production_commit),
            production_oids=production_oids,
            production_contents=production_contents,
        )
        summary = {
            "runId": args.run_id,
            "sourceRunIds": args.source_run,
            "droppedPaths": sorted(dropped_paths),
            "deletedPaths": sorted(deleted_paths),
            "baseCommit": production_commit,
            "files": len(manifest["files"]),
            "bytes": sum(int(item.get("bytes") or 0) for item in manifest["files"]),
            "prepare": bool(args.prepare),
        }
        if args.prepare:
            summary["outbox"] = str(write_bundle(state_root, manifest, contents))
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except (
        SuccessorError,
        run_manager.RunManagerError,
        OSError,
        json.JSONDecodeError,
    ) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
