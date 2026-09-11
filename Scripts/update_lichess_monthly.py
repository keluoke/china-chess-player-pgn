#!/usr/bin/env python3
"""Prepare a verified Lichess release; publish that artifact without source access."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "docs/data/bulk/"
RECEIPT = PREFIX + "lichess-broadcast/update-receipt.json"


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def allowed(path: str) -> bool:
    parts = Path(path).parts
    return (".." not in parts and not Path(path).is_absolute() and (
        path in {PREFIX + "manifest.json", PREFIX + "lichess-broadcast/manifest.json", RECEIPT}
        or path.startswith(PREFIX + "youth/") and path.endswith((".json", ".pgn"))
        or path.startswith(PREFIX + "lichess-events/") and path.endswith((".json", ".pgn"))))


def check_months(shards, previous: dict, today: dt.date) -> None:
    months = {s.month for s in shards}
    expected = (today.replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m")
    if expected not in months:
        raise RuntimeError(f"LICHESS_MONTH_NOT_PUBLISHED: expected {expected}")
    old = {s["month"] for s in previous.get("shards", [])}
    if not old <= months:
        raise RuntimeError(f"LICHESS_CATALOG_REGRESSION: missing {sorted(old - months)}")
    cursor = dt.date.fromisoformat(min(months) + "-01")
    while cursor.strftime("%Y-%m") <= expected:
        if cursor.strftime("%Y-%m") not in months:
            raise RuntimeError(f"LICHESS_MONTH_GAP: {cursor:%Y-%m}")
        cursor = (cursor.replace(day=28) + dt.timedelta(days=4)).replace(day=1)


def stream_object(client, bucket: str, key: str, destination: Path | None = None) -> tuple[str, int]:
    response = client.get_object(Bucket=bucket, Key=key)
    body = response["Body"]
    h, size = hashlib.sha256(), 0
    try:
        with (destination.open("wb") if destination else open(os.devnull, "wb")) as output:
            for chunk in iter(lambda: body.read(1024 * 1024), b""):
                output.write(chunk)
                h.update(chunk)
                size += len(chunk)
    finally:
        body.close()
    if size != response["ContentLength"]:
        raise RuntimeError(f"R2_BODY_TRUNCATED: {key}")
    return h.hexdigest(), size


def collect(release: Path) -> None:
    import boto3
    import sync_lichess_broadcast_bulk as bulk
    from botocore.exceptions import ClientError

    if release.exists():
        raise RuntimeError("RELEASE_OUTPUT_EXISTS: use a fresh artifact directory")
    if os.environ.get("R2_BUCKET") != "chess-data":
        raise RuntimeError("R2_BUCKET_INVALID: expected chess-data")
    client = boto3.client("s3", endpoint_url=os.environ["R2_ENDPOINT"],
                          aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
                          aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"], region_name="auto")
    base = git("rev-parse", "HEAD")
    previous_path = ROOT / (PREFIX + "lichess-broadcast/manifest.json")
    previous = json.loads(previous_path.read_text())
    previous_rows = {row["month"]: row for row in previous["shards"]}
    shards, meta = bulk.fetch_broadcast_metadata()
    today = dt.datetime.now(dt.timezone.utc).date()
    check_months(shards, previous, today)
    shards = [s for s in shards if s.month < today.strftime("%Y-%m")]
    objects = []
    # The runner's scratch tree never enters Git or the release artifact.
    with tempfile.TemporaryDirectory(prefix="lichess-monthly-") as temporary:
        stage = Path(temporary)
        bulk.DOCS_DATA = stage
        bulk.BULK_ROOT = stage / "bulk"
        bulk.LICHESS_ROOT = stage / "bulk/lichess-broadcast"
        bulk.SHARD_ROOT = bulk.LICHESS_ROOT / "shards"
        bulk.EXISTING_SHARD_ROOT = bulk.SHARD_ROOT
        bulk.YOUTH_ROOT = stage / "bulk/youth"
        bulk.LICHESS_EVENT_ROOT = stage / "bulk/lichess-events"
        bulk.SHARD_ROOT.mkdir(parents=True)
        for shard in shards:
            target = shard.output_shard_path
            key = "bulk/lichess-broadcast/shards/" + shard.file_name
            old = previous_rows.get(shard.month, {})
            remote = None
            try:
                remote = client.head_object(Bucket="chess-data", Key=key)
            except ClientError as error:
                if error.response["Error"]["Code"] not in {"404", "NoSuchKey", "NotFound"}:
                    raise
            if remote is not None:
                sha, size = stream_object(client, "chess-data", key, target)
                expected = old.get("sha256") or remote.get("Metadata", {}).get("sha256")
                if not expected or sha != expected:
                    raise RuntimeError(f"R2_IMMUTABLE_HASH_MISMATCH: {key}")
            else:
                bulk.mirror_shards([shard], False, 0.2, False)
                sha, size = digest(target), target.stat().st_size
                if old.get("sha256") and old["sha256"] != sha:
                    raise RuntimeError(f"LICHESS_IMMUTABLE_HASH_CHANGED: {key}")
            bulk.validate_local_shard(target, shard.size_bytes)
            # zstd -t checks complete frames, including truncated final frames.
            subprocess.run(["zstd", "-q", "-t", str(target)], check=True)
            count = sum(1 for _ in bulk.iter_zst_pgn_games(target))
            if count != shard.games:
                raise RuntimeError(f"LICHESS_GAME_COUNT_MISMATCH: {shard.month}: {count}/{shard.games}")
            objects.append({"key": key, "sha256": sha, "bytes": size,
                            "games": count, "month": shard.month, "upload": remote is None,
                            "publicURL": f"{bulk.OBJECT_STORAGE_BASE}/{key}"})
            print(f"verified {shard.month}: {count} games, {size} bytes", flush=True)
        bulk.enrich_local_shards(shards)
        bulk.write_bulk_manifest(shards, meta, False)
        if not bulk.REGISTRY_PLAYERS_JSON.is_file() or not bulk.PUBLIC_EVENTS_JSON.is_file():
            raise RuntimeError("LICHESS_PROJECTION_INPUT_MISSING")
        bulk.build_youth_index(shards, False)
        bulk.build_target_event_archives(shards, False)
        # Only verified immutable monthly objects are created, never overwritten.
        for row in objects:
            if row.pop("upload"):
                local = bulk.SHARD_ROOT / Path(row["key"]).name
                with local.open("rb") as body:
                    client.put_object(Bucket="chess-data", Key=row["key"], Body=body,
                                      IfNoneMatch="*", Metadata={"sha256": row["sha256"]},
                                      ContentType="application/zstd", CacheControl="public,max-age=31536000,immutable")
                sha, size = stream_object(client, "chess-data", row["key"])
                if (sha, size) != (row["sha256"], row["bytes"]):
                    raise RuntimeError(f"R2_BODY_HASH_MISMATCH: {row['key']}")
        receipt = {"schemaVersion": 1, "inputCommit": base,
                   "runId": os.environ.get("GITHUB_RUN_ID", "local"),
                   "verifiedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
                   "source": meta, "latestMonth": max(s.month for s in shards),
                   "objects": objects}
        (bulk.LICHESS_ROOT / "update-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        release.mkdir(parents=True)
        files = []
        for source in sorted(stage.rglob("*")):
            if not source.is_file():
                continue
            path = "docs/data/" + source.relative_to(stage).as_posix()
            if not allowed(path):
                continue
            destination = release / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            old_path = ROOT / path
            files.append({"path": path, "sha256": digest(source), "bytes": source.stat().st_size,
                          "baseSha256": digest(old_path) if old_path.is_file() else None, "operation": "upsert"})
        included = {row["path"] for row in files}
        for subtree in ("youth", "lichess-events"):
            for old_path in sorted((ROOT / PREFIX / subtree).rglob("*")):
                path = old_path.relative_to(ROOT).as_posix()
                if old_path.is_file() and allowed(path) and path not in included:
                    files.append({"path": path, "operation": "delete", "baseSha256": digest(old_path)})
        (release / "release.json").write_text(json.dumps({"schemaVersion": 1, "inputCommit": base,
                                                        "files": files}, indent=2) + "\n")
        print(json.dumps({"release": str(release), "shards": len(shards),
                          "games": sum(s.games for s in shards), "files": len(files)}))


def validate_release(release: Path, base: str) -> list[dict]:
    manifest = json.loads((release / "release.json").read_text())
    if manifest["inputCommit"] != base:
        raise RuntimeError("LICHESS_RELEASE_BASE_MISMATCH")
    rows = manifest["files"]
    paths = [row["path"] for row in rows]
    if not rows or len(paths) != len(set(paths)) or RECEIPT not in paths:
        raise RuntimeError("LICHESS_RELEASE_MANIFEST_INVALID")
    for row in rows:
        path = row["path"]
        source, current = release / path, ROOT / path
        if not allowed(path) or row.get("operation") not in {"upsert", "delete"}:
            raise RuntimeError(f"LICHESS_RELEASE_PATH_INVALID: {path}")
        if row["operation"] == "upsert" and (source.is_symlink() or not source.is_file()):
            raise RuntimeError(f"LICHESS_RELEASE_PATH_INVALID: {path}")
        if row["operation"] == "upsert" and (digest(source), source.stat().st_size) != (row["sha256"], row["bytes"]):
            raise RuntimeError(f"LICHESS_RELEASE_HASH_MISMATCH: {path}")
        if (digest(current) if current.is_file() else None) != row["baseSha256"]:
            raise RuntimeError(f"LICHESS_RELEASE_BASE_CONFLICT: {path}")
    return rows


def publish(release: Path) -> None:
    base = git("rev-parse", "HEAD")
    rows = validate_release(release, base)
    if git("status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("LICHESS_RELEASE_DIRTY_WORKTREE")
    git("fetch", "--depth=1", "origin", "main")
    remote = git("rev-parse", "origin/main")
    if remote != base:
        # Rerunning only the failed publish job must not download sources again.
        identical = True
        for row in rows:
            result = subprocess.run(["git", "show", f"{remote}:{row['path']}"], cwd=ROOT, capture_output=True)
            if row["operation"] == "delete":
                identical = identical and result.returncode != 0
            else:
                identical = identical and result.returncode == 0 and hashlib.sha256(result.stdout).hexdigest() == row["sha256"]
        if not identical:
            raise RuntimeError("LICHESS_RELEASE_REMOTE_MOVED: preserve artifact; rebuild from current main")
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write(f"target_sha={remote}\n")
        return
    for row in rows:
        destination = ROOT / row["path"]
        if row["operation"] == "delete":
            destination.unlink(missing_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(release / row["path"], destination)
    subprocess.run(["bash", "Scripts/ci_commit_push.sh", "Update Lichess broadcast monthly archive",
                    *[row["path"] for row in rows]], cwd=ROOT, check=True,
                   env={**os.environ, "CI_COMMIT_REBASE_ON_CONFLICT": "false", "PUSH_BRANCH": "main"})
    sha = git("rev-parse", "HEAD")
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(f"target_sha={sha}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["collect", "publish"])
    parser.add_argument("--release", required=True, type=Path)
    args = parser.parse_args()
    (collect if args.command == "collect" else publish)(args.release.resolve())


if __name__ == "__main__":
    main()
