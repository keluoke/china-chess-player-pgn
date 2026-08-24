#!/usr/bin/env python3
"""Build public per-player participation history independent of PGN coverage.

The input records are tournament result facts keyed by FIDE ID.  PGN-backed
profile events are only used to annotate whether a playable game archive is
available; they are never allowed to define whether a player participated.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
from collections import defaultdict
from typing import Any

from build_event_catalog import TEST_NAME_RE, has_chinese_text
from canonical_player_facts import PLAYER_EVENT_FACTS, load_fact_dataset, manifest_reference
from snapshot_context import snapshot_id
from stable_json import write_json


ROOT = pathlib.Path(__file__).resolve().parents[1]
PUBLIC_EVENTS = ROOT / "docs/data/index/public-events.json"
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


def build_rows(
    event_facts: list[dict[str, Any]],
    catalog_path: pathlib.Path,
) -> dict[str, list[dict[str, Any]]]:
    catalog = load_catalog(catalog_path)
    today = dt.date.today().isoformat()
    by_player: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for fact in event_facts:
        fide_id = clean(fact.get("fideID"))
        tournament_id = clean(fact.get("tournamentID"))
        if not fide_id.isdigit() or not tournament_id:
            continue
        catalog_event = catalog.get(tournament_id, {})
        # Facts remain internal until the curated public catalog admits the
        # event.  No raw crawler CSV or previous by-player projection is read.
        if not catalog_event:
            continue
        game_count = int(fact.get("gameCount") or 0)
        event_id = clean(catalog_event.get("id"))
        name = clean(
            catalog_event.get("chineseName")
            or catalog_event.get("displayName")
            or catalog_event.get("name")
        ) or "未命名赛事"
        if TEST_NAME_RE.search(name) or not has_chinese_text(name):
            continue
        event_date = clean(catalog_event.get("date") or fact.get("date"))
        row = {
            **({"id": event_id} if event_id else {}),
            "tournamentID": tournament_id,
            "name": name,
            "date": event_date,
            "rank": clean(fact.get("rank")),
            "rounds": clean(fact.get("rounds")),
            "participants": clean(fact.get("participants")),
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


def write_output(
    rows: dict[str, list[dict[str, Any]]],
    output_root: pathlib.Path,
    fact_input: dict[str, Any] | None = None,
) -> None:
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
        **({"factInputs": {"playerEvents": fact_input}} if fact_input else {}),
        "totals": {
            "players": len(rows),
            "participations": sum(len(events) for events in rows.values()),
            "withPGN": sum(event.get("pgnStatus") == "available" for events in rows.values() for event in events),
        },
    })


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--player-event-facts", type=pathlib.Path, default=PLAYER_EVENT_FACTS)
    parser.add_argument("--public-events", type=pathlib.Path, default=PUBLIC_EVENTS)
    parser.add_argument("--output-root", type=pathlib.Path, default=OUTPUT_ROOT)
    args = parser.parse_args()
    facts, manifest = load_fact_dataset(args.player_event_facts, "player-event-facts")
    rows = build_rows(facts, args.public_events)
    write_output(rows, args.output_root, manifest_reference(args.player_event_facts, manifest))
    print(json.dumps({"players": len(rows), "participations": sum(map(len, rows.values()))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
