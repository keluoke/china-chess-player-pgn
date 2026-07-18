#!/usr/bin/env python3
"""Archive official-rating observations keyed by FIDE list month.

Review §6.2 contract: observations anchor to the rating list's effective
month, not the month this script happens to run. The registry manifest does
not yet carry an explicit FIDE list date, so the list month is derived from
the registry capture time (FIDE lists take effect on the 1st; a capture made
in month M carries the M list) and that derivation is recorded explicitly as
``effectiveDateSource`` — estimate/backtest layers must not treat it as a
verified list date until the registry sync records one.

Each month file also records the registry content hash and both timestamps
(capture and archive), so a wrong anchor can be detected and rebuilt.
Previous month files are immutable history and are never rewritten.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "docs" / "data" / "registry" / "players.json"
REGISTRY_MANIFEST = ROOT / "docs" / "data" / "registry" / "manifest.json"
OUTPUT_DIR = ROOT / "data" / "generated" / "rating-observations"

FIELDS = ["fide_id", "list_month", "standard", "rapid", "blitz", "inactive"]


def main() -> int:
    if not REGISTRY.exists():
        print("registry missing; nothing to archive")
        return 0
    registry_bytes = REGISTRY.read_bytes()
    registry_hash = hashlib.sha256(registry_bytes).hexdigest()
    players = json.loads(registry_bytes.decode("utf-8"))
    manifest = {}
    try:
        manifest = json.loads(REGISTRY_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    captured_at = str(manifest.get("generatedAt") or "")
    explicit_list_date = str(manifest.get("listDate") or manifest.get("effectiveDate") or "")
    if explicit_list_date[:7]:
        list_month = explicit_list_date[:7]
        effective_source = "registry-manifest-list-date"
    elif captured_at[:7]:
        list_month = captured_at[:7]
        effective_source = "assumed-from-capture-month"
    else:
        list_month = dt.date.today().strftime("%Y-%m")
        effective_source = "assumed-from-archive-run"

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUTPUT_DIR / f"{list_month}.csv"
    meta_target = OUTPUT_DIR / f"{list_month}.meta.json"

    # Same list month, same registry content → nothing to do. A different
    # registry hash within the month refreshes the file (registry is the
    # authority); other months stay immutable.
    if meta_target.exists():
        try:
            previous = json.loads(meta_target.read_text(encoding="utf-8"))
            if previous.get("registrySHA256") == registry_hash:
                print(json.dumps({"month": list_month, "unchanged": True}, ensure_ascii=False))
                return 0
        except (OSError, json.JSONDecodeError):
            pass

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
                "list_month": list_month,
                "standard": standard if standard is not None else "",
                "rapid": rapid if rapid is not None else "",
                "blitz": blitz if blitz is not None else "",
                "inactive": "1" if player.get("inactive") else "",
            })
            rows += 1
    meta_target.write_text(json.dumps({
        "schemaVersion": 2,
        "listMonth": list_month,
        "effectiveDateSource": effective_source,
        "registryCapturedAt": captured_at or None,
        "registrySHA256": registry_hash,
        "archivedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "players": rows,
        "controls": ["standard", "rapid", "blitz"],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    months = sorted(p.stem for p in OUTPUT_DIR.glob("*.csv"))
    print(json.dumps({
        "month": list_month, "players": rows,
        "effectiveDateSource": effective_source,
        "archivedMonths": months,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
