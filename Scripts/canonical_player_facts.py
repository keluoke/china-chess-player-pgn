#!/usr/bin/env python3
"""Read and validate the canonical player fact datasets.

The fact manifests are deliberately required inputs for every downstream
projection.  A warm build must never fall back to a previous snapshot's
``by-player`` output when either manifest or its data file is absent.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
PLAYER_EVENT_FACTS = ROOT / "data/generated/player-event-facts/manifest.json"
PLAYER_GAME_FACTS = ROOT / "data/generated/player-game-facts/manifest.json"


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_required_json(path: pathlib.Path, label: str) -> Any:
    if not path.is_file():
        raise RuntimeError(f"REQUIRED_FACT_MANIFEST_MISSING: {label}: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"REQUIRED_FACT_MANIFEST_INVALID: {label}: {path}: {error}") from error


def load_fact_dataset(
    manifest_path: pathlib.Path,
    expected_kind: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return validated facts and their manifest.

    ``dataFile`` is resolved beside the manifest so temporary cold-build
    fixtures use the exact same contract as production.  When the sanctioned
    snapshot orchestrator exports ``SNAPSHOT_ID``, the manifest and data must
    match it; standalone diagnostic reads still verify mutual consistency.
    """
    manifest = read_required_json(manifest_path, expected_kind)
    if manifest.get("kind") != expected_kind:
        raise RuntimeError(
            f"FACT_MANIFEST_KIND_MISMATCH: expected {expected_kind}, "
            f"got {manifest.get('kind')!r}: {manifest_path}"
        )
    data_file = str(manifest.get("dataFile") or "").strip()
    if not data_file or pathlib.PurePosixPath(data_file).name != data_file:
        raise RuntimeError(f"FACT_MANIFEST_DATA_PATH_INVALID: {manifest_path}: {data_file!r}")
    data_path = manifest_path.parent / data_file
    if not data_path.is_file():
        raise RuntimeError(f"REQUIRED_FACT_DATA_MISSING: {expected_kind}: {data_path}")
    expected_sha = str(manifest.get("dataSha256") or "").strip()
    actual_sha = sha256_file(data_path)
    if len(expected_sha) != 64 or actual_sha != expected_sha:
        raise RuntimeError(
            f"FACT_DATA_HASH_MISMATCH: {expected_kind}: expected {expected_sha or '<missing>'}, "
            f"got {actual_sha}"
        )
    try:
        payload = json.loads(data_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"REQUIRED_FACT_DATA_INVALID: {expected_kind}: {data_path}: {error}") from error
    facts = payload.get("facts") if isinstance(payload, dict) else None
    if not isinstance(facts, list):
        raise RuntimeError(f"FACT_DATA_ROWS_INVALID: {expected_kind}: {data_path}")
    declared_rows = manifest.get("rows")
    if not isinstance(declared_rows, int) or declared_rows != len(facts):
        raise RuntimeError(
            f"FACT_DATA_ROW_COUNT_MISMATCH: {expected_kind}: declared {declared_rows!r}, "
            f"actual {len(facts)}"
        )
    manifest_sid = str(manifest.get("snapshotId") or "").strip()
    data_sid = str(payload.get("snapshotId") or "").strip()
    expected_sid = os.environ.get("SNAPSHOT_ID", "").strip()
    if not manifest_sid or manifest_sid != data_sid or (expected_sid and manifest_sid != expected_sid):
        raise RuntimeError(
            f"FACT_SNAPSHOT_MISMATCH: {expected_kind}: orchestrator={expected_sid or '<standalone>'}, "
            f"manifest={manifest_sid or '<missing>'}, data={data_sid or '<missing>'}"
        )
    return facts, manifest


def manifest_reference(manifest_path: pathlib.Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Small immutable reference embedded in downstream manifests."""
    try:
        relative = str(manifest_path.relative_to(ROOT))
    except ValueError:
        relative = str(manifest_path)
    return {
        "path": relative,
        "sha256": sha256_file(manifest_path),
        "snapshotId": manifest.get("snapshotId"),
        "rows": manifest.get("rows"),
    }
