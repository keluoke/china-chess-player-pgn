#!/usr/bin/env python3
"""Write the canonical metric contract and align the legacy index manifest."""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from public_metrics import DOCS_DATA, canonical_public_metrics, read_json  # noqa: E402
from snapshot_context import snapshot_id  # noqa: E402
from stable_json import write_json as write_stable_json  # noqa: E402

OUTPUT = DOCS_DATA / "public-metrics.json"
INDEX_MANIFEST = DOCS_DATA / "index" / "manifest.json"


def write_json(path: pathlib.Path, data: dict) -> None:
    write_stable_json(path, data, ensure_ascii=False, indent=1)


def enrich_index_manifest(index: dict, metrics: dict, *, snapshot: str = "") -> dict:
    enriched = dict(index)
    source_totals = dict(enriched.get("sourceTotals") or enriched.get("totals", {}))
    enriched["sourceTotals"] = source_totals
    enriched["totals"] = {
        **source_totals,
        "players": metrics["totals"]["playersWithGames"],
        "games": metrics["totals"]["games"],
    }
    enriched["metricContract"] = {
        "version": metrics["metricVersion"],
        "scope": metrics["scope"],
        "source": "data/public-metrics.json",
    }
    if snapshot:
        enriched["snapshotId"] = snapshot
    return enriched


def main() -> int:
    metrics = canonical_public_metrics()
    metrics["generatedAt"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    write_json(OUTPUT, metrics)

    index = enrich_index_manifest(
        read_json(INDEX_MANIFEST, {}) or {}, metrics, snapshot=snapshot_id()
    )
    write_json(INDEX_MANIFEST, index)
    print(json.dumps(metrics["totals"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
