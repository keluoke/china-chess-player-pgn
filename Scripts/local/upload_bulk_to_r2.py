#!/usr/bin/env python3
"""Upload large immutable assets (bulk PGN shards, event archives) to R2.

Reads S3 credentials from ``.secrets.local`` (never committed). Idempotent:
existing objects with matching size are skipped, so interrupted runs resume.
Content-hash keyed layout is deferred until the object-storage manifest v2;
current keys mirror the repository layout for a 1:1 mapping.

Usage:
  python3 Scripts/local/upload_bulk_to_r2.py [--prefix bulk/lichess-broadcast/shards]
                                             [--max-seconds 40] [--dry-run]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import time
from typing import Any

import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from stable_json import write_json  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
SECRETS = ROOT / ".secrets.local"

SOURCES = {
    "bulk/lichess-broadcast/shards": ROOT / "docs/data/bulk/lichess-broadcast/shards",
    "events/chess-results": ROOT / "data/generated/chess-results-event-pgn",
}
PUBLIC_BASE = "https://data.chessdb.aigclabs.cc"


def load_secrets() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in SECRETS.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", default="bulk/lichess-broadcast/shards")
    parser.add_argument("--max-seconds", type=int, default=0, help="stop cleanly after N seconds (resume later)")
    parser.add_argument("--allow-pending", action="store_true",
                        help="return success when the time budget leaves resumable pending objects")
    parser.add_argument("--verify", action="store_true",
                        help="HEAD every remote object and compare its sha256 metadata against the local file")
    parser.add_argument("--backfill-metadata", action="store_true",
                        help="server-side CopyObject to attach sha256 metadata to size-matching objects "
                             "uploaded before checksums were recorded (no byte re-transfer)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    import boto3
    from botocore.config import Config

    secrets = load_secrets()
    client = boto3.client(
        "s3",
        endpoint_url=secrets["R2_ENDPOINT"],
        aws_access_key_id=secrets["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=secrets["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
        config=Config(retries={"max_attempts": 3}),
    )
    bucket = secrets.get("R2_BUCKET", "chess-data")
    source_root = SOURCES[args.prefix]
    receipt_path = ROOT / "data" / "generated" / "r2-object-receipts" / f"{args.prefix.replace('/', '--')}.json"

    def sha256_file(path: pathlib.Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def get_remote_status(key: str) -> dict[str, Any]:
        try:
            head = client.head_object(Bucket=bucket, Key=key)
            sha = (head.get("Metadata") or {}).get("sha256")
            return {
                "status": "ok",
                "sha256": sha,
                "size": head.get("ContentLength"),
            }
        except client.exceptions.NoSuchKey:
            return {"status": "absent"}
        except Exception as e:
            if "Not Found" in str(e) or "404" in str(e):
                return {"status": "absent"}
            return {"status": "error", "error": str(e)}

    existing: dict[str, int] = {}
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=args.prefix):
        for obj in page.get("Contents", []):
            existing[obj["Key"]] = obj["Size"]

    started = time.time()

    if args.verify:
        # Review §6.1: size equality never certifies content; compare the
        # sha256 stored in object metadata against the local file.
        verified = mismatched = missing_meta = absent = error_count = 0
        problems: list[str] = []
        for path in sorted(source_root.glob("*")):
            if not path.is_file():
                continue
            key = f"{args.prefix}/{path.name}"

            res = get_remote_status(key)
            if res["status"] == "absent":
                absent += 1
                problems.append(f"absent: {key}")
            elif res["status"] == "error":
                error_count += 1
                problems.append(f"network-error: {key} ({res['error']})")
            elif not res["sha256"]:
                missing_meta += 1
                problems.append(f"no-sha-metadata: {key}")
            else:
                local_sha = sha256_file(path)
                if res["sha256"] == local_sha:
                    verified += 1
                else:
                    mismatched += 1
                    problems.append(f"SHA MISMATCH: {key} (local: {local_sha}, remote: {res['sha256']})")

        print(json.dumps({
            "bucket": bucket, "prefix": args.prefix, "verified": verified,
            "mismatched": mismatched, "missingMetadata": missing_meta,
            "absent": absent, "errors": error_count, "problems": problems[:20],
            "seconds": round(time.time() - started, 1),
        }, ensure_ascii=False))
        return 1 if (mismatched or absent or missing_meta or error_count) else 0

    # Confirmation cache: once a remote object's sha256 metadata matched the
    # local file, skip the per-object HEAD on later runs unless the local
    # file changed. The --verify mode always re-checks remotely.
    cache_path = pathlib.Path.home() / ".r2-upload-confirmed.json"
    try:
        confirmed: dict[str, list] = json.loads(cache_path.read_text())
    except (OSError, ValueError):
        confirmed = {}

    if args.backfill_metadata:
        # Missing metadata cannot prove that same-size remote bytes equal the
        # local object. Re-upload the local file and verify it by HEAD; never
        # stamp a guessed local digest onto unverified server-side bytes.
        stamped = already = failed_backfill = 0
        pending = []
        receipts: list[dict[str, Any]] = []
        for path in sorted(source_root.glob("*")):
            if not path.is_file():
                continue
            key = f"{args.prefix}/{path.name}"
            stat = path.stat()
            local_sha = sha256_file(path)
            # Cache stores size, mtime, and local SHA
            fingerprint = [stat.st_size, int(stat.st_mtime), local_sha]

            if confirmed.get(key) == fingerprint:
                already += 1
                receipts.append({"key": key, "sha256": local_sha, "bytes": stat.st_size,
                                 "publicURL": f"{PUBLIC_BASE}/{key}"})
                continue
            if args.max_seconds and time.time() - started > args.max_seconds:
                pending.append(key)
                continue

            res = get_remote_status(key)
            if res["status"] == "ok" and res["sha256"] == local_sha:
                confirmed[key] = fingerprint
                already += 1
                receipts.append({"key": key, "sha256": local_sha, "bytes": stat.st_size,
                                 "publicURL": f"{PUBLIC_BASE}/{key}"})
                continue

            if not args.dry_run:
                try:
                    client.upload_file(
                        str(path), bucket, key,
                        ExtraArgs={"Metadata": {"sha256": local_sha}},
                    )
                    verified = get_remote_status(key)
                    if verified["status"] != "ok" or verified["sha256"] != local_sha:
                        raise RuntimeError("post-upload HEAD checksum mismatch")
                    confirmed[key] = fingerprint
                    receipts.append({"key": key, "sha256": local_sha, "bytes": stat.st_size,
                                     "publicURL": f"{PUBLIC_BASE}/{key}"})
                    stamped += 1
                except Exception as e:
                    failed_backfill += 1
                    print(f"Failed to re-upload object for metadata repair: {key} ({e})")
            else:
                stamped += 1

        try:
            cache_path.write_text(json.dumps(confirmed))
        except OSError:
            pass
        if not args.dry_run:
            write_json(receipt_path, {
                "schemaVersion": 1, "bucket": bucket, "prefix": args.prefix,
                "verifiedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "objects": sorted(receipts, key=lambda row: row["key"]),
            }, ensure_ascii=False, indent=2)
        print(json.dumps({
            "bucket": bucket, "prefix": args.prefix, "metadataStamped": stamped,
            "alreadyStamped": already,
            "failedBackfill": failed_backfill,
            "pending": len(pending), "seconds": round(time.time() - started, 1),
        }, ensure_ascii=False))
        return 1 if failed_backfill > 0 or (pending and not args.allow_pending) else 0

    uploaded = skipped = reupload = failures = 0
    pending = []
    receipts: list[dict[str, Any]] = []
    for path in sorted(source_root.glob("*")):
        if not path.is_file():
            continue
        key = f"{args.prefix}/{path.name}"
        stat = path.stat()
        local_sha = sha256_file(path)
        fingerprint = [stat.st_size, int(stat.st_mtime), local_sha]
        if confirmed.get(key) == fingerprint and key in existing:
            skipped += 1
            receipts.append({"key": key, "sha256": local_sha, "bytes": stat.st_size,
                             "publicURL": f"{PUBLIC_BASE}/{key}"})
            continue
        # Budget check BEFORE the expensive head+hash round-trip.
        if args.max_seconds and time.time() - started > args.max_seconds:
            pending.append(key)
            continue

        if existing.get(key) == stat.st_size:
            # Size match alone is not success (review §6.1): trust only a
            # matching sha256 in the object metadata; anything else re-uploads.
            res = get_remote_status(key)
            if res["status"] == "ok" and res["sha256"] == local_sha:
                skipped += 1
                confirmed[key] = fingerprint
                receipts.append({"key": key, "sha256": local_sha, "bytes": stat.st_size,
                                 "publicURL": f"{PUBLIC_BASE}/{key}"})
                continue
            reupload += 1

        if args.max_seconds and time.time() - started > args.max_seconds:
            pending.append(key)
            continue

        if not args.dry_run:
            try:
                client.upload_file(
                    str(path), bucket, key,
                    ExtraArgs={"Metadata": {"sha256": local_sha}},
                )
                # HEAD回读校验并记录审计receipt
                res_after = get_remote_status(key)
                if res_after["status"] == "ok" and res_after["sha256"] == local_sha:
                    confirmed[key] = fingerprint
                    uploaded += 1
                    receipts.append({"key": key, "sha256": local_sha, "bytes": stat.st_size,
                                     "publicURL": f"{PUBLIC_BASE}/{key}"})
                else:
                    failures += 1
                    print(f"Post-upload HEAD verification failed for {key}")
            except Exception as e:
                failures += 1
                print(f"Failed to upload {key}: {e}")
        else:
            uploaded += 1

    try:
        cache_path.write_text(json.dumps(confirmed))
    except OSError:
        pass
    if not args.dry_run:
        write_json(receipt_path, {
            "schemaVersion": 1, "bucket": bucket, "prefix": args.prefix,
            "verifiedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "objects": sorted(receipts, key=lambda row: row["key"]),
        }, ensure_ascii=False, indent=2)

    print(json.dumps({
        "bucket": bucket,
        "prefix": args.prefix,
        "uploaded": uploaded,
        "reuploadedAfterShaMiss": reupload,
        "skippedVerified": skipped,
        "failed": failures,
        "pending": len(pending),
        "receipt": str(receipt_path),
        "seconds": round(time.time() - started, 1),
    }, ensure_ascii=False))
    return 1 if failures or (pending and not args.allow_pending) else 0


if __name__ == "__main__":
    raise SystemExit(main())
