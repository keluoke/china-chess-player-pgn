#!/usr/bin/env python3
"""Prepare, deliver and reconcile an exact Git snapshot into shadow ingest.

The snapshot must be a clean checkout at the requested commit.  Packages live
under the external collector state directory, never in either Git workspace.
No chess source is contacted and no Git ref is mutated.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import urllib.parse
from typing import Any

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent))

import cloudflare_ingest  # noqa: E402
import run_manager  # noqa: E402
from source_policy import local_state_root  # noqa: E402


MAX_FILES = cloudflare_ingest.MAX_RELEASE_FILES
MAX_BYTES = cloudflare_ingest.MAX_RELEASE_BYTES
TERMINAL = cloudflare_ingest.TERMINAL


class BaselineError(RuntimeError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def hash_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def git(repo: pathlib.Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise BaselineError(completed.stderr.strip() or f"git {' '.join(args)} failed")
    return completed.stdout.strip()


def git_bytes(repo: pathlib.Path, *args: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise BaselineError(completed.stderr.decode(errors="replace").strip() or f"git {' '.join(args)} failed")
    return completed.stdout


def source_for_path(path: str) -> tuple[str, dict[str, str]]:
    if run_manager.within(path, [
        "docs/data/registry",
        "data/generated/federation-snapshots",
        "data/generated/transfer-candidates.json",
    ]):
        return "fide", {"source": "FIDE Rating List", "releasePolicy": "factual-registry-projection"}
    if run_manager.within(path, ["docs/data/bulk"]):
        return "lichess", {
            "source": "Lichess Broadcasts",
            "releasePolicy": "cc-by-sa-4.0",
            "licenseURL": "https://creativecommons.org/licenses/by-sa/4.0/",
            "attributionURL": "https://database.lichess.org/#broadcasts",
        }
    if run_manager.within(path, list(run_manager.CHESS_RESULTS_RELEASE_PREFIXES)):
        return "chess-results", {"source": "Chess-Results", "releasePolicy": "full-data"}
    raise BaselineError(f"RELEASE_SOURCE_PATH_MISMATCH: {path}")


def tree_entries(snapshot: pathlib.Path, target_commit: str) -> list[dict[str, Any]]:
    head = git(snapshot, "rev-parse", "HEAD")
    target = git(snapshot, "rev-parse", f"{target_commit}^{{commit}}")
    if head != target:
        raise BaselineError(f"SNAPSHOT_COMMIT_MISMATCH: HEAD={head} target={target}")
    if git(snapshot, "status", "--porcelain", "--untracked-files=no"):
        raise BaselineError("SNAPSHOT_WORKTREE_DIRTY")
    raw = subprocess.run(
        ["git", "-C", str(snapshot), "ls-tree", "-r", "-z", target],
        check=True,
        capture_output=True,
    ).stdout
    entries: list[dict[str, Any]] = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        _, kind, oid = metadata.decode().split()
        path = raw_path.decode()
        if kind != "blob" or not run_manager.within(path, list(run_manager.PUBLIC_RELEASE_PREFIXES)):
            continue
        source_key, source = source_for_path(path)
        candidate = snapshot / path
        if not candidate.is_file():
            raise BaselineError(f"SNAPSHOT_FILE_MISSING: {path}")
        size = candidate.stat().st_size
        if size > MAX_BYTES or size > cloudflare_ingest.MAX_FILE_BYTES:
            raise BaselineError(f"FREE_TIER_RELEASE_OBJECT_LIMIT: {path} ({size})")
        entries.append({
            "path": path,
            "oid": oid,
            "sha256": hash_file(candidate),
            "bytes": size,
            "sourceKey": source_key,
            "source": source,
        })
    return sorted(entries, key=lambda item: item["path"])


def pack_entries(entries: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    packages: list[list[dict[str, Any]]] = []
    for source_key in sorted({item["sourceKey"] for item in entries}):
        bins: list[dict[str, Any]] = []
        grouped = sorted(
            (item for item in entries if item["sourceKey"] == source_key),
            key=lambda item: (-item["bytes"], item["path"]),
        )
        for item in grouped:
            target = next((
                bucket for bucket in bins
                if len(bucket["items"]) < MAX_FILES and bucket["bytes"] + item["bytes"] <= MAX_BYTES
            ), None)
            if target is None:
                target = {"items": [], "bytes": 0}
                bins.append(target)
            target["items"].append(item)
            target["bytes"] += item["bytes"]
        packages.extend(sorted(
            (sorted(bucket["items"], key=lambda item: item["path"]) for bucket in bins),
            key=lambda group: group[0]["path"],
        ))
    return packages


def run_id(created_at: dt.datetime, seed: str, index: int) -> str:
    stamp = (created_at + dt.timedelta(seconds=index)).strftime("%Y%m%d-%H%M%S")
    suffix = hashlib.sha256(f"{seed}:{index}".encode()).hexdigest()[:8]
    return f"{stamp}-{suffix}"


def write_package(
    migration_dir: pathlib.Path,
    snapshot: pathlib.Path,
    target_commit: str,
    migration_id: str,
    root_sha256: str,
    items: list[dict[str, Any]],
    package_index: int,
    package_count: int,
    created_at: dt.datetime,
) -> dict[str, Any]:
    package_run_id = run_id(created_at, migration_id, package_index)
    bundle = migration_dir / "outbox" / package_run_id
    files_root = bundle / "files"
    files: list[dict[str, Any]] = []
    for item in items:
        destination = files_root / item["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(snapshot / item["path"], destination)
        files.append({
            "path": item["path"],
            "operation": "upsert",
            "sha256": item["sha256"],
            "bytes": item["bytes"],
            "baseBlobOid": item["oid"],
            "baseSha256": item["sha256"],
        })
    manifest = {
        "schemaVersion": 1,
        "runId": package_run_id,
        "command": "baseline-migrate",
        "baseCommit": target_commit,
        "source": items[0]["source"],
        "migration": {
            "migrationId": migration_id,
            "rootSha256": root_sha256,
            "packageIndex": package_index,
            "packageCount": package_count,
        },
        "files": files,
    }
    run_manager.validate_manifest(manifest)
    run_manager.atomic_json(bundle / "manifest.json", manifest)
    return {
        "runId": package_run_id,
        "sourceKey": items[0]["sourceKey"],
        "files": len(items),
        "bytes": sum(item["bytes"] for item in items),
        "status": "pending",
    }


def prepare(snapshot: pathlib.Path, target_commit: str, output_root: pathlib.Path, migration_id: str | None) -> pathlib.Path:
    target = git(snapshot, "rev-parse", f"{target_commit}^{{commit}}")
    migration_id = migration_id or f"baseline-{target[:12]}"
    migration_dir = output_root / "baseline-migrations" / migration_id
    if migration_dir.exists():
        raise BaselineError(f"MIGRATION_ALREADY_EXISTS: {migration_dir}")
    entries = tree_entries(snapshot, target)
    root_rows = [[item["path"], item["oid"], item["sha256"], item["bytes"], item["sourceKey"]] for item in entries]
    root_sha256 = hashlib.sha256(canonical_bytes(root_rows)).hexdigest()
    groups = pack_entries(entries)
    created_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    packages = [
        write_package(
            migration_dir,
            snapshot,
            target,
            migration_id,
            root_sha256,
            group,
            index,
            len(groups),
            created_at,
        )
        for index, group in enumerate(groups)
    ]
    root = {
        "schemaVersion": 1,
        "migrationId": migration_id,
        "status": "prepared",
        "targetCommit": target,
        "rootSha256": root_sha256,
        "createdAt": created_at.isoformat(),
        "expectedFiles": len(entries),
        "expectedBytes": sum(item["bytes"] for item in entries),
        "entries": root_rows,
        "cleanupPackages": [],
        "packages": packages,
    }
    run_manager.atomic_json(migration_dir / "migration.json", root)
    return migration_dir


def git_tree_oids(repo: pathlib.Path, commit: str) -> dict[str, str]:
    raw = git_bytes(repo, "ls-tree", "-r", "-z", commit)
    result: dict[str, str] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        _, kind, oid = metadata.decode().split()
        path = raw_path.decode()
        if kind == "blob" and run_manager.within(path, list(run_manager.PUBLIC_RELEASE_PREFIXES)):
            result[path] = oid
    return result


def changed_paths(repo: pathlib.Path, base_commit: str, target_commit: str) -> list[tuple[str, str]]:
    raw = git_bytes(
        repo,
        "diff",
        "--name-status",
        "-z",
        "--no-renames",
        f"{base_commit}..{target_commit}",
        "--",
        *run_manager.PUBLIC_RELEASE_PREFIXES,
    )
    fields = raw.split(b"\0")
    if fields and not fields[-1]:
        fields.pop()
    if len(fields) % 2:
        raise BaselineError("CATCHUP_DIFF_INVALID")
    changes: list[tuple[str, str]] = []
    for index in range(0, len(fields), 2):
        status = fields[index].decode()
        path = fields[index + 1].decode()
        if status not in {"A", "M", "D"}:
            raise BaselineError(f"CATCHUP_DIFF_STATUS_UNSUPPORTED: {status} {path}")
        source_for_path(path)
        changes.append((status, path))
    return changes


def write_catchup_package(
    migration_dir: pathlib.Path,
    base_commit: str,
    migration_id: str,
    root_sha256: str,
    items: list[dict[str, Any]],
    package_index: int,
    package_count: int,
    created_at: dt.datetime,
) -> dict[str, Any]:
    package_run_id = run_id(created_at, migration_id, package_index)
    bundle = migration_dir / "outbox" / package_run_id
    files_root = bundle / "files"
    manifest_files: list[dict[str, Any]] = []
    for item in items:
        operation = item["operation"]
        if operation == "upsert":
            destination = files_root / item["path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(item["content"])
        manifest_files.append({
            "path": item["path"],
            "operation": operation,
            "sha256": item.get("sha256"),
            "bytes": item["bytes"],
            "baseBlobOid": item.get("baseOid"),
            "baseSha256": item.get("baseSha256"),
        })
    if not files_root.exists():
        files_root.mkdir(parents=True)
    manifest = {
        "schemaVersion": 1,
        "runId": package_run_id,
        "command": "baseline-catchup",
        "baseCommit": base_commit,
        "source": items[0]["source"],
        "migration": {
            "migrationId": migration_id,
            "rootSha256": root_sha256,
            "packageIndex": package_index,
            "packageCount": package_count,
        },
        "files": manifest_files,
    }
    run_manager.validate_manifest(manifest)
    run_manager.atomic_json(bundle / "manifest.json", manifest)
    return {
        "runId": package_run_id,
        "sourceKey": items[0]["sourceKey"],
        "files": len(items),
        "bytes": sum(item["bytes"] for item in items),
        "status": "pending",
    }


def prepare_catchup(
    repo: pathlib.Path,
    baseline_migration_dir: pathlib.Path,
    target_commit: str,
    migration_id: str | None,
) -> pathlib.Path:
    baseline = read_root(baseline_migration_dir)
    if baseline.get("status") not in {"delivered", "reconciled"}:
        raise BaselineError("CATCHUP_BASELINE_NOT_DELIVERED")
    if any(item.get("status") != "complete" for item in baseline.get("packages") or []):
        raise BaselineError("CATCHUP_BASELINE_INCOMPLETE")
    base_commit = git(repo, "rev-parse", f"{baseline['targetCommit']}^{{commit}}")
    target = git(repo, "rev-parse", f"{target_commit}^{{commit}}")
    if subprocess.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", base_commit, target],
        check=False,
    ).returncode != 0:
        raise BaselineError("CATCHUP_TARGET_NOT_DESCENDANT")
    base_rows = baseline.get("entries") or []
    entries = {
        str(row[0]): {
            "path": str(row[0]),
            "oid": str(row[1]),
            "sha256": str(row[2]),
            "bytes": int(row[3]),
            "sourceKey": str(row[4]),
        }
        for row in base_rows
    }
    if {path: item["oid"] for path, item in entries.items()} != git_tree_oids(repo, base_commit):
        raise BaselineError("CATCHUP_BASELINE_ROOT_GIT_MISMATCH")
    changes: list[dict[str, Any]] = []
    for status, path in changed_paths(repo, base_commit, target):
        base = entries.get(path)
        source_key, source = source_for_path(path)
        if status in {"M", "D"} and base is None:
            raise BaselineError(f"CATCHUP_BASE_PATH_MISSING: {path}")
        if status == "D":
            changes.append({
                "path": path,
                "operation": "delete",
                "bytes": 0,
                "baseOid": base["oid"],
                "baseSha256": base["sha256"],
                "sourceKey": source_key,
                "source": source,
            })
            del entries[path]
            continue
        oid = git(repo, "rev-parse", f"{target}:{path}")
        content = git_bytes(repo, "cat-file", "blob", oid)
        size = len(content)
        if size > MAX_BYTES or size > cloudflare_ingest.MAX_FILE_BYTES:
            raise BaselineError(f"FREE_TIER_RELEASE_OBJECT_LIMIT: {path} ({size})")
        sha256 = hashlib.sha256(content).hexdigest()
        changes.append({
            "path": path,
            "operation": "upsert",
            "oid": oid,
            "sha256": sha256,
            "bytes": size,
            "content": content,
            "baseOid": base["oid"] if base else None,
            "baseSha256": base["sha256"] if base else None,
            "sourceKey": source_key,
            "source": source,
        })
        entries[path] = {
            "path": path,
            "oid": oid,
            "sha256": sha256,
            "bytes": size,
            "sourceKey": source_key,
        }
    target_tree = git_tree_oids(repo, target)
    if {path: item["oid"] for path, item in entries.items()} != target_tree:
        raise BaselineError("CATCHUP_TARGET_ROOT_GIT_MISMATCH")
    migration_id = migration_id or f"catchup-{target[:12]}"
    migration_dir = baseline_migration_dir.parent / migration_id
    if migration_dir.exists():
        raise BaselineError(f"MIGRATION_ALREADY_EXISTS: {migration_dir}")
    root_rows = [
        [item["path"], item["oid"], item["sha256"], item["bytes"], item["sourceKey"]]
        for item in sorted(entries.values(), key=lambda item: item["path"])
    ]
    root_sha256 = hashlib.sha256(canonical_bytes(root_rows)).hexdigest()
    groups = pack_entries(changes) if changes else []
    created_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    packages = [
        write_catchup_package(
            migration_dir,
            base_commit,
            migration_id,
            root_sha256,
            group,
            index,
            len(groups),
            created_at,
        )
        for index, group in enumerate(groups)
    ]
    root = {
        "schemaVersion": 1,
        "migrationId": migration_id,
        "kind": "baseline-catchup",
        "status": "prepared" if packages else "delivered",
        "baseMigrationId": baseline["migrationId"],
        "baseCommit": base_commit,
        "targetCommit": target,
        "rootSha256": root_sha256,
        "createdAt": created_at.isoformat(),
        "expectedFiles": len(root_rows),
        "expectedBytes": sum(row[3] for row in root_rows),
        "entries": root_rows,
        "changes": [{
            "path": item["path"],
            "operation": item["operation"],
            "baseSha256": item.get("baseSha256"),
            "sha256": item.get("sha256"),
            "bytes": item["bytes"],
        } for item in changes],
        "cleanupPackages": [],
        "packages": packages,
    }
    run_manager.atomic_json(migration_dir / "migration.json", root)
    return migration_dir


def read_root(migration_dir: pathlib.Path) -> dict[str, Any]:
    path = migration_dir / "migration.json"
    if not path.is_file():
        raise BaselineError(f"MIGRATION_NOT_FOUND: {migration_dir}")
    return json.loads(path.read_text(encoding="utf-8"))


def fetch_heads(endpoint: str, secret: str) -> dict[str, dict[str, Any]]:
    heads: dict[str, dict[str, Any]] = {}
    after = ""
    while True:
        query = urllib.parse.urlencode({"after": after, "limit": 200})
        page = cloudflare_ingest.request_json(endpoint, "GET", f"/v1/heads?{query}", secret)
        for item in page.get("heads") or []:
            heads[str(item["path"])] = item
        after = str(page.get("nextAfter") or "")
        if not after:
            return heads


def prepare_cleanup(migration_dir: pathlib.Path, endpoint: str, secret: str) -> list[dict[str, Any]]:
    root = read_root(migration_dir)
    expected = {row[0] for row in root["entries"]}
    heads = fetch_heads(endpoint, secret)
    extras = [item for path, item in heads.items() if path not in expected and not int(item.get("deleted") or 0)]
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in extras:
        source_key, source = source_for_path(str(item["path"]))
        item = {**item, "sourceKey": source_key, "source": source}
        groups.setdefault(source_key, []).append(item)
    created_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    cleanup: list[dict[str, Any]] = []
    offset = 10000
    for group_index, source_key in enumerate(sorted(groups)):
        items = sorted(groups[source_key], key=lambda item: item["path"])
        for start in range(0, len(items), MAX_FILES):
            chunk = items[start:start + MAX_FILES]
            package_index = offset + group_index * 100 + start // MAX_FILES
            package_run_id = run_id(created_at, root["migrationId"], package_index)
            bundle = migration_dir / "outbox" / package_run_id
            manifest = {
                "schemaVersion": 1,
                "runId": package_run_id,
                "command": "baseline-compensating-delete",
                "baseCommit": root["targetCommit"],
                "source": chunk[0]["source"],
                "migration": {"migrationId": root["migrationId"], "rootSha256": root["rootSha256"]},
                "files": [{
                    "path": item["path"],
                    "operation": "delete",
                    "sha256": None,
                    "bytes": 0,
                    "shadowBaseSha256": item["sha256"],
                } for item in chunk],
            }
            run_manager.validate_manifest(manifest)
            (bundle / "files").mkdir(parents=True, exist_ok=True)
            run_manager.atomic_json(bundle / "manifest.json", manifest)
            cleanup.append({"runId": package_run_id, "sourceKey": source_key, "files": len(chunk), "bytes": 0, "status": "pending"})
    root["cleanupPackages"] = cleanup
    root["status"] = "cleanup-prepared"
    run_manager.atomic_json(migration_dir / "migration.json", root)
    return cleanup


def deliver(migration_dir: pathlib.Path, endpoint: str, secret: str, max_packages: int, wait_seconds: int) -> dict[str, Any]:
    root = read_root(migration_dir)
    delivered = 0
    for collection in (root.get("cleanupPackages") or [], root.get("packages") or []):
        for package in collection:
            state_path = migration_dir / "outbox" / package["runId"] / "shadow-delivery.json"
            if state_path.is_file():
                state = json.loads(state_path.read_text(encoding="utf-8"))
                if state.get("status") == "complete":
                    package["status"] = "complete"
                    continue
            if delivered >= max_packages:
                continue
            result = cloudflare_ingest.deliver(
                package["runId"], endpoint, secret, migration_dir, wait_seconds,
            )
            package["status"] = str(result.get("status") or "unknown")
            delivered += 1
            run_manager.atomic_json(migration_dir / "migration.json", root)
            if package["status"] not in TERMINAL or package["status"] != "complete":
                root["status"] = "attention"
                run_manager.atomic_json(migration_dir / "migration.json", root)
                return root
    pending = [item for key in ("cleanupPackages", "packages") for item in root.get(key, []) if item.get("status") != "complete"]
    root["status"] = "delivered" if not pending else "delivery-pending"
    run_manager.atomic_json(migration_dir / "migration.json", root)
    return root


def reconcile(migration_dir: pathlib.Path, endpoint: str, secret: str) -> dict[str, Any]:
    root = read_root(migration_dir)
    expected = {row[0]: row[2] for row in root["entries"]}
    heads = fetch_heads(endpoint, secret)
    active = {path: item.get("sha256") for path, item in heads.items() if not int(item.get("deleted") or 0)}
    missing = sorted(path for path in expected if path not in active)
    extra = sorted(path for path in active if path not in expected)
    mismatched = sorted(path for path in expected.keys() & active.keys() if expected[path] != active[path])
    result = {
        "schemaVersion": 1,
        "migrationId": root["migrationId"],
        "targetCommit": root["targetCommit"],
        "expectedFiles": len(expected),
        "activeHeads": len(active),
        "missing": missing,
        "extra": extra,
        "mismatched": mismatched,
        "deletedHeads": sum(1 for item in heads.values() if int(item.get("deleted") or 0)),
        "consistent": not missing and not extra and not mismatched,
        "checkedAt": run_manager.now(),
    }
    run_manager.atomic_json(migration_dir / "reconciliation.json", result)
    root["status"] = "reconciled" if result["consistent"] else "reconciliation-failed"
    root["reconciliation"] = {key: value for key, value in result.items() if key not in {"missing", "extra", "mismatched"}}
    run_manager.atomic_json(migration_dir / "migration.json", root)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--snapshot-root", type=pathlib.Path, required=True)
    prepare_parser.add_argument("--target-commit", required=True)
    prepare_parser.add_argument("--migration-id")
    prepare_parser.add_argument("--output-root", type=pathlib.Path, default=local_state_root())
    catchup_parser = subparsers.add_parser("prepare-catchup")
    catchup_parser.add_argument("--repo-root", type=pathlib.Path, required=True)
    catchup_parser.add_argument("--baseline-migration-dir", type=pathlib.Path, required=True)
    catchup_parser.add_argument("--target-commit", required=True)
    catchup_parser.add_argument("--migration-id")
    for name in ("prepare-cleanup", "deliver", "reconcile"):
        child = subparsers.add_parser(name)
        child.add_argument("--migration-dir", type=pathlib.Path, required=True)
        child.add_argument("--endpoint", default=cloudflare_ingest.DEFAULT_ENDPOINT)
    deliver_parser = subparsers.choices["deliver"]
    deliver_parser.add_argument("--max-packages", type=int, default=8)
    deliver_parser.add_argument("--wait-seconds", type=int, default=300)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result: Any = {"migrationDir": str(prepare(args.snapshot_root.resolve(), args.target_commit, args.output_root.resolve(), args.migration_id))}
        elif args.command == "prepare-catchup":
            result = {"migrationDir": str(prepare_catchup(
                args.repo_root.resolve(),
                args.baseline_migration_dir.resolve(),
                args.target_commit,
                args.migration_id,
            ))}
        else:
            secret = cloudflare_ingest.ingest_secret()
            if not secret:
                raise BaselineError("CLOUDFLARE_INGEST_HMAC_SECRET is required")
            if args.command == "prepare-cleanup":
                result = prepare_cleanup(args.migration_dir.resolve(), args.endpoint, secret)
            elif args.command == "deliver":
                if args.max_packages < 1 or args.max_packages > 8:
                    raise BaselineError("FREE_TIER_DAILY_RELEASE_LIMIT")
                result = deliver(args.migration_dir.resolve(), args.endpoint, secret, args.max_packages, args.wait_seconds)
            else:
                result = reconcile(args.migration_dir.resolve(), args.endpoint, secret)
    except (BaselineError, cloudflare_ingest.ShadowDeliveryError, run_manager.RunManagerError, OSError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
