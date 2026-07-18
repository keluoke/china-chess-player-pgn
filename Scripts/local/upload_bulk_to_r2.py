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

    existing: dict[str, int] = {}
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=args.prefix):
        for obj in page.get("Contents", []):
            existing[obj["Key"]] = obj["Size"]

    started = time.time()
    uploaded = skipped = 0
    pending = []
    for path in sorted(source_root.glob("*")):
        if not path.is_file():
            continue
        key = f"{args.prefix}/{path.name}"
        size = path.stat().st_size
        if existing.get(key) == size:
            skipped += 1
            continue
        if args.max_seconds and time.time() - started > args.max_seconds:
            pending.append(key)
            continue
        if not args.dry_run:
            client.upload_file(str(path), bucket, key)
        uploaded += 1

    print(json.dumps({
        "bucket": bucket,
        "prefix": args.prefix,
        "uploaded": uploaded,
        "skippedExisting": skipped,
        "pending": len(pending),
        "seconds": round(time.time() - started, 1),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
