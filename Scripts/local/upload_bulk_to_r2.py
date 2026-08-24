#!/usr/bin/env python3
"""Upload large immutable assets (bulk PGN shards, event archives) to R2.

Reads S3 credentials from ``.secrets.local`` (never committed). Idempotent:
existing objects with matching size are skipped, so interrupted runs resume.
Player PGN packages use a content-addressed primary key and an explicitly
short-lived compatibility alias.  A newly uploaded object is read back and
hashed before it can enter the receipt; existing immutable objects must carry
matching size and SHA-256 metadata.

Usage:
  python3 Scripts/local/upload_bulk_to_r2.py [--prefix bulk/lichess-broadcast/shards]
                                             [--max-seconds 40] [--dry-run]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
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
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def content_addressed_key(prefix: str, sha256: str, suffix: str = ".pgn") -> str:
    if not HEX64.fullmatch(sha256):
        raise ValueError(f"invalid sha256: {sha256}")
    safe_suffix = suffix.lower() if suffix.lower() in {".pgn", ".json", ".zst"} else ""
    return f"{prefix.rstrip('/')}/objects/sha256/{sha256[:2]}/{sha256}{safe_suffix}"


def _body_sha256(response: dict[str, Any]) -> str:
    digest = hashlib.sha256()
    body = response["Body"]
    try:
        for chunk in iter(lambda: body.read(1024 * 1024), b""):
            digest.update(chunk)
    finally:
        close = getattr(body, "close", None)
        if close:
            close()
    return digest.hexdigest()


def run_content_addressed_upload(
    *,
    client: Any,
    bucket: str,
    prefix: str,
    source_root: pathlib.Path,
    files: list[pathlib.Path],
    receipt_path: pathlib.Path,
    receipt_field: str,
    workers: int,
    publish_aliases: bool,
    verify_only: bool,
    verify_body: bool,
    dry_run: bool,
    audit_sample: int = 0,
    endpoint: str = "",
    max_class_a: int = 10_000,
    max_class_b: int = 10_000,
) -> int:
    """Publish immutable objects with inventory and rotating body proof.

    A full ListObjectsV2 inventory proves that every current key exists with
    the expected size.  New or previously uncertified keys are GET-hashed in
    full; later snapshots carry that body certificate forward but rotate a
    bounded, persistent GET audit across the sorted current key set.  A
    mismatching immutable key is never overwritten.
    """

    if publish_aliases:
        print(json.dumps({"ok": False, "error": "R2_CONTENT_ADDRESSED_ALIASES_FORBIDDEN"}))
        return 1
    source_root = source_root.resolve()
    endpoint = endpoint.rstrip("/")
    object_pattern = f"{prefix.rstrip('/')}/objects/sha256/<first-two>/<sha256>.pgn"

    snapshot_path = ROOT / "docs" / "data" / "snapshot.json"
    try:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(json.dumps({"ok": False, "error": f"R2_SNAPSHOT_INVALID:{error}"}))
        return 1
    snapshot_id = str(snapshot.get("snapshotId") or "")
    input_commit = str(snapshot.get("inputCommit") or "")
    if not snapshot_id or not re.fullmatch(r"[0-9a-f]{40}", input_commit):
        print(json.dumps({"ok": False, "error": "R2_SNAPSHOT_CONTRACT_INVALID"}))
        return 1

    try:
        previous = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        previous = {}

    previous_rows_list = previous.get(receipt_field) or []
    previous_rows: dict[str, dict[str, Any]] = {}
    reusable_previous = (
        previous.get("schemaVersion") == 3
        and previous.get("contentAddressed") is True
        and previous.get("bodyCertified") is True
        and previous.get("bucket") == bucket
        and str(previous.get("endpoint") or "").rstrip("/") == endpoint
        and previous.get("objectPattern") == object_pattern
        and isinstance(previous_rows_list, list)
    )
    if reusable_previous:
        seen_keys: dict[str, tuple[str, int]] = {}
        for row in previous_rows_list:
            path = str(row.get("path") or "") if isinstance(row, dict) else ""
            key = str(row.get("key") or "") if isinstance(row, dict) else ""
            if (
                not path
                or path in previous_rows
                or not key
                or row.get("verified") != "body-sha256"
                or not row.get("bodyVerifiedAtSnapshot")
                or not HEX64.fullmatch(str(row.get("sha256") or ""))
                or int(row.get("bytes") or -1) <= 0
            ):
                reusable_previous = False
                previous_rows = {}
                break
            signature = (str(row.get("sha256") or ""), int(row.get("bytes") or -1))
            if key in seen_keys and seen_keys[key] != signature:
                reusable_previous = False
                previous_rows = {}
                break
            previous_rows[path] = row
            seen_keys[key] = signature
    if not reusable_previous:
        previous_rows = {}

    local_rows: list[dict[str, Any]] = []
    objects: dict[str, dict[str, Any]] = {}
    for raw_path in sorted(set(files)):
        path = raw_path.resolve()
        try:
            relative = path.relative_to(source_root).as_posix()
        except ValueError as error:
            print(json.dumps({"ok": False, "error": f"R2_SOURCE_PATH_ESCAPE:{path}"}))
            return 1
        if not path.is_file():
            print(json.dumps({"ok": False, "error": f"R2_SOURCE_FILE_MISSING:{path}"}))
            return 1
        sha256 = sha256_file(path)
        size = path.stat().st_size
        key = content_addressed_key(prefix, sha256, path.suffix)
        logical_path = f"data/pgn/{relative}" if prefix.rstrip("/") == "data/pgn" else f"{prefix.rstrip('/')}/{relative}"
        row = {"path": logical_path, "key": key, "sha256": sha256, "bytes": size, "source": path}
        local_rows.append(row)
        existing = objects.get(key)
        if existing and (existing["sha256"], existing["bytes"]) != (sha256, size):
            print(json.dumps({"ok": False, "error": f"R2_LOCAL_KEY_COLLISION:{key}"}))
            return 1
        objects.setdefault(key, row)
    if not local_rows:
        print(json.dumps({"ok": False, "error": "R2_EMPTY_SOURCE_SET"}))
        return 1
    paths = [row["path"] for row in local_rows]
    if len(paths) != len(set(paths)):
        print(json.dumps({"ok": False, "error": "R2_DUPLICATE_LOGICAL_PATH"}))
        return 1

    inventory: dict[str, int] = {}
    inventory_pages = 0
    try:
        paginator = client.get_paginator("list_objects_v2")
        content_prefix = f"{prefix.rstrip('/')}/objects/sha256/"
        for page in paginator.paginate(Bucket=bucket, Prefix=content_prefix):
            inventory_pages += 1
            for item in page.get("Contents") or []:
                inventory[str(item.get("Key") or "")] = int(item.get("Size") or 0)
    except Exception as error:
        print(json.dumps({"ok": False, "error": f"R2_INVENTORY_FAILED:{error}"}))
        return 1

    sorted_keys = sorted(objects)
    previous_cursor = int(((previous.get("audit") or {}).get("nextCursor") or 0)) if reusable_previous else 0
    start_cursor = previous_cursor % len(sorted_keys)
    sample_size = min(max(0, int(audit_sample)), len(sorted_keys))
    audit_keys = {
        sorted_keys[(start_cursor + offset) % len(sorted_keys)]
        for offset in range(sample_size)
    }
    next_cursor = (start_cursor + sample_size) % len(sorted_keys)

    prior_by_key: dict[str, dict[str, Any]] = {}
    for row in previous_rows.values():
        prior_by_key[str(row["key"])] = row
    missing_keys: set[str] = set()
    read_keys: set[str] = set()
    problems: list[str] = []
    for key, row in objects.items():
        listed_size = inventory.get(key)
        if listed_size is None:
            missing_keys.add(key)
            read_keys.add(key)
        elif listed_size != row["bytes"]:
            problems.append(f"R2_IMMUTABLE_OBJECT_SIZE_MISMATCH:{key}")
        prior = prior_by_key.get(key)
        prior_certified = bool(
            prior
            and prior.get("sha256") == row["sha256"]
            and int(prior.get("bytes") or -1) == row["bytes"]
            and prior.get("verified") == "body-sha256"
        )
        if not prior_certified or key in audit_keys or verify_body or verify_only:
            read_keys.add(key)

    class_a_requests = inventory_pages + (0 if verify_only else len(missing_keys))
    class_b_requests = len(read_keys)
    if class_a_requests > max_class_a or class_b_requests > max_class_b:
        problems.append(
            f"R2_QUOTA_GUARD:classA={class_a_requests}/{max_class_a},classB={class_b_requests}/{max_class_b}"
        )
    if problems:
        print(json.dumps({"ok": False, "problems": problems[:50]}, ensure_ascii=False))
        return 1

    def remote_body(key: str) -> dict[str, Any]:
        try:
            response = client.get_object(Bucket=bucket, Key=key)
        except Exception as error:
            code = str(
                (getattr(error, "response", {}) or {}).get("Error", {}).get("Code") or ""
            )
            if code in {"404", "NoSuchKey", "NotFound"} or any(
                marker in str(error) for marker in ("Not Found", "404", "NoSuchKey")
            ):
                return {"status": "absent"}
            return {"status": "error", "error": str(error)}
        result = {
            "status": "ok",
            "sha256": (response.get("Metadata") or {}).get("sha256"),
            "size": int(response.get("ContentLength") or 0),
        }
        try:
            result["bodySha256"] = _body_sha256(response)
        except Exception as error:
            return {"status": "error", "error": str(error)}
        return result

    def upload(path: pathlib.Path, key: str, sha256: str, *, immutable: bool) -> None:
        client.upload_file(
            str(path),
            bucket,
            key,
            ExtraArgs={
                "Metadata": {"sha256": sha256},
                "ContentType": "application/x-chess-pgn" if path.suffix.lower() == ".pgn" else "application/octet-stream",
                "CacheControl": (
                    "public, max-age=31536000, immutable"
                    if immutable
                    else "public, max-age=60, must-revalidate"
                ),
            },
        )

    verified_now: set[str] = set()
    verification_reason: dict[str, str] = {}

    def process(key: str) -> list[str]:
        row = objects[key]
        path = row["source"]
        local_problems: list[str] = []
        if key in missing_keys:
            if verify_only:
                return [f"R2_OBJECT_MISSING:{key}"]
            if dry_run:
                return [f"R2_DRY_RUN_OBJECT_MISSING:{key}"]
            try:
                upload(path, key, row["sha256"], immutable=True)
                verification_reason[key] = "uploaded-readback"
            except Exception as error:
                return [f"R2_UPLOAD_FAILED:{key}:{error}"]
        if key in read_keys:
            status = remote_body(key)
            if (
                status.get("status") != "ok"
                or status.get("sha256") != row["sha256"]
                or int(status.get("size") or -1) != row["bytes"]
                or status.get("bodySha256") != row["sha256"]
            ):
                local_problems.append(f"R2_IMMUTABLE_OBJECT_MISMATCH:{key}:{status}")
            else:
                verified_now.add(key)
                verification_reason.setdefault(key, "rotating-audit" if key in audit_keys else "body-readback")
        return local_problems

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(process, key): key for key in sorted_keys}
        for future in as_completed(futures):
            problems.extend(future.result())

    if problems:
        print(json.dumps({
            "ok": False,
            "contentAddressed": True,
            "files": len(local_rows),
            "verified": len(verified_now),
            "errors": len(problems),
            "problems": problems[:50],
        }, ensure_ascii=False))
        return 1

    receipts: list[dict[str, Any]] = []
    for local in local_rows:
        prior = previous_rows.get(local["path"]) or {}
        verified_snapshot = (
            snapshot_id if local["key"] in verified_now
            else str(
                prior.get("bodyVerifiedAtSnapshot")
                or (prior_by_key.get(local["key"]) or {}).get("bodyVerifiedAtSnapshot")
                or ""
            )
        )
        if not verified_snapshot:
            print(json.dumps({"ok": False, "error": f"R2_BODY_CERTIFICATE_MISSING:{local['key']}"}))
            return 1
        receipts.append({
            "path": local["path"],
            "key": local["key"],
            "sha256": local["sha256"],
            "bytes": local["bytes"],
            "publicURL": f"{PUBLIC_BASE}/{local['key']}",
            "verified": "body-sha256",
            "bodyVerifiedAtSnapshot": verified_snapshot,
            "verification": verification_reason.get(local["key"], "prior-receipt"),
        })

    if not dry_run:
        payload: dict[str, Any] = {
            "schemaVersion": 3,
            "bucket": bucket,
            "endpoint": endpoint,
            "verifiedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "contentAddressed": True,
            "bodyVerified": True,
            "bodyCertified": True,
            "objectPattern": object_pattern,
            "inventory": {
                "prefix": f"{prefix.rstrip('/')}/objects/sha256/",
                "expectedKeys": len(objects),
                "presentKeys": len(objects),
                "pages": inventory_pages,
                "missingKeys": 0,
                "sizeMismatches": 0,
            },
            "audit": {
                "startCursor": start_cursor,
                "nextCursor": next_cursor,
                "sampleSize": sample_size,
                "auditedKeys": sorted(audit_keys),
            },
            "quota": {
                "classARequests": class_a_requests,
                "classBRequests": class_b_requests,
                "maxClassA": max_class_a,
                "maxClassB": max_class_b,
            },
            receipt_field: sorted(receipts, key=lambda row: row["path"]),
        }
        payload["snapshotId"] = snapshot_id
        payload["inputCommit"] = input_commit
        write_json(receipt_path, payload, ensure_ascii=False, indent=2)
    print(json.dumps({
        "ok": True,
        "contentAddressed": True,
        "files": len(local_rows),
        "verified": len(receipts),
        "objects": len(objects),
        "audited": sample_size,
        "classARequests": class_a_requests,
        "classBRequests": class_b_requests,
        "bodyCertified": True,
    }, ensure_ascii=False))
    return 0


def select_source_files(source_root: pathlib.Path, file_list: pathlib.Path | None) -> list[pathlib.Path]:
    if not file_list:
        return [path for path in sorted(source_root.rglob("*")) if path.is_file()]
    root = source_root.resolve()
    selected: list[pathlib.Path] = []
    for raw in file_list.read_text(encoding="utf-8").splitlines():
        relative = pathlib.PurePosixPath(raw.strip())
        if not raw.strip():
            continue
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"invalid --file-list path: {raw}")
        path = (root / pathlib.Path(*relative.parts)).resolve()
        try:
            path.relative_to(root)
        except ValueError as error:
            raise ValueError(f"file-list path escapes source-root: {raw}") from error
        if not path.is_file():
            raise ValueError(f"file-list path is not a file: {raw}")
        selected.append(path)
    return sorted(set(selected))


def merge_receipt_rows(existing: object, verified: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = {
        str(row.get("key")): row
        for row in (existing if isinstance(existing, list) else [])
        if isinstance(row, dict) and row.get("key")
    }
    merged.update({str(row["key"]): row for row in verified})
    return sorted(merged.values(), key=lambda row: row["key"])


def load_secrets(path: pathlib.Path = SECRETS) -> dict[str, str]:
    values: dict[str, str] = {}
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    for line in lines:
        if "=" in line and not line.strip().startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    for key in ("R2_ENDPOINT", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"):
        if os.environ.get(key):
            values[key] = os.environ[key].strip()
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", default="bulk/lichess-broadcast/shards")
    parser.add_argument("--source-root", type=pathlib.Path,
                        help="override the configured local source root")
    parser.add_argument("--file-list", type=pathlib.Path,
                        help="newline-delimited paths relative to source-root; upload/verify only these files")
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
    parser.add_argument("--verify-body", action="store_true",
                        help="GET and hash existing object bodies as well as checking metadata")
    parser.add_argument("--content-addressed", action="store_true",
                        help="publish files under full-SHA object keys and emit a schema-v3 receipt")
    parser.add_argument("--publish-aliases", action="store_true",
                        help="also update short-lived repository-layout compatibility aliases")
    parser.add_argument("--audit-sample", type=int, default=0,
                        help="number of sorted current immutable keys to GET-hash per rotating audit")
    parser.add_argument("--max-class-a", type=int, default=10000,
                        help="fail closed before exceeding this run's Class A request budget")
    parser.add_argument("--max-class-b", type=int, default=10000,
                        help="fail closed before exceeding this run's Class B request budget")
    parser.add_argument("--backfill-metadata", action="store_true",
                        help="server-side CopyObject to attach sha256 metadata to size-matching objects "
                             "uploaded before checksums were recorded (no byte re-transfer)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    import boto3
    from botocore.config import Config

    secrets = load_secrets(args.secrets)
    required_secrets = [
        key for key in ("R2_ENDPOINT", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")
        if not secrets.get(key)
    ]
    if required_secrets:
        raise SystemExit(f"R2_CONFIG_MISSING: {','.join(required_secrets)}")
    client = boto3.client(
        "s3",
        endpoint_url=secrets["R2_ENDPOINT"],
        aws_access_key_id=secrets["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=secrets["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
        config=Config(retries={"max_attempts": 3}),
    )
    bucket = secrets.get("R2_BUCKET", "chess-data")
    source_root = (args.source_root or SOURCES[args.prefix]).resolve()
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
        try:
            return select_source_files(source_root, args.file_list)
        except ValueError as error:
            raise SystemExit(str(error)) from error

    if args.content_addressed:
        return run_content_addressed_upload(
            client=client,
            bucket=bucket,
            prefix=args.prefix,
            source_root=source_root,
            files=source_files(),
            receipt_path=receipt_path,
            receipt_field=args.receipt_field,
            workers=workers,
            publish_aliases=args.publish_aliases,
            verify_only=args.verify,
            verify_body=args.verify_body,
            dry_run=args.dry_run,
            audit_sample=args.audit_sample,
            endpoint=secrets["R2_ENDPOINT"],
            max_class_a=args.max_class_a,
            max_class_b=args.max_class_b,
        )

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
        if args.file_list and isinstance(payload.get(args.receipt_field), list):
            receipts = merge_receipt_rows(payload[args.receipt_field], receipts)
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

        verified_all = not (mismatched or absent or missing_meta or error_count)
        if verified_all and not args.dry_run:
            receipts = []
            for path in source_files():
                key = object_key(path)
                stat = path.stat()
                receipts.append({
                    "key": key,
                    "sha256": sha256_file(path),
                    "bytes": stat.st_size,
                    "publicURL": f"{PUBLIC_BASE}/{key}",
                })
            write_receipt(receipts)

        print(json.dumps({
            "bucket": bucket, "prefix": args.prefix, "verified": verified,
            "mismatched": mismatched, "missingMetadata": missing_meta,
            "absent": absent, "errors": error_count, "problems": problems[:20],
            "seconds": round(time.time() - started, 1),
        }, ensure_ascii=False))
        return 0 if verified_all else 1

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
