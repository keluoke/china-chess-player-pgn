#!/usr/bin/env python3
"""Deliver one immutable local outbox bundle to the Cloudflare shadow ingest.

This command never contacts a chess source and never mutates Git.  The Worker
streams declared objects into a dedicated content-addressed R2 bucket, so the
client does not need a new bucket-wide credential.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import pathlib
import secrets
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent))

from source_policy import local_state_root  # noqa: E402

import run_manager  # noqa: E402


TERMINAL = {"complete", "conflict", "failed"}
KEYCHAIN_SERVICE = "china-chess-cloudflare-ingest-shadow"
DEFAULT_ENDPOINT = "https://chess-data-ingest-shadow.seanyan099.workers.dev"
# Logical snapshot limits.  Registration and merge work is split into small
# requests/messages so this is no longer the per-invocation D1 query limit.
MAX_RELEASE_FILES = 384
MAX_CHUNK_FILES = 10
MAX_RELEASE_BYTES = 96 * 1024 * 1024
MAX_FILE_BYTES = 96 * 1024 * 1024
MAX_SINGLE_UPLOAD_BYTES = 16 * 1024 * 1024
MULTIPART_PART_BYTES = 8 * 1024 * 1024
MAX_MULTIPART_PARTS = 12


class ShadowDeliveryError(RuntimeError):
    pass


def canonical_request(method: str, path: str, timestamp: int, nonce: str, digest: str) -> str:
    return "\n".join((method.upper(), path, str(timestamp), nonce, digest))


def signed_headers(method: str, path: str, body_digest: str, secret: str) -> dict[str, str]:
    timestamp = int(time.time())
    nonce = secrets.token_hex(16)
    canonical = canonical_request(method, path, timestamp, nonce, body_digest)
    signature = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    return {
        "x-chess-timestamp": str(timestamp),
        "x-chess-nonce": nonce,
        "x-chess-content-sha256": body_digest,
        "x-chess-signature": signature,
    }


def request_json(
    endpoint: str,
    method: str,
    path: str,
    secret: str,
    payload: dict[str, Any] | None = None,
    raw_body: bytes | None = None,
    content_digest: str | None = None,
) -> dict[str, Any]:
    if payload is not None and raw_body is not None:
        raise ValueError("payload and raw_body are mutually exclusive")
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        content_type = "application/json"
    else:
        body = raw_body or b""
        content_type = "application/octet-stream"
    digest = content_digest or hashlib.sha256(body).hexdigest()
    headers = signed_headers(method, urllib.parse.urlsplit(path).path, digest, secret)
    headers.update({"content-type": content_type, "content-length": str(len(body))})
    url = endpoint.rstrip("/") + path
    if shutil.which("curl"):
        command = [
            "curl", "--fail-with-body", "--silent", "--show-error",
            "--max-time", "60", "--request", method.upper(),
        ]
        if method.upper() != "GET":
            command.extend(("--header", f"content-type: {content_type}"))
        for name, value in headers.items():
            command.extend(("--header", f"{name}: {value}"))
        if method.upper() != "GET":
            command.extend(("--data-binary", "@-"))
        command.append(url)
        completed = subprocess.run(command, input=body, capture_output=True, check=False)
        try:
            result = json.loads(completed.stdout.decode())
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            detail = completed.stderr.decode(errors="replace").strip()
            raise ShadowDeliveryError(f"CLOUDFLARE_INGEST_UNAVAILABLE: {detail or 'invalid response'}") from error
        if completed.returncode != 0:
            raise ShadowDeliveryError(str(result.get("error") or f"CURL_{completed.returncode}"))
        if not isinstance(result, dict) or not result.get("ok"):
            raise ShadowDeliveryError(str(result.get("error") if isinstance(result, dict) else "INVALID_RESPONSE"))
        return result
    request = urllib.request.Request(
        url,
        data=body if method.upper() != "GET" else None,
        headers=headers,
        method=method.upper(),
    )
    ca_file = os.environ.get("SSL_CERT_FILE", "")
    if not ca_file:
        try:
            import certifi  # type: ignore

            ca_file = certifi.where()
        except ImportError:
            ca_file = "/etc/ssl/cert.pem" if pathlib.Path("/etc/ssl/cert.pem").is_file() else ""
    context = ssl.create_default_context(cafile=ca_file or None)
    try:
        with urllib.request.urlopen(request, timeout=60, context=context) as response:
            result = json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        try:
            detail = json.loads(error.read().decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            detail = {"error": f"HTTP_{error.code}"}
        raise ShadowDeliveryError(str(detail.get("error") or f"HTTP_{error.code}")) from error
    except (OSError, TimeoutError) as error:
        raise ShadowDeliveryError(f"CLOUDFLARE_INGEST_UNAVAILABLE: {error}") from error
    if not isinstance(result, dict) or not result.get("ok"):
        raise ShadowDeliveryError(str(result.get("error") if isinstance(result, dict) else "INVALID_RESPONSE"))
    return result


def bundle_paths(run_id: str, root: pathlib.Path | None = None) -> tuple[pathlib.Path, pathlib.Path]:
    outbox = (root or local_state_root()) / "outbox" / run_id
    manifest = outbox / "manifest.json"
    files = outbox / "files"
    if not manifest.is_file() or not files.is_dir():
        raise ShadowDeliveryError(f"OUTBOX_BUNDLE_MISSING: {run_id}")
    return manifest, files


def build_shadow_manifest(manifest_path: pathlib.Path, files_root: pathlib.Path) -> tuple[dict[str, Any], list[tuple[dict[str, Any], pathlib.Path]]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = run_manager.validate_manifest(manifest)
    shadow_files: list[dict[str, Any]] = []
    uploads: list[tuple[dict[str, Any], pathlib.Path]] = []
    for item in items:
        path = item["path"]
        base_sha = item.get(
            "shadowBaseSha256",
            item.get("deliveryBaseSha256", item.get("baseSha256")),
        )
        projected = {
            "path": path,
            "operation": item["operation"],
            "sha256": item.get("sha256"),
            "baseSha256": base_sha,
            "bytes": int(item.get("bytes") or 0),
        }
        shadow_files.append(projected)
        if item["operation"] == "upsert":
            candidate = files_root / path
            if not candidate.is_file():
                raise ShadowDeliveryError(f"OUTBOX_FILE_MISSING: {path}")
            digest = hashlib.sha256()
            parts: list[dict[str, Any]] = []
            with candidate.open("rb") as handle:
                while True:
                    body = handle.read(MULTIPART_PART_BYTES)
                    if not body:
                        break
                    digest.update(body)
                    if candidate.stat().st_size > MAX_SINGLE_UPLOAD_BYTES:
                        parts.append({
                            "number": len(parts) + 1,
                            "sha256": hashlib.sha256(body).hexdigest(),
                            "bytes": len(body),
                        })
            digest = digest.hexdigest()
            if digest != item["sha256"] or candidate.stat().st_size != item["bytes"]:
                raise ShadowDeliveryError(f"RELEASE_HASH_MISMATCH: {path}")
            if parts:
                projected["multipart"] = {
                    "partSize": MULTIPART_PART_BYTES,
                    "parts": parts,
                }
            uploads.append((projected, candidate))
    return {
        "schemaVersion": 1,
        "runId": manifest["runId"],
        "command": manifest.get("command") or "unknown",
        "baseCommit": manifest.get("deliveryBaseCommit") or manifest.get("baseCommit"),
        "source": manifest.get("source") or {},
        "files": shadow_files,
    }, uploads


def validate_shadow_limits(payload: dict[str, Any]) -> None:
    files = payload.get("files") or []
    if len(files) > MAX_RELEASE_FILES:
        raise ShadowDeliveryError(
            f"FREE_TIER_RELEASE_FILE_LIMIT: {len(files)} > {MAX_RELEASE_FILES}"
        )
    total = sum(int(item.get("bytes") or 0) for item in files)
    if total > MAX_RELEASE_BYTES:
        raise ShadowDeliveryError(
            f"FREE_TIER_RELEASE_BYTE_LIMIT: {total} > {MAX_RELEASE_BYTES}"
        )
    oversized = [item for item in files if int(item.get("bytes") or 0) > MAX_FILE_BYTES]
    if oversized:
        raise ShadowDeliveryError(
            f"FREE_TIER_RELEASE_OBJECT_LIMIT: {oversized[0].get('path')}"
        )
    invalid_multipart = [
        item for item in files
        if int(item.get("bytes") or 0) > MAX_SINGLE_UPLOAD_BYTES
        and not item.get("multipart")
    ]
    if invalid_multipart:
        raise ShadowDeliveryError(
            f"RELEASE_MULTIPART_REQUIRED: {invalid_multipart[0].get('path')}"
        )


def canonical_manifest_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def chunk_fingerprint_bytes(files: list[dict[str, Any]]) -> bytes:
    """Canonical cross-language binding for one registered file chunk."""
    rows = [
        [
            item.get("path"),
            item.get("operation"),
            item.get("sha256"),
            item.get("baseSha256"),
            int(item.get("bytes") or 0),
            [
                int(item["multipart"]["partSize"]),
                [
                    [int(part["number"]), part["sha256"], int(part["bytes"])]
                    for part in item["multipart"]["parts"]
                ],
            ] if item.get("multipart") else None,
        ]
        for item in files
    ]
    return json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode()


def registration_payloads(payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Project one logical manifest into a header plus bounded file chunks."""
    files = list(payload.get("files") or [])
    digest = hashlib.sha256(canonical_manifest_bytes(payload)).hexdigest()
    chunks = [files[index:index + MAX_CHUNK_FILES] for index in range(0, len(files), MAX_CHUNK_FILES)]
    chunk_digests = [hashlib.sha256(chunk_fingerprint_bytes(chunk)).hexdigest() for chunk in chunks]
    header = {
        "schemaVersion": 2,
        "runId": payload["runId"],
        "command": payload.get("command") or "unknown",
        "baseCommit": payload.get("baseCommit"),
        "source": payload.get("source") or {},
        "manifestSha256": digest,
        "expectedFiles": len(files),
        "expectedBytes": sum(int(item.get("bytes") or 0) for item in files),
        "expectedUpserts": sum(1 for item in files if item.get("operation") == "upsert"),
        "expectedMultipartFiles": sum(1 for item in files if item.get("multipart")),
        "expectedUploadParts": sum(
            len(item.get("multipart", {}).get("parts", [])) for item in files
        ),
        "expectedChunks": len(chunks),
        "chunkSha256s": chunk_digests,
    }
    projected = [
        {
            "schemaVersion": 1,
            "manifestSha256": digest,
            "chunkSha256": chunk_digests[index],
            "chunkIndex": index,
            "files": chunk,
        }
        for index, chunk in enumerate(chunks)
    ]
    return header, projected


