#!/usr/bin/env python3
"""Write the canonical metric contract and align the legacy index manifest."""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from public_metrics import DOCS_DATA, canonical_public_metrics, read_json  # noqa: E402

OUTPUT = DOCS_DATA / "public-metrics.json"
INDEX_MANIFEST = DOCS_DATA / "index" / "manifest.json"


def write_json(path: pathlib.Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def main() -> int:
    metrics = canonical_public_metrics()
    metrics["generatedAt"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    write_json(OUTPUT, metrics)

    index = read_json(INDEX_MANIFEST, {}) or {}
    original = dict(index.get("totals", {}))
    index["sourceTotals"] = original
    index["totals"] = {
        **original,
        "players": metrics["totals"]["playersWithGames"],
        "games": metrics["totals"]["games"],
    }
    index["metricContract"] = {
        "version": metrics["metricVersion"],
        "scope": metrics["scope"],
        "source": "data/public-metrics.json",
    }
    write_json(INDEX_MANIFEST, index)
    print(json.dumps(metrics["totals"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
