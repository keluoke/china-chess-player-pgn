#!/usr/bin/env python3
"""Append a data changelog entry (docs/data/changelog.json) when totals move.

Runs at the end of every index rebuild. Pure computation.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS_DATA = REPO_ROOT / "docs" / "data"
CHANGELOG = DOCS_DATA / "changelog.json"
MAX_ENTRIES = 200


def read_json(path: pathlib.Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def current_totals() -> dict:
    registry = read_json(DOCS_DATA / "registry" / "manifest.json", {}) or {}
    by_player = read_json(DOCS_DATA / "index" / "by-player" / "manifest.json", {}) or {}
    reg_totals = registry.get("totals", {})
    bp_totals = by_player.get("totals", {})
    return {
        "players": reg_totals.get("players"),
        "withChineseName": reg_totals.get("withChineseName"),
        "playersWithGames": bp_totals.get("players"),
        "games": bp_totals.get("games"),
    }


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
