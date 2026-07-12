#!/usr/bin/env python3
"""Append a data changelog entry (docs/data/changelog.json) when totals move.

Runs at the end of every index rebuild. Pure computation.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from public_metrics import canonical_public_metrics  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS_DATA = REPO_ROOT / "docs" / "data"
CHANGELOG = DOCS_DATA / "changelog.json"
MAX_ENTRIES = 200


def read_json(path: pathlib.Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def current_totals() -> dict:
    return canonical_public_metrics()["totals"]


def main() -> int:
    data = read_json(CHANGELOG, {"entries": []}) or {"entries": []}
    entries = data.get("entries", [])
    totals = current_totals()
    last = entries[0]["totals"] if entries else {}
    if totals == last:
        print(json.dumps({"changed": False}))
        return 0
    delta = {
        k: (totals.get(k) or 0) - (last.get(k) or 0)
        for k in totals
        if totals.get(k) is not None
    }
    entries.insert(0, {
        "date": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "totals": totals,
        "delta": delta,
    })
    CHANGELOG.write_text(
        json.dumps({"entries": entries[:MAX_ENTRIES]}, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"changed": True, "delta": delta}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
