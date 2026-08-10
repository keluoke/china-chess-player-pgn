#!/usr/bin/env python3
"""Reconcile durable per-event PGN source state from local machine facts.

This is offline: it never probes Chess-Results.  A complete captured pairing
set can prove that the source advertised no PGN links; existing archives prove
that PGN bytes were fetched/imported.  Network/empty failures recorded by
``fetch_event_pgn.py`` are preserved until a later successful archive replaces
them.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
from typing import Any

from fetch_event_pgn import split_games
from stable_json import write_json

ROOT = pathlib.Path(__file__).resolve().parents[1]
DETAILS = ROOT / "data" / "generated" / "chess-results-event-details"
ARCHIVES = ROOT / "data" / "generated" / "chess-results-event-pgn"
OUTPUT = ROOT / "data" / "generated" / "pgn-collection-status.json"
ATTEMPTS = ROOT / "data" / "generated" / "pgn-source-attempts"


def read_json(path: pathlib.Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def advertised_count(payload: dict[str, Any]) -> tuple[int, int]:
    pairings = [
        pairing
        for round_row in payload.get("rounds") or []
        for pairing in round_row.get("pairings") or []
    ]
    advertised = sum(bool(pairing.get("hasPGN") or pairing.get("pgnURL")) for pairing in pairings)
    return advertised, len(pairings)


def event_attempt(tid: str) -> dict[str, Any]:
    payload = read_json(ATTEMPTS / f"tnr{tid}.json", {})
    if str(payload.get("tournamentID") or "").strip() != tid:
        return {}
    return payload


def build() -> dict[str, Any]:
    previous = read_json(OUTPUT, {})
    previous_events = previous.get("events") if isinstance(previous.get("events"), dict) else {}
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    events: dict[str, dict[str, Any]] = {}
    for path in sorted(DETAILS.glob("tnr*.json")):
        payload = read_json(path, {})
        tid = str(payload.get("tournamentID") or "").strip()
        if not tid:
            continue
        archive = ARCHIVES / f"tnr{tid}.pgn"
        previous_row = previous_events.get(tid, {})
        attempt = event_attempt(tid)
        if attempt.get("attemptedAt") and str(attempt.get("attemptedAt")) >= str(previous_row.get("attemptedAt") or ""):
            previous_row = {**previous_row, **attempt}
        advertised, pairings = advertised_count(payload)
        if archive.is_file():
            games = len(split_games(archive.read_text(encoding="utf-8", errors="replace")))
            status = previous_row.get("status") if previous_row.get("status") in {"fetched", "imported"} else "imported"
            via_fide_event = bool(payload.get("fideEventID")) and not advertised
            row = {
                "tournamentID": tid,
                "status": status,
                "games": games,
                "expectedScope": "all-boards" if games else "unknown",
                "evidence": "validated-fide-event-archive" if via_fide_event else "validated-local-event-archive",
                **({"via": "fide-event-id"} if via_fide_event else {}),
                **({"attemptedAt": previous_row.get("attemptedAt")} if previous_row.get("attemptedAt") else {}),
            }
        elif advertised:
            status = previous_row.get("status") if previous_row.get("status") in {"fetch-failed", "empty-response"} else "advertised"
            row = {
                "tournamentID": tid,
                "status": status,
                "advertisedBoards": advertised,
                "expectedScope": "all-boards" if advertised == pairings else "selected-boards",
                "evidence": "captured-pairing-pages",
                **({key: previous_row.get(key) for key in ("attemptedAt", "errorCode") if previous_row.get(key)}),
            }
        elif pairings and previous_row.get("status") in {"fetch-failed", "empty-response"}:
            row = {
                "tournamentID": tid,
                "status": previous_row["status"],
                "expectedScope": "all-boards" if previous_row.get("via") == "fide-event-id" else "unknown",
                "evidence": (
                    "official-fide-event-pgn-advertised"
                    if previous_row.get("via") == "fide-event-id"
                    else "source-fetch-attempt"
                ),
                **({key: previous_row.get(key) for key in ("attemptedAt", "errorCode", "via") if previous_row.get(key)}),
            }
        elif pairings:
            row = {
                "tournamentID": tid,
                "status": "not-published",
                "expectedScope": "none",
                "errorCode": "SOURCE_PGN_NOT_PUBLISHED",
                "evidence": "complete-pairing-pages-without-pgn-links",
            }
        else:
            row = {
                "tournamentID": tid,
                "status": previous_row.get("status") or "unknown",
                "expectedScope": "unknown",
                **({key: previous_row.get(key) for key in ("attemptedAt", "errorCode") if previous_row.get(key)}),
            }
        events[tid] = row
    return {"schemaVersion": 1, "updatedAt": now, "events": events}


def main() -> int:
    previous = read_json(OUTPUT, {})
    previous_count = len(previous.get("events") or {})
    visible = len(list(DETAILS.glob("tnr*.json")))
    if previous_count and visible < previous_count:
        print(json.dumps({
            "skipped": "private event detail layer incomplete; keeping PGN source status",
            "visibleDetails": visible,
            "statusEvents": previous_count,
        }, ensure_ascii=False))
        return 0
    payload = build()
    write_json(OUTPUT, payload, ensure_ascii=False, indent=2)
    counts: dict[str, int] = {}
    for row in payload["events"].values():
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    print(json.dumps({"events": len(payload["events"]), "statuses": counts}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
