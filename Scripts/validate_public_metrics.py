#!/usr/bin/env python3
"""Fail CI when public manifests disagree about PGN coverage totals."""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def main() -> int:
    metrics = load("docs/data/public-metrics.json")
    expected = metrics["totals"]
    index = load("docs/data/index/manifest.json")["totals"]
    api = load("docs/api/v1/manifest.json")["totals"]
    changelog = load("docs/data/changelog.json")["entries"][0]["totals"]
    dashboard = load("docs/data/dashboard.json")["totals"]
    observed = {
        "index": (index.get("players"), index.get("games")),
        "api": (api.get("withGameData"), api.get("games")),
        "changelog": (changelog.get("playersWithGames"), changelog.get("games")),
        "dashboard": (dashboard.get("playersWithGames"), dashboard.get("games")),
    }
    target = (expected.get("playersWithGames"), expected.get("games"))
    mismatches = {name: value for name, value in observed.items() if value != target}
    if mismatches:
        print(json.dumps({"expected": target, "mismatches": mismatches}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"ok": True, "playersWithGames": target[0], "games": target[1]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