def save_state(path: pathlib.Path, payload: dict[str, Any]) -> None:
    run_manager.atomic_json(path, {"schemaVersion": 1, **payload, "updatedAt": run_manager.now()})


def deliver(
    run_id: str,
    endpoint: str,
    secret: str,
    root: pathlib.Path | None = None,
    wait_seconds: int = 180,
) -> dict[str, Any]:
    manifest_path, files_root = bundle_paths(run_id, root)
    payload, uploads = build_shadow_manifest(manifest_path, files_root)
    state_path = manifest_path.parent / "shadow-delivery.json"
    try:
        validate_shadow_limits(payload)
    except ShadowDeliveryError as error:
        save_state(state_path, {
            "runId": run_id,
            "status": "ineligible",
            "errorCode": str(error).split(":", 1)[0],
            "message": str(error),
            "files": len(payload.get("files") or []),
            "bytes": sum(int(item.get("bytes") or 0) for item in payload.get("files") or []),
            "endpoint": endpoint,
        })
        raise
    save_state(state_path, {"runId": run_id, "status": "registering", "endpoint": endpoint})
    header, chunks = registration_payloads(payload)
    registered = request_json(endpoint, "POST", "/v1/releases", secret, payload=header)
    if registered.get("status") in TERMINAL:
        last = request_json(endpoint, "GET", f"/v1/releases/{run_id}", secret)
        save_state(state_path, {"runId": run_id, **last, "endpoint": endpoint})
        return last
    for index, chunk in enumerate(chunks, start=1):
        request_json(
            endpoint,
            "POST",
            f"/v1/releases/{run_id}/chunks/{chunk['chunkIndex']}",
            secret,
            payload=chunk,
        )
        save_state(state_path, {
            "runId": run_id,
            "status": "registering",
            "registeredChunks": index,
            "totalChunks": len(chunks),
            "files": len(payload.get("files") or []),
            "endpoint": endpoint,
        })
    for index, (item, candidate) in enumerate(uploads, start=1):
        multipart = item.get("multipart")
        if multipart:
            with candidate.open("rb") as handle:
                for part in multipart["parts"]:
                    body = handle.read(int(part["bytes"]))
                    if len(body) != int(part["bytes"]) or hashlib.sha256(body).hexdigest() != part["sha256"]:
                        raise ShadowDeliveryError(f"RELEASE_HASH_MISMATCH: {item['path']}")
                    request_json(
                        endpoint,
                        "PUT",
                        f"/v1/releases/{run_id}/files/{item['sha256']}/parts/{part['number']}/{part['sha256']}",
                        secret,
                        raw_body=body,
                        content_digest=str(part["sha256"]),
                    )
        else:
            body = candidate.read_bytes()
            request_json(
                endpoint,
                "PUT",
                f"/v1/releases/{run_id}/files/{item['sha256']}",
                secret,
                raw_body=body,
                content_digest=str(item["sha256"]),
            )
        save_state(state_path, {
            "runId": run_id,
            "status": "uploading",
            "uploaded": index,
            "totalUploads": len(uploads),
            "endpoint": endpoint,
        })
    committed = request_json(endpoint, "POST", f"/v1/releases/{run_id}/commit", secret, payload={})
    save_state(state_path, {"runId": run_id, "status": committed["status"], "endpoint": endpoint})
    deadline = time.monotonic() + wait_seconds
    last = committed
    while wait_seconds > 0 and time.monotonic() < deadline:
        last = request_json(endpoint, "GET", f"/v1/releases/{run_id}", secret)
        save_state(state_path, {"runId": run_id, **last, "endpoint": endpoint})
        if last.get("status") in TERMINAL:
            return last
        time.sleep(2)
    return last


