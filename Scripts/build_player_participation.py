#!/usr/bin/env python3
"""Build public per-player participation history independent of PGN coverage.

The input records are tournament result facts keyed by FIDE ID.  PGN-backed
profile events are only used to annotate whether a playable game archive is
available; they are never allowed to define whether a player participated.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import pathlib
from collections import defaultdict
from typing import Any

from build_event_catalog import TEST_NAME_RE, has_chinese_text
from snapshot_context import snapshot_id
from stable_json import write_json


ROOT = pathlib.Path(__file__).resolve().parents[1]
PLAYER_EVENTS = ROOT / "data/generated/chess-results-player-events.csv"
PUBLIC_EVENTS = ROOT / "docs/data/index/public-events.json"
PLAYER_DETAILS = ROOT / "docs/data/index/players"
OUTPUT_ROOT = ROOT / "docs/data/index/player-participation"


def clean(value: Any) -> str:
    return str(value or "").strip().rstrip(",")


def bucket_for(fide_id: str) -> str:
    return f"{int(fide_id) % 256:02x}"


def load_catalog(path: pathlib.Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        clean(row.get("tournamentID")): row
        for row in payload.get("events", [])
        if clean(row.get("tournamentID"))
    }


def load_pgn_coverage(root: pathlib.Path) -> dict[tuple[str, str], int]:
    coverage: dict[tuple[str, str], int] = {}
    if not root.is_dir():
        return coverage
    for path in root.glob("fide-*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        fide_id = clean(payload.get("fideID")) or path.stem.removeprefix("fide-")
        for event in payload.get("events", []) or []:
            tournament_id = clean(event.get("tournamentID"))
            if not tournament_id:
                continue
            key = (fide_id, tournament_id)
            coverage[key] = max(coverage.get(key, 0), int(event.get("gameCount") or 0))
    return coverage


def build_rows(
    player_events_path: pathlib.Path,
    catalog_path: pathlib.Path,
    player_details_root: pathlib.Path,
) -> dict[str, list[dict[str, Any]]]:
    catalog = load_catalog(catalog_path)
    pgn_coverage = load_pgn_coverage(player_details_root)
    today = dt.date.today().isoformat()
    by_player: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    with player_events_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for raw in csv.DictReader(handle):
            fide_id = clean(raw.get("fide_id"))
            tournament_id = clean(raw.get("tnrid"))
            if not fide_id.isdigit() or not tournament_id:
                continue
            catalog_event = catalog.get(tournament_id, {})
            # The public player history follows the curated event catalog.
            # Raw player-search rows are often duplicated/truncated English
            # source titles and may include parser tests; they remain evidence
            # inputs but never become frontend rows on their own.
            if not catalog_event:
                continue
            game_count = pgn_coverage.get((fide_id, tournament_id), 0)
            event_id = clean(catalog_event.get("id"))
            name = clean(
                catalog_event.get("chineseName")
                or catalog_event.get("displayName")
                or catalog_event.get("name")
            ) or "未命名赛事"
            if TEST_NAME_RE.search(name) or not has_chinese_text(name):
                continue
            event_date = clean(catalog_event.get("date") or raw.get("end_date"))
            row = {
                **({"id": event_id} if event_id else {}),
                "tournamentID": tournament_id,
                "name": name,
                "date": event_date,
                "rank": clean(raw.get("rank")),
                "rounds": clean(raw.get("rounds")),
                "participants": clean(raw.get("participants")),
                "resultStatus": "scheduled" if event_date and event_date > today else "recorded",
                "pgnStatus": "available" if game_count else "not-archived",
                "gameCount": game_count,
                "cataloged": bool(event_id),
            }
            existing = by_player[fide_id].get(tournament_id)
            if existing is None or sum(bool(v) for v in row.values()) > sum(bool(v) for v in existing.values()):
                by_player[fide_id][tournament_id] = row
    return {
        fide_id: sorted(events.values(), key=lambda row: (row.get("date", ""), row["tournamentID"]), reverse=True)
        for fide_id, events in by_player.items()
    }


def write_output(rows: dict[str, list[dict[str, Any]]], output_root: pathlib.Path) -> None:
    sid = snapshot_id()
    generated_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    buckets: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(dict)
    for fide_id, events in rows.items():
        buckets[bucket_for(fide_id)][fide_id] = events
    bucket_root = output_root / "buckets"
    bucket_root.mkdir(parents=True, exist_ok=True)
    expected: set[pathlib.Path] = set()
    for bucket, players in sorted(buckets.items()):
        path = bucket_root / f"{bucket}.json"
        expected.add(path)
        write_json(path, {
            "schemaVersion": 1,
            "snapshotId": sid,
            "generatedAt": generated_at,
            "players": players,
        }, separators=(",", ":"))
    for stale in bucket_root.glob("*.json"):
        if stale not in expected:
            stale.unlink()
    write_json(output_root / "manifest.json", {
        "schemaVersion": 1,
        "snapshotId": sid,
        "generatedAt": generated_at,
        "storage": {"buckets": "data/index/player-participation/buckets/{bucket}.json"},
        "bucketRule": "integer FIDE ID modulo 256, lower-case hex",
        "totals": {
            "players": len(rows),
            "participations": sum(len(events) for events in rows.values()),
            "withPGN": sum(event.get("pgnStatus") == "available" for events in rows.values() for event in events),
        },
    })


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--player-events", type=pathlib.Path, default=PLAYER_EVENTS)
    parser.add_argument("--public-events", type=pathlib.Path, default=PUBLIC_EVENTS)
    parser.add_argument("--player-details", type=pathlib.Path, default=PLAYER_DETAILS)
    parser.add_argument("--output-root", type=pathlib.Path, default=OUTPUT_ROOT)
    args = parser.parse_args()
    if not args.player_events.is_file():
        raise SystemExit(f"missing player-event facts: {args.player_events}")
    rows = build_rows(args.player_events, args.public_events, args.player_details)
    write_output(rows, args.output_root)
    print(json.dumps({"players": len(rows), "participations": sum(map(len, rows.values()))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
