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
import re
from collections import defaultdict
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS_DATA = REPO_ROOT / "docs" / "data"
CATALOG = DOCS_DATA / "index" / "chess-results-tournaments.json"
BY_PLAYER = DOCS_DATA / "index" / "by-player"
OUTPUT = DOCS_DATA / "index" / "events.json"
CANONICAL_OUTPUT = DOCS_DATA / "index" / "canonical-events.json"
MAPPING_CANDIDATES = DOCS_DATA / "index" / "event-name-mapping-candidates.json"
EVENT_DETAILS = DOCS_DATA / "index" / "event-details" / "manifest.json"
MAPPINGS = REPO_ROOT / "data" / "community" / "tournament-name-mappings.csv"
MASTER_GROUPS = REPO_ROOT / "data" / "community" / "master-tournament-groups.csv"


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
                    "canonicalEventID": clean(row.get("canonical_event_id")),
                    "chineseName": clean(row.get("chinese_name")),
                    "evidenceURL": clean(row.get("evidence_url")),
                }
    return result


def load_master_groups() -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    if not MASTER_GROUPS.exists():
        return result
    with MASTER_GROUPS.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            tid = clean(row.get("tournament_id"))
            if tid:
                result[tid] = {key: clean(value) for key, value in row.items()}
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


# Event hierarchy: canonical event → section/group → round → game.
# PGN-header derived rows from broadcast archives are frequently a single
# round/board/game title ("Round 6: A - B"), i.e. crawl/evidence units — NOT
# tournaments. They stay in the catalog for provenance but are tagged
# level="source-item" so product surfaces (dashboard counts, 最新赛事, event
# search) only treat level="event" rows as赛事.
ROUND_ITEM_RE = re.compile(
    r"^\s*(round|rd\.?|game|board|tiebreak)\s*\d+\s*([:.\-–—]|$)", re.IGNORECASE
)


def classify_level(source: str, name: str, game_count: int, player_count: int) -> str:
    if ROUND_ITEM_RE.match(name or ""):
        return "source-item"
    if str(source).lower().startswith("lichess") and game_count <= 4 and player_count <= 4:
        # Untitled/singleton broadcast fragments without a recognizable
        # tournament aggregate are evidence units, not events.
        return "source-item"
    return "event"


def build_catalog() -> list[dict[str, Any]]:
    mappings = load_mappings()
    master_groups = load_master_groups()
    coverage = static_event_stats()
    details = {
        clean(item.get("tournamentID")): item
        for item in read_json(EVENT_DETAILS, {}).get("events", [])
        if clean(item.get("tournamentID"))
    }
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
        canonical_event_id = mapping.get("canonicalEventID", "")
        event_detail = details.get(tournament_id, {})
        master_group = master_groups.get(tournament_id, {})
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
            "level": "event",
        }
        if event_detail:
            events[key]["detailPath"] = event_detail.get("path")
            events[key]["coverageScope"] = "domestic-full"
            events[key]["standingCount"] = event_detail.get("standingCount")
        if canonical_event_id:
            events[key]["canonicalEventID"] = canonical_event_id
            events[key]["sourceRefs"] = [{"source": source, "tournamentID": tournament_id, "url": clean(upstream.get("url")) or None}]
        if master_group:
            events[key]["sectionID"] = master_group.get("section_id")
            events[key]["groupCode"] = master_group.get("group_code")
            events[key]["station"] = master_group.get("station")

    # A reviewed mapping is useful metadata even before the player crawler has
    # discovered participants. Keep these sections visible and let later
    # player/PGN refreshes enrich the same stable source key.
    for (source_key, tournament_id), mapping in mappings.items():
        key = (source_key, tournament_id)
        if key in events:
            continue
        source = "Chess-Results" if source_key == "chess-results" else source_key.title()
        master_group = master_groups.get(tournament_id, {})
        chinese_name = mapping.get("chineseName") or ""
        year = master_group.get("year") or ""
        events[key] = {
            "id": event_id(source, tournament_id, chinese_name, year),
            "source": source,
            "tournamentID": tournament_id,
            "canonicalEventID": mapping.get("canonicalEventID") or None,
            "name": chinese_name,
            "chineseName": chinese_name,
            "displayName": chinese_name or f"{source} {tournament_id}",
            # The master list identifies the season, not an exact start date.
            # Do not invent January 1st: consumers can display the year until
            # the direct Chess-Results sync supplies authoritative dates.
            "date": None,
            "year": year or None,
            "url": mapping.get("evidenceURL") or None,
            "evidenceURL": mapping.get("evidenceURL") or None,
            "sourceRefs": [{"source": source, "tournamentID": tournament_id, "url": mapping.get("evidenceURL") or None}],
            "players": [],
            "playerCount": 0,
            "pgnPlayerCount": 0,
            "pgnCount": 0,
            "gameCount": 0,
            "coverageScope": "metadata-only",
            "sectionID": master_group.get("section_id") or None,
            "groupCode": master_group.get("group_code") or None,
            "station": master_group.get("station") or None,
            "level": "event",
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
            "level": classify_level(source, name, int(stats["gameCount"]), len(stats["players"])),
        }

    return sorted(events.values(), key=lambda item: (item.get("date") or "", item["id"]), reverse=True)