def ingest_secret() -> str:
    configured = os.environ.get("CLOUDFLARE_INGEST_HMAC_SECRET", "")
    if configured:
        return configured
    if sys.platform == "darwin":
        result = subprocess.run(
            ["security", "find-generic-password", "-w", "-s", KEYCHAIN_SERVICE],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--endpoint", default=os.environ.get("CLOUDFLARE_INGEST_URL", DEFAULT_ENDPOINT))
    parser.add_argument("--wait-seconds", type=int, default=180)
    parser.add_argument(
        "--accept-queued", action="store_true",
        help="return success after an authenticated upload is queued; the panel will poll later",
    )
    parser.add_argument("--state-root", type=pathlib.Path)
    args = parser.parse_args()
    secret = ingest_secret()
    if not args.endpoint:
        parser.error("--endpoint or CLOUDFLARE_INGEST_URL is required")
    if not secret:
        parser.error("CLOUDFLARE_INGEST_HMAC_SECRET is required")
    try:
        result = deliver(args.run_id, args.endpoint, secret, args.state_root, max(0, args.wait_seconds))
    except (ShadowDeliveryError, run_manager.RunManagerError, OSError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    successful = {"complete"}
    if args.accept_queued:
        successful.update({"queued", "processing"})
    return 0 if result.get("status") in successful else 2


if __name__ == "__main__":
    raise SystemExit(main())
