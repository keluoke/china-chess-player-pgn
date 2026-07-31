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
import ssl
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
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


def load_secrets(path: pathlib.Path = SECRETS) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", default="bulk/lichess-broadcast/shards")
    parser.add_argument("--source-root", type=pathlib.Path,
                        help="override the configured local source root")
    parser.add_argument("--secrets", type=pathlib.Path, default=SECRETS)
    parser.add_argument("--receipt-path", type=pathlib.Path,
                        help="override the generated receipt path")
    parser.add_argument("--receipt-field", default="objects",
                        help="receipt array field; use a distinct field when sharing a receipt")
    parser.add_argument("--workers", type=int, default=1,
                        help="parallel file workers for upload/verification")
    parser.add_argument("--ensure-cors", action="store_true",
                        help="allow browser GET/HEAD requests from public origins")
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

    secrets = load_secrets(args.secrets)
    client = boto3.client(
        "s3",
        endpoint_url=secrets["R2_ENDPOINT"],
        aws_access_key_id=secrets["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=secrets["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
        config=Config(retries={"max_attempts": 3}),
    )
    bucket = secrets.get("R2_BUCKET", "chess-data")
    source_root = args.source_root or SOURCES[args.prefix]
    receipt_path = args.receipt_path or (
        ROOT / "data" / "generated" / "r2-object-receipts" / f"{args.prefix.replace('/', '--')}.json"
    )
    workers = max(1, args.workers)

    if args.ensure_cors and not args.dry_run:
        cors_rule = {
            "allowed": {
                "headers": ["*"],
                "methods": ["GET", "HEAD"],
                "origins": [
                    "https://4chess.cc",
                    "https://www.4chess.cc",
                    "https://china-chess-player-pgn.pages.dev",
                ],
            },
            "exposeHeaders": ["ETag", "Content-Length", "Content-Type"],
            "maxAgeSeconds": 86400,
        }
        api_token = secrets.get("R2_TOKEN")
        account_id = secrets.get("CF_ACCOUNT_ID")
        if api_token and account_id:
            request = urllib.request.Request(
                f"https://api.cloudflare.com/client/v4/accounts/{account_id}/r2/buckets/{bucket}/cors",
                data=json.dumps({"rules": [cors_rule]}).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {api_token}",
                    "Content-Type": "application/json",
                },
                method="PUT",
            )
            try:
                try:
                    import certifi
                    ssl_context = ssl.create_default_context(cafile=certifi.where())
                except ImportError:
                    ssl_context = ssl.create_default_context()
                with urllib.request.urlopen(request, timeout=30, context=ssl_context) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                detail = error.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"R2 CORS API failed: HTTP {error.code} {detail[:300]}") from error
            if payload.get("success") is False:
                raise RuntimeError(f"R2 CORS API rejected policy: {payload.get('errors')}")
        else:
            client.put_bucket_cors(
                Bucket=bucket,
                CORSConfiguration={"CORSRules": [{
                    "AllowedHeaders": cors_rule["allowed"]["headers"],
                    "AllowedMethods": cors_rule["allowed"]["methods"],
                    "AllowedOrigins": cors_rule["allowed"]["origins"],
                    "ExposeHeaders": cors_rule["exposeHeaders"],
                    "MaxAgeSeconds": cors_rule["maxAgeSeconds"],
                }]},
            )

    def source_files() -> list[pathlib.Path]:
        return [path for path in sorted(source_root.rglob("*")) if path.is_file()]

    def object_key(path: pathlib.Path) -> str:
        return f"{args.prefix.rstrip('/')}/{path.relative_to(source_root).as_posix()}"

    def upload_args(path: pathlib.Path, local_sha: str) -> dict[str, Any]:
        content_type = "application/x-chess-pgn" if path.suffix.lower() == ".pgn" else "application/octet-stream"
        return {
            "Metadata": {"sha256": local_sha},
            "ContentType": content_type,
            "CacheControl": "public, max-age=31536000, immutable",
        }

    def write_receipt(receipts: list[dict[str, Any]]) -> None:
        try:
            payload = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {"schemaVersion": 1, "bucket": bucket}
        payload["bucket"] = bucket
        payload["verifiedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if args.receipt_field == "objects":
            payload["prefix"] = args.prefix
        else:
            payload[f"{args.receipt_field}Prefix"] = args.prefix
        payload[args.receipt_field] = sorted(receipts, key=lambda row: row["key"])
        write_json(receipt_path, payload, ensure_ascii=False, indent=2)

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
        def verify_path(path: pathlib.Path) -> tuple[str, str]:
            key = object_key(path)
            res = get_remote_status(key)
            if res["status"] == "absent":
                return "absent", f"absent: {key}"
            if res["status"] == "error":
                return "error", f"network-error: {key} ({res['error']})"
            if not res["sha256"]:
                return "missing", f"no-sha-metadata: {key}"
            local_sha = sha256_file(path)
            if res["sha256"] == local_sha:
                return "verified", ""
            return "mismatched", f"SHA MISMATCH: {key} (local: {local_sha}, remote: {res['sha256']})"

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for status, problem in pool.map(verify_path, source_files()):
                if status == "verified":
                    verified += 1
                elif status == "mismatched":
                    mismatched += 1
                elif status == "missing":
                    missing_meta += 1
                elif status == "absent":
                    absent += 1
                else:
                    error_count += 1
                if problem:
                    problems.append(problem)

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
        for path in source_files():
            key = object_key(path)
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
                        ExtraArgs=upload_args(path, local_sha),
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
            write_receipt(receipts)
        print(json.dumps({
            "bucket": bucket, "prefix": args.prefix, "metadataStamped": stamped,
            "alreadyStamped": already,
            "failedBackfill": failed_backfill,
            "pending": len(pending), "seconds": round(time.time() - started, 1),
        }, ensure_ascii=False))
        return 1 if failed_backfill > 0 or (pending and not args.allow_pending) else 0

    uploaded = skipped = reupload = failures = 0
    pending: list[str] = []
    receipts: list[dict[str, Any]] = []

    def upload_path(path: pathlib.Path) -> tuple[str, dict[str, Any] | None, str]:
        key = object_key(path)
        stat = path.stat()
        local_sha = sha256_file(path)
        fingerprint = [stat.st_size, int(stat.st_mtime), local_sha]
        if confirmed.get(key) == fingerprint and key in existing:
            return "skipped", {"key": key, "sha256": local_sha, "bytes": stat.st_size,
                               "publicURL": f"{PUBLIC_BASE}/{key}"}, ""
        if args.max_seconds and time.time() - started > args.max_seconds:
            return "pending", None, key

        if existing.get(key) == stat.st_size:
            res = get_remote_status(key)
            if res["status"] == "ok" and res["sha256"] == local_sha:
                confirmed[key] = fingerprint
                return "skipped", {"key": key, "sha256": local_sha, "bytes": stat.st_size,
                                   "publicURL": f"{PUBLIC_BASE}/{key}"}, ""

        if args.max_seconds and time.time() - started > args.max_seconds:
            return "pending", None, key

        if args.dry_run:
            return "uploaded", None, ""
        try:
            client.upload_file(str(path), bucket, key, ExtraArgs=upload_args(path, local_sha))
            res_after = get_remote_status(key)
            if res_after["status"] != "ok" or res_after["sha256"] != local_sha:
                return "failed", None, f"Post-upload HEAD verification failed for {key}"
            confirmed[key] = fingerprint
            return "uploaded", {"key": key, "sha256": local_sha, "bytes": stat.st_size,
                                "publicURL": f"{PUBLIC_BASE}/{key}"}, ""
        except Exception as error:
            return "failed", None, f"Failed to upload {key}: {error}"

    files = source_files()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(upload_path, path): path for path in files}
        for future in as_completed(futures):
            status, receipt, message = future.result()
            if status == "uploaded":
                uploaded += 1
            elif status == "skipped":
                skipped += 1
            elif status == "pending":
                pending.append(message)
            else:
                failures += 1
                print(message)
            if receipt:
                receipts.append(receipt)

    try:
        cache_path.write_text(json.dumps(confirmed))
    except OSError:
        pass
    if not args.dry_run:
        write_receipt(receipts)

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
