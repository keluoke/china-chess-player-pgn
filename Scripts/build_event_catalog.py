#!/usr/bin/env python3
"""Build the public event catalog from committed source and index artifacts.

Events are first-class entities: a Chess-Results tournament ID is stable, while
its displayed title can vary by language and may be truncated by the upstream
player-search table.  Community-maintained Chinese names therefore live in
``data/community/tournament-name-mappings.csv`` and are only *read* here.
This script never writes back to crawler outputs or the registry.
"""

from __future__ import annotations

import csv
import hashlib
import json
import pathlib
from collections import defaultdict
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS_DATA = REPO_ROOT / "docs" / "data"
CATALOG = DOCS_DATA / "index" / "chess-results-tournaments.json"
BY_PLAYER = DOCS_DATA / "index" / "by-player"
OUTPUT = DOCS_DATA / "index" / "events.json"
MAPPINGS = REPO_ROOT / "data" / "community" / "tournament-name-mappings.csv"


def read_json(path: pathlib.Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def event_id(source: str, tournament_id: str, name: str, date: str) -> str:
    """Return a stable key even for non-Chess-Results PGN sources."""
    source_key = source.lower().replace(" ", "-")
    if tournament_id:
        return f"{source_key}:{tournament_id}"
    # The source/name/date combination is a fallback for providers without a
    # tournament ID. Use a digest instead of a raw string prefix: many Lichess
    # events share the same provider prefix, so a truncated hex encoding would
    # silently collide.
    seed = hashlib.sha256("|".join([source, name, date]).encode("utf-8")).hexdigest()[:16]
    return f"{source_key}:{seed}"


def load_mappings() -> dict[tuple[str, str], dict[str, str]]:
    result: dict[tuple[str, str], dict[str, str]] = {}
    if not MAPPINGS.exists():
        return result
    with MAPPINGS.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            source = clean(row.get("source"))
            tournament_id = clean(row.get("tournament_id"))
            if source and tournament_id:
                result[(source.lower(), tournament_id)] = {
                    "chineseName": clean(row.get("chinese_name")),
                    "evidenceURL": clean(row.get("evidence_url")),
                }
    return result


def static_event_stats() -> dict[tuple[str, str], dict[str, Any]]:
    """Collect actual archived PGN coverage from per-player static indexes."""
    result: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"players": set(), "pgnCount": 0, "gameCount": 0, "names": set(), "dates": set()}
    )
    for path in sorted(BY_PLAYER.glob("fide-*.json")):
        detail = read_json(path, {})
        fide_id = clean(detail.get("player", {}).get("fideID"))
        for event in detail.get("events", []):
            source = clean(event.get("source")) or "Static PGN"
            tournament_id = clean(event.get("tournamentID"))
            key = (source.lower(), tournament_id or f"name:{clean(event.get('name'))}|{clean(event.get('date'))}")
            row = result[key]
            if fide_id:
                row["players"].add(fide_id)
            row["pgnCount"] += 1
            row["gameCount"] += int(event.get("gameCount") or 0)
            if clean(event.get("name")):
                row["names"].add(clean(event["name"]))
            if clean(event.get("date")):
                row["dates"].add(clean(event["date"]))
    return result


def build_catalog() -> list[dict[str, Any]]:
    mappings = load_mappings()
    coverage = static_event_stats()
    events: dict[tuple[str, str], dict[str, Any]] = {}

    # The crawler catalog supplies all recorded Chess-Results participations,
    # including events for which an openly distributable PGN is unavailable.
    for upstream in read_json(CATALOG, []):
        source = clean(upstream.get("source")) or "Chess-Results"
        tournament_id = clean(upstream.get("tournamentID"))
        if not tournament_id:
            continue
        key = (source.lower(), tournament_id)
        mapping = mappings.get(key, {})
        stats = coverage.pop(key, {})
        name = clean(upstream.get("name"))
        chinese_name = mapping.get("chineseName", "")
        source_date = clean(upstream.get("date"))
        # When PGN is available, its EventDate is a direct record of the
        # played event and is more useful than a player-search row that may
        # represent a later rating/listing date.
        archived_dates = sorted(stats.get("dates", set()), reverse=True)
        date = archived_dates[0] if archived_dates else source_date
        players = sorted({clean(fid) for fid in upstream.get("players", []) if clean(fid)})
        players.extend(sorted(stats.get("players", set()) - set(players)))
        events[key] = {
            "id": event_id(source, tournament_id, name, date),
            "source": source,
            "tournamentID": tournament_id,
            "name": name,
            "chineseName": chinese_name or None,
            "displayName": chinese_name or name,
            "date": date or None,
            "sourceDate": source_date if source_date and source_date != date else None,
            "rounds": clean(upstream.get("rounds")) or None,
            "participants": clean(upstream.get("participants")) or None,
            "url": clean(upstream.get("url")) or None,
            "evidenceURL": mapping.get("evidenceURL") or None,
            "players": players,
            "playerCount": len(players),
            "pgnPlayerCount": len(stats.get("players", set())),
            "pgnCount": int(stats.get("pgnCount") or 0),
            "gameCount": int(stats.get("gameCount") or 0),
        }

    # Preserve event data sourced exclusively from PGN archives (for example
    # Lichess Broadcasts) without inventing a Chess-Results URL.
    for (source_key, key_id), stats in coverage.items():
        name = sorted(stats["names"])[0] if stats["names"] else "未命名赛事"
        date = sorted(stats["dates"], reverse=True)[0] if stats["dates"] else ""
        source = "Chess-Results" if source_key == "chess-results" else source_key.title()
        tournament_id = "" if key_id.startswith("name:") else key_id
        key = (source_key, key_id)
        events[key] = {
            "id": event_id(source, tournament_id, name, date),
            "source": source,
            "tournamentID": tournament_id or None,
            "name": name,
            "chineseName": None,
            "displayName": name,
            "date": date or None,
            "rounds": None,
            "participants": None,
            "url": None,
            "evidenceURL": None,
            "players": sorted(stats["players"]),
            "playerCount": len(stats["players"]),
            "pgnPlayerCount": len(stats["players"]),
            "pgnCount": int(stats["pgnCount"]),
            "gameCount": int(stats["gameCount"]),
        }

    return sorted(events.values(), key=lambda item: (item.get("date") or "", item["id"]), reverse=True)


def main() -> int:
    events = build_catalog()
    OUTPUT.write_text(json.dumps(events, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    mapped = sum(1 for event in events if event.get("chineseName"))
    print(json.dumps({"events": len(events), "mappedChineseNames": mapped}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
