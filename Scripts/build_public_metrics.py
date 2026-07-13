#!/usr/bin/env python3
"""Write the canonical metric contract and align the legacy index manifest."""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from public_metrics import DOCS_DATA, canonical_public_metrics, read_json  # noqa: E402
from stable_json import write_json as write_stable_json  # noqa: E402

OUTPUT = DOCS_DATA / "public-metrics.json"
INDEX_MANIFEST = DOCS_DATA / "index" / "manifest.json"


def write_json(path: pathlib.Path, data: dict) -> None:
    write_stable_json(path, data, ensure_ascii=False, indent=1)


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
