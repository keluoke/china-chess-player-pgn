#!/usr/bin/env python3
"""Archive monthly official-rating observations (plan §5, RatingObservation).

The registry only ever holds the *current* official ratings; the reference-
estimate model (plan §6) needs month-by-month anchors. Each run snapshots
the committed registry ratings into ``data/generated/rating-observations/``
keyed by list month. Within one month the file is refreshed (the registry
itself is the authority); previous months are immutable history and are
never rewritten.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "docs" / "data" / "registry" / "players.json"
OUTPUT_DIR = ROOT / "data" / "generated" / "rating-observations"

FIELDS = ["fide_id", "list_month", "standard", "rapid", "blitz", "inactive"]


def main() -> int:
    if not REGISTRY.exists():
        print("registry missing; nothing to archive")
        return 0
    players = json.loads(REGISTRY.read_text(encoding="utf-8"))
    month = dt.date.today().strftime("%Y-%m")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUTPUT_DIR / f"{month}.csv"
    rows = 0
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for player in players:
            fide_id = str(player.get("fideID") or "").strip()
            if not fide_id:
                continue
            standard, rapid, blitz = player.get("standard"), player.get("rapid"), player.get("blitz")
            if standard is None and rapid is None and blitz is None:
                continue
            writer.writerow({
                "fide_id": fide_id,
                "list_month": month,
                "standard": standard if standard is not None else "",
                "rapid": rapid if rapid is not None else "",
                "blitz": blitz if blitz is not None else "",
                "inactive": "1" if player.get("inactive") else "",
            })
            rows += 1
    months = sorted(p.stem for p in OUTPUT_DIR.glob("*.csv"))
    print(json.dumps({"month": month, "players": rows, "archivedMonths": months}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
