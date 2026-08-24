#!/usr/bin/env python3
"""Fail closed unless every by-player PGN package is certified in R2."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
import urllib.parse
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
BUCKET_ROOT = ROOT / "docs" / "data" / "index" / "by-player-buckets"
PACKAGE_MANIFEST = ROOT / "docs" / "data" / "index" / "by-player" / "manifest.json"
RECEIPT = ROOT / "docs" / "data" / "index" / "player-pgn-r2-receipt.json"
SNAPSHOT = ROOT / "docs" / "data" / "snapshot.json"
PGN_ROOT = ROOT / "docs" / "data" / "pgn"
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def load(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def file_sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def expected_packages(bucket_root: pathlib.Path) -> dict[str, dict[str, Any]]:
    expected: dict[str, dict[str, Any]] = {}
    for bucket_path in sorted(bucket_root.glob("[0-9a-f][0-9a-f].json")):
        players = load(bucket_path).get("players") or {}
        for fide_id, detail in players.items():
            for package in detail.get("packages") or []:
                path = str(package.get("pgnPath") or "")
                pure = pathlib.PurePosixPath(path)
                if (
                    not path.startswith("data/pgn/by-player/")
                    or pure.is_absolute()
                    or ".." in pure.parts
                    or path != pure.as_posix()
                    or path in expected
                ):
                    raise ValueError(f"invalid or duplicate package path: {path}")
                sha256 = str(package.get("sha256") or "")
                object_path = str(package.get("objectPath") or "")
                expected_object = f"data/pgn/objects/sha256/{sha256[:2]}/{sha256}.pgn"
                if not HEX64.fullmatch(sha256) or object_path != expected_object:
                    raise ValueError(f"content-addressed metadata mismatch: {path}")
                if str(package.get("publicURL") or "") != f"https://data.chessdb.aigclabs.cc/{expected_object}":
                    raise ValueError(f"publicURL is not content addressed: {path}")
                expected[path] = {
                    "fideID": str(fide_id),
                    "sha256": sha256,
                    "bytes": int(package.get("pgnBytes") or 0),
                    "key": expected_object,
                }
    return expected


def validate(
    *,
    bucket_root: pathlib.Path = BUCKET_ROOT,
    receipt_path: pathlib.Path = RECEIPT,
    snapshot_path: pathlib.Path = SNAPSHOT,
    pgn_root: pathlib.Path = PGN_ROOT,
    package_manifest_path: pathlib.Path = PACKAGE_MANIFEST,
    check_local_files: bool = True,
) -> dict[str, int]:
    expected = expected_packages(bucket_root)
    receipt = load(receipt_path)
    snapshot = load(snapshot_path)
    package_manifest = load(package_manifest_path)
    if (
        receipt.get("schemaVersion") != 3
        or receipt.get("contentAddressed") is not True
        or receipt.get("bodyVerified") is not True
        or receipt.get("bodyCertified") is not True
        or receipt.get("bucket") != "chess-data"
        or receipt.get("objectPattern") != "data/pgn/objects/sha256/<first-two>/<sha256>.pgn"
    ):
        raise ValueError("player PGN receipt is not a body-certified schema-v3 receipt")
    endpoint = urllib.parse.urlsplit(str(receipt.get("endpoint") or ""))
    if (
        endpoint.scheme != "https"
        or not endpoint.hostname
        or not endpoint.hostname.endswith(".r2.cloudflarestorage.com")
        or endpoint.username
        or endpoint.password
        or endpoint.query
        or endpoint.fragment
        or endpoint.path not in {"", "/"}
    ):
        raise ValueError("player PGN receipt endpoint is invalid")
    rows = receipt.get("playerObjects") or []
    actual = {str(row.get("path") or ""): row for row in rows}
    if len(actual) != len(rows):
        raise ValueError("duplicate paths in player PGN R2 receipt")
    if set(expected) != set(actual):
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        raise ValueError(f"player PGN receipt coverage mismatch: missing={missing[:5]} extra={extra[:5]}")
    snapshot_id = str(snapshot.get("snapshotId") or "")
    if not snapshot_id or receipt.get("snapshotId") != snapshot_id or package_manifest.get("snapshotId") != snapshot_id:
        raise ValueError("player PGN receipt snapshot mismatch")
    input_commit = str(snapshot.get("inputCommit") or "")
    if not re.fullmatch(r"[0-9a-f]{40}", input_commit) or receipt.get("inputCommit") != input_commit:
        raise ValueError("player PGN receipt input commit mismatch")
    unique_keys = {wanted["key"] for wanted in expected.values()}
    inventory = receipt.get("inventory") or {}
    if (
        inventory.get("prefix") != "data/pgn/objects/sha256/"
        or int(inventory.get("expectedKeys", -1)) != len(unique_keys)
        or int(inventory.get("presentKeys", -1)) != len(unique_keys)
        or int(inventory.get("pages") or 0) <= 0
        or int(inventory.get("missingKeys", -1)) != 0
        or int(inventory.get("sizeMismatches", -1)) != 0
    ):
        raise ValueError("player PGN receipt inventory mismatch")
    audit = receipt.get("audit") or {}
    audited_keys = audit.get("auditedKeys") or []
    ordered_keys = sorted(unique_keys)
    expected_sample = min(384, len(ordered_keys))
    start_cursor = audit.get("startCursor")
    next_cursor = audit.get("nextCursor")
    expected_audited = {
        ordered_keys[(start_cursor + offset) % len(ordered_keys)]
        for offset in range(expected_sample)
    } if ordered_keys and isinstance(start_cursor, int) else set()
    if (
        not isinstance(audited_keys, list)
        or len(audited_keys) != len(set(audited_keys))
        or int(audit.get("sampleSize", -1)) != expected_sample
        or len(audited_keys) != expected_sample
        or not set(audited_keys).issubset(unique_keys)
        or set(audited_keys) != expected_audited
        or not isinstance(start_cursor, int)
        or not 0 <= start_cursor < max(1, len(ordered_keys))
        or not isinstance(next_cursor, int)
        or next_cursor != (start_cursor + expected_sample) % max(1, len(ordered_keys))
    ):
        raise ValueError("player PGN receipt audit mismatch")
    quota = receipt.get("quota") or {}
    if (
        int(quota.get("classARequests", -1)) < 0
        or int(quota.get("classBRequests", -1)) < 0
        or int(quota.get("classARequests") or 0) > int(quota.get("maxClassA") or -1)
        or int(quota.get("classBRequests") or 0) > int(quota.get("maxClassB") or -1)
    ):
        raise ValueError("player PGN receipt quota mismatch")
    total_bytes = 0
    for path, wanted in expected.items():
        row = actual[path]
        if (
            row.get("sha256") != wanted["sha256"]
            or int(row.get("bytes") or -1) != wanted["bytes"]
            or row.get("key") != wanted["key"]
            or row.get("publicURL") != f"https://data.chessdb.aigclabs.cc/{wanted['key']}"
            or row.get("verified") != "body-sha256"
            or not row.get("bodyVerifiedAtSnapshot")
            or wanted["bytes"] <= 0
        ):
            raise ValueError(f"player PGN receipt row mismatch: {path}")
        if check_local_files:
            local = pgn_root / pathlib.Path(path).relative_to("data/pgn")
            if not local.is_file() or local.stat().st_size != wanted["bytes"] or file_sha256(local) != wanted["sha256"]:
                raise ValueError(f"local player PGN package mismatch: {path}")
        total_bytes += wanted["bytes"]
    totals = package_manifest.get("totals") or {}
    if int(totals.get("packages") or -1) != len(expected) or int(totals.get("bytes") or -1) != total_bytes:
        raise ValueError("player PGN manifest totals mismatch")
    return {"packages": len(expected), "bytes": total_bytes}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-local-files", action="store_true")
    args = parser.parse_args()
    try:
        totals = validate(check_local_files=not args.no_local_files)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"PLAYER_PGN_R2_RECEIPT_INVALID: {error}", file=sys.stderr)
        return 1
    print(f"player PGN R2 receipt valid: packages={totals['packages']} bytes={totals['bytes']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
