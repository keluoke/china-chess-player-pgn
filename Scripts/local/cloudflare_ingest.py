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
    headers = signed_headers(method, path, digest, secret)
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
        base_sha = item.get("deliveryBaseSha256", item.get("baseSha256"))
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
            digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
            if digest != item["sha256"] or candidate.stat().st_size != item["bytes"]:
                raise ShadowDeliveryError(f"RELEASE_HASH_MISMATCH: {path}")
            uploads.append((projected, candidate))
    return {
        "schemaVersion": 1,
        "runId": manifest["runId"],
        "command": manifest.get("command") or "unknown",
        "baseCommit": manifest.get("deliveryBaseCommit") or manifest.get("baseCommit"),
        "source": manifest.get("source") or {},
        "files": shadow_files,
    }, uploads


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
    save_state(state_path, {"runId": run_id, "status": "registering", "endpoint": endpoint})
    registered = request_json(endpoint, "POST", "/v1/releases", secret, payload=payload)
    for index, (item, candidate) in enumerate(uploads, start=1):
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
    return 0 if result.get("status") == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
