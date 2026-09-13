"""R2 immutable-object transaction: inventory, write, body proof and receipt."""
from __future__ import annotations
import hashlib
import json
import pathlib
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from stable_json import write_json
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
    repo_root: pathlib.Path,
    public_base: str,
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

    snapshot_path = repo_root / "docs" / "data" / "snapshot.json"
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
            "publicURL": f"{public_base}/{local['key']}",
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