def canonical_catalog(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        if event.get("canonicalEventID"):
            grouped[event["canonicalEventID"]].append(event)
    result: list[dict[str, Any]] = []
    for canonical_id, sections in grouped.items():
        players = sorted({fide_id for section in sections for fide_id in section.get("players", [])})
        refs = [ref for section in sections for ref in section.get("sourceRefs", [])]
        chinese_names = [section.get("chineseName") for section in sections if section.get("chineseName")]
        result.append({
            "id": canonical_id,
            "canonicalEventID": canonical_id,
            "displayName": canonical_display_name(canonical_id, sections, chinese_names),
            "date": max((section.get("date") or "" for section in sections), default="") or None,
            "sections": [{
                "id": section["id"],
                "tournamentID": section.get("tournamentID"),
                "displayName": section.get("displayName"),
                "playerCount": section.get("playerCount"),
                "gameCount": section.get("gameCount"),
            } for section in sections],
            "sourceRefs": refs,
            "players": players,
            "playerCount": len(players),
            "gameCount": sum(int(section.get("gameCount") or 0) for section in sections),
        })
    return sorted(result, key=lambda item: (item.get("date") or "", item["id"]), reverse=True)


def canonical_display_name(canonical_id: str, sections: list[dict[str, Any]], chinese_names: list[str]) -> str:
    match = re.fullmatch(r"lichengzhi-cup-(\d{4})", canonical_id)
    if match:
        return f"{match.group(1)}年全国国际象棋青少年锦标赛（个人）暨李成智杯"
    return chinese_names[0] if chinese_names else sections[0].get("displayName") or canonical_id


def mapping_candidates(events: list[dict[str, Any]], limit: int = 500) -> list[dict[str, Any]]:
    candidates = [event for event in events if event.get("source") == "Chess-Results" and not event.get("chineseName")]
    candidates.sort(key=lambda event: (event.get("date") or "", event.get("playerCount") or 0), reverse=True)
    return [{
        "source": event.get("source"),
        "tournamentID": event.get("tournamentID"),
        "sourceName": event.get("name"),
        "date": event.get("date"),
        "chinesePlayerCount": event.get("playerCount"),
        "sourceURL": event.get("url"),
        "reviewStatus": "needs-mapping",
    } for event in candidates[:limit]]


def main() -> int:
    events = build_catalog()
    OUTPUT.write_text(json.dumps(events, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    canonical = canonical_catalog(events)
    CANONICAL_OUTPUT.write_text(json.dumps(canonical, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    candidates = mapping_candidates(events)
    MAPPING_CANDIDATES.write_text(json.dumps(candidates, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    mapped = sum(1 for event in events if event.get("chineseName"))
    print(json.dumps({"events": len(events), "canonicalEvents": len(canonical), "mappedChineseNames": mapped, "mappingCandidates": len(candidates)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
