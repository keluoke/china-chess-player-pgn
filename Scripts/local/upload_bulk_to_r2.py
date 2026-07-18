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

ROOT = pathlib.Path(__file__).resolve().parents[2]
SECRETS = ROOT / ".secrets.local"

SOURCES = {
    "bulk/lichess-broadcast/shards": ROOT / "docs/data/bulk/lichess-broadcast/shards",
}


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

    def sha256_file(path: pathlib.Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def remote_sha(key: str) -> str | None:
        try:
            head = client.head_object(Bucket=bucket, Key=key)
        except Exception:
            return None
        return (head.get("Metadata") or {}).get("sha256")

    existing: dict[str, int] = {}
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=args.prefix):
        for obj in page.get("Contents", []):
            existing[obj["Key"]] = obj["Size"]

    started = time.time()

    if args.verify:
        # Review §6.1: size equality never certifies content; compare the
        # sha256 stored in object metadata against the local file.
        verified = mismatched = missing_meta = absent = 0
        problems: list[str] = []
        for path in sorted(source_root.glob("*")):
            if not path.is_file():
                continue
            key = f"{args.prefix}/{path.name}"
            if key not in existing:
                absent += 1
                problems.append(f"absent: {key}")
                continue
            remote = remote_sha(key)
            if not remote:
                missing_meta += 1
                problems.append(f"no-sha-metadata: {key}")
                continue
            if remote == sha256_file(path):
                verified += 1
            else:
                mismatched += 1
                problems.append(f"SHA MISMATCH: {key}")
        print(json.dumps({
            "bucket": bucket, "prefix": args.prefix, "verified": verified,
            "mismatched": mismatched, "missingMetadata": missing_meta,
            "absent": absent, "problems": problems[:20],
            "seconds": round(time.time() - started, 1),
        }, ensure_ascii=False))
        return 1 if (mismatched or absent) else 0

    # Confirmation cache: once a remote object's sha256 metadata matched the
    # local file, skip the per-object HEAD on later runs unless the local
    # file changed. The --verify mode always re-checks remotely.
    cache_path = pathlib.Path.home() / ".r2-upload-confirmed.json"
    try:
        confirmed: dict[str, list] = json.loads(cache_path.read_text())
    except (OSError, ValueError):
        confirmed = {}

    if args.backfill_metadata:
        # Original uploads were integrity-checked per request by the SDK; the
        # sha256 metadata was simply not recorded. Attach it via server-side
        # copy (no byte re-transfer), then --verify closes the loop.
        stamped = already = size_mismatch = 0
        pending = []
        for path in sorted(source_root.glob("*")):
            if not path.is_file():
                continue
            key = f"{args.prefix}/{path.name}"
            stat = path.stat()
            if existing.get(key) != stat.st_size:
                size_mismatch += 1
                continue
            if confirmed.get(key) == [stat.st_size, int(stat.st_mtime)]:
                already += 1
                continue
            if args.max_seconds and time.time() - started > args.max_seconds:
                pending.append(key)
                continue
            if remote_sha(key) and remote_sha(key) != "probe":
                confirmed[key] = [stat.st_size, int(stat.st_mtime)]
                already += 1
                continue
            local_sha = sha256_file(path)
            if not args.dry_run:
                client.copy_object(
                    Bucket=bucket, Key=key,
                    CopySource={"Bucket": bucket, "Key": key},
                    Metadata={"sha256": local_sha},
                    MetadataDirective="REPLACE",
                )
                confirmed[key] = [stat.st_size, int(stat.st_mtime)]
            stamped += 1
        try:
            cache_path.write_text(json.dumps(confirmed))
        except OSError:
            pass
        print(json.dumps({
            "bucket": bucket, "prefix": args.prefix, "metadataStamped": stamped,
            "alreadyStamped": already, "sizeMismatch": size_mismatch,
            "pending": len(pending), "seconds": round(time.time() - started, 1),
        }, ensure_ascii=False))
        return 0

    uploaded = skipped = reupload = 0
    pending = []
    for path in sorted(source_root.glob("*")):
        if not path.is_file():
            continue
        key = f"{args.prefix}/{path.name}"
        stat = path.stat()
        fingerprint = [stat.st_size, int(stat.st_mtime)]
        if confirmed.get(key) == fingerprint and key in existing:
            skipped += 1
            continue
        # Budget check BEFORE the expensive head+hash round-trip.
        if args.max_seconds and time.time() - started > args.max_seconds:
            pending.append(key)
            continue
        local_sha: str | None = None
        if existing.get(key) == stat.st_size:
            # Size match alone is not success (review §6.1): trust only a
            # matching sha256 in the object metadata; anything else re-uploads.
            remote = remote_sha(key)
            local_sha = sha256_file(path)
            if remote == local_sha:
                skipped += 1
                confirmed[key] = fingerprint
                continue
            reupload += 1
        if args.max_seconds and time.time() - started > args.max_seconds:
            pending.append(key)
            continue
        if not args.dry_run:
            local_sha = local_sha or sha256_file(path)
            client.upload_file(
                str(path), bucket, key,
                ExtraArgs={"Metadata": {"sha256": local_sha}},
            )
            confirmed[key] = fingerprint
        uploaded += 1
    try:
        cache_path.write_text(json.dumps(confirmed))
    except OSError:
        pass

    print(json.dumps({
        "bucket": bucket,
        "prefix": args.prefix,
        "uploaded": uploaded,
        "reuploadedAfterShaMiss": reupload,
        "skippedVerified": skipped,
        "pending": len(pending),
        "seconds": round(time.time() - started, 1),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
