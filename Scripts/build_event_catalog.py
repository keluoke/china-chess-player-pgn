#!/usr/bin/env python3
"""Build the event catalogs from committed source and index artifacts.

Two catalogs come out of this script:

- ``events.json``: the full internal catalog (every recorded participation,
  Lichess broadcast fragments included). This is evidence/audit surface only —
  product pages must not read it.
- ``public-events.json``: the curated public catalog. Only the four target
  series are admitted (棋协大师赛 / 李成智杯 / 世少赛 / 亚少赛), every entry
  carries structured fields (series/year/station/groupLabel/sex/ageGroup/
  level/tournamentID/date) and a display name that includes the group.

Community-maintained Chinese names live in
``data/community/tournament-name-mappings.csv`` and are only *read* here.
This script never writes back to crawler outputs or the registry.
"""

from __future__ import annotations

import csv
import datetime as dt
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
PUBLIC_OUTPUT = DOCS_DATA / "index" / "public-events.json"
CANONICAL_OUTPUT = DOCS_DATA / "index" / "canonical-events.json"
MAPPING_CANDIDATES = DOCS_DATA / "index" / "event-name-mapping-candidates.json"
DETAIL_GAPS = DOCS_DATA / "audit" / "event-detail-gaps.json"
EVENT_DETAILS = DOCS_DATA / "index" / "event-details" / "manifest.json"
EVENT_QUEUE = DOCS_DATA / "audit" / "domestic-event-queue.json"
MAPPINGS = REPO_ROOT / "data" / "community" / "tournament-name-mappings.csv"
MASTER_GROUPS = REPO_ROOT / "data" / "community" / "master-tournament-groups.csv"

# The public catalog admits exactly these four series.
SERIES_LABELS = {
    "chess-association-master": "全国国际象棋棋协大师赛",
    "lichengzhi-cup": "全国国际象棋青少年锦标赛（个人）暨李成智杯",
    "world-youth": "世界青少年国际象棋锦标赛",
    "asian-youth": "亚洲青少年国际象棋锦标赛",
}

MASTER_GROUP_LABELS = {
    "OPEN": "棋协大师组",
    "MEN_CANDIDATE": "男子候补棋协大师组",
    "WOMEN_CANDIDATE": "女子候补棋协大师组",
    "MEN_LEVEL_1": "男子一级棋士组",
    "WOMEN_LEVEL_1": "女子一级棋士组",
}

TEST_NAME_RE = re.compile(r"\btest\b|测试|演示|\bdemo\b", re.IGNORECASE)
WORLD_YOUTH_RE = re.compile(r"world\s+(youth|cadets?)", re.IGNORECASE)
ASIAN_YOUTH_RE = re.compile(r"asian\s+(youth|schools?|junior)", re.IGNORECASE)
GROUP_TOKEN_RE = re.compile(r"\(?\b(open\s+)?([UGB])\s?(\d{1,2})\b\)?", re.IGNORECASE)
EDITION_RE = re.compile(r"\b(\d{1,3})(?:st|nd|rd|th)\b", re.IGNORECASE)


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
# level="source-item" so product surfaces only treat level="event" rows as赛事.
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


def best_source_name(upstream_name: str, pgn_names: set[str]) -> tuple[str, list[str]]:
    """Pick the fullest event name and keep every observed variant as alias.

    The upstream player-search table truncates titles (~30 chars, e.g.
    "2025 China Youth Rapid Champio"); PGN headers usually carry the complete
    Event tag. Prefer the longest variant that extends the upstream prefix.
    """
    variants = {clean(n) for n in pgn_names if clean(n)}
    upstream_name = clean(upstream_name)
    if upstream_name:
        variants.add(upstream_name)
    if not variants:
        return "", []
    best = upstream_name or ""
    for candidate in sorted(variants, key=len, reverse=True):
        if not best or (len(candidate) > len(best) and candidate.lower().startswith(best[: max(10, len(best) - 4)].lower())):
            best = candidate
            break
    aliases = sorted(variants - {best})
    return best, aliases


def build_upstream_event(
    upstream: dict[str, Any],
    mapping: dict[str, str],
    master_group: dict[str, str],
    detail: dict[str, Any],
    stats: dict[str, Any],
) -> dict[str, Any]:
    """Pure assembly of one catalog event; no state leaks between calls."""
    source = clean(upstream.get("source")) or "Chess-Results"
    tournament_id = clean(upstream.get("tournamentID"))
    name, aliases = best_source_name(clean(upstream.get("name")), stats.get("names", set()))
    chinese_name = mapping.get("chineseName", "")
    canonical_event_id = mapping.get("canonicalEventID", "")
    source_date = clean(upstream.get("date"))
    # When PGN is available, its EventDate is a direct record of the played
    # event and is more useful than a player-search row that may represent a
    # later rating/listing date.
    archived_dates = sorted(stats.get("dates", set()), reverse=True)
    date = archived_dates[0] if archived_dates else source_date
    players = sorted({clean(fid) for fid in upstream.get("players", []) if clean(fid)})
    players.extend(sorted(stats.get("players", set()) - set(players)))
    event = {
        "id": event_id(source, tournament_id, name, date),
        "source": source,
        "tournamentID": tournament_id,
        "name": name,
        "aliases": aliases or None,
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
    if detail:
        event["detailPath"] = detail.get("path")
        event["coverageScope"] = "domestic-full"
        event["standingCount"] = detail.get("standingCount")
        if detail.get("roundsPendingVerification"):
            event["roundsPendingVerification"] = True
    if canonical_event_id:
        event["canonicalEventID"] = canonical_event_id
        event["sourceRefs"] = [{"source": source, "tournamentID": tournament_id, "url": event["url"]}]
    if master_group:
        event["sectionID"] = master_group.get("section_id") or None
        event["groupCode"] = master_group.get("group_code") or None
        event["station"] = master_group.get("station") or None
    return event


def build_mapping_only_event(
    source_key: str,
    tournament_id: str,
    mapping: dict[str, str],
    master_group: dict[str, str],
    detail: dict[str, Any],
) -> dict[str, Any]:
    """Pure assembly of a reviewed-mapping-only event (no crawler row yet)."""
    source = "Chess-Results" if source_key == "chess-results" else source_key.title()
    chinese_name = mapping.get("chineseName") or ""
    year = master_group.get("year") or ""
    event = {
        "id": event_id(source, tournament_id, chinese_name, year),
        "source": source,
        "tournamentID": tournament_id,
        "canonicalEventID": mapping.get("canonicalEventID") or None,
        "name": chinese_name,
        "chineseName": chinese_name,
        "displayName": chinese_name or f"{source} {tournament_id}",
        # The master list identifies the season, not an exact start date.
        # Do not invent January 1st: consumers can display the year until the
        # direct Chess-Results sync supplies authoritative dates.
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
    if detail:
        event["detailPath"] = detail.get("path")
        event["coverageScope"] = "domestic-full"
        event["standingCount"] = detail.get("standingCount")
    return event


def build_pgn_only_event(source_key: str, key_id: str, stats: dict[str, Any]) -> dict[str, Any]:
    """Pure assembly of an event known only from PGN archives."""
    name = sorted(stats["names"])[0] if stats["names"] else "未命名赛事"
    date = sorted(stats["dates"], reverse=True)[0] if stats["dates"] else ""
    source = "Chess-Results" if source_key == "chess-results" else source_key.title()
    tournament_id = "" if key_id.startswith("name:") else key_id
    return {
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
        tournament_id = clean(upstream.get("tournamentID"))
        if not tournament_id:
            continue
        source = clean(upstream.get("source")) or "Chess-Results"
        key = (source.lower(), tournament_id)
        events[key] = build_upstream_event(
            upstream,
            mappings.get(key, {}),
            master_groups.get(tournament_id, {}),
            details.get(tournament_id, {}),
            coverage.pop(key, {}),
        )

    # A reviewed mapping is useful metadata even before the player crawler has
    # discovered participants. Keep these sections visible and let later
    # player/PGN refreshes enrich the same stable source key.
    for (source_key, tournament_id), mapping in mappings.items():
        key = (source_key, tournament_id)
        if key in events:
            continue
        events[key] = build_mapping_only_event(
            source_key,
            tournament_id,
            mapping,
            master_groups.get(tournament_id, {}),
            details.get(tournament_id, {}),
        )

    # Preserve event data sourced exclusively from PGN archives (for example
    # Lichess Broadcasts) without inventing a Chess-Results URL.
    for (source_key, key_id), stats in coverage.items():
        events[(source_key, key_id)] = build_pgn_only_event(source_key, key_id, stats)

    return sorted(events.values(), key=lambda item: (item.get("date") or "", item["id"]), reverse=True)


# --- curated public catalog --------------------------------------------------


def classify_series(event: dict[str, Any]) -> str | None:
    canonical = str(event.get("canonicalEventID") or "")
    if canonical.startswith("chess-association-master"):
        return "chess-association-master"
    if canonical.startswith("lichengzhi-cup"):
        return "lichengzhi-cup"
    haystack = " ".join(filter(None, [
        event.get("name"), event.get("displayName"), *(event.get("aliases") or []),
    ]))
    if WORLD_YOUTH_RE.search(haystack):
        return "world-youth"
    if ASIAN_YOUTH_RE.search(haystack):
        return "asian-youth"
    return None


def parse_group_token(text: str) -> tuple[str | None, str | None, str | None]:
    """Extract (groupLabel, sex, ageGroup) from a source title like '(U14)'
    or 'Open U12' or '亚洲青少年…G10'. B/U → open/boys, G → girls."""
    match = GROUP_TOKEN_RE.search(text or "")
    if not match:
        return None, None, None
    letter = match.group(2).upper()
    age = match.group(3)
    sex = "F" if letter == "G" else "M"
    label = f"{'女子' if sex == 'F' else '男子'}U{age}组" if letter != "G" else f"女子U{age}组"
    return label, sex, f"U{age}"


def parse_chinese_group(chinese_name: str) -> tuple[str | None, str | None, str | None]:
    """Extract group info from a reviewed Chinese title, e.g.
    '…暨第30届李成智杯 U12男子组' → ('U12男子组', 'M', 'U12')."""
    match = re.search(r"([UG]\d{1,2})?\s*(男子|女子)?\s*(青年|甲|乙|丙)?组", chinese_name or "")
    if not match or not any(match.groups()):
        return None, None, None
    label = clean(match.group(0))
    sex = {"男子": "M", "女子": "F"}.get(match.group(2) or "")
    age = match.group(1)
    if age and age.upper().startswith("G"):
        sex = sex or "F"
        age = "U" + age[1:]
    return label, sex, age


def public_event(event: dict[str, Any], series: str, master_group: dict[str, str]) -> dict[str, Any] | None:
    """Project one internal event into the structured public-catalog shape.

    Pure function; returns None when required structured fields cannot be
    established (those are reported by the audit, never silently admitted).
    """
    tournament_id = clean(event.get("tournamentID"))
    if not tournament_id:
        return None
    date = event.get("date")
    year = (date or "")[:4] or clean(master_group.get("year")) or (str(event.get("year") or ""))[:4]
    if not year:
        canonical = str(event.get("canonicalEventID") or "")
        match = re.search(r"(\d{4})", canonical)
        year = match.group(1) if match else ""
    if not year:
        return None

    station = clean(master_group.get("station")) or None
    group_label = sex = age_group = None
    level = clean(master_group.get("level")) or None
    edition = None

    if series == "chess-association-master":
        code = clean(master_group.get("group_code"))
        group_label = MASTER_GROUP_LABELS.get(code)
        sex = clean(master_group.get("sex")) or None
        display = f"{year}年全国国际象棋棋协大师赛（{station}）{group_label}" if station and group_label else None
    elif series == "lichengzhi-cup":
        chinese = event.get("chineseName") or ""
        group_label, sex, age_group = parse_chinese_group(chinese)
        display = chinese or None
    else:  # world-youth / asian-youth
        haystack = " ".join(filter(None, [event.get("name"), *(event.get("aliases") or [])]))
        group_label, sex, age_group = parse_group_token(haystack)
        edition_match = EDITION_RE.search(haystack)
        edition = f"第{edition_match.group(1)}届" if edition_match else None
        display = event.get("chineseName") or event.get("name")
        if display and group_label and group_label not in display and (age_group or "") not in display:
            display = f"{display}（{group_label}）"

    display_name = display or event.get("displayName") or event.get("name")
    if group_label and group_label not in (display_name or ""):
        display_name = f"{display_name}（{group_label}）"

    return {
        "id": event.get("id"),
        "series": series,
        "seriesLabel": SERIES_LABELS[series],
        "year": year,
        "edition": edition,
        "station": station,
        "groupLabel": group_label,
        "sex": sex,
        "ageGroup": age_group,
        "level": level,
        "tournamentID": tournament_id,
        "date": date,
        "displayName": display_name,
        "name": event.get("name") or None,
        "chineseName": event.get("chineseName"),
        "aliases": event.get("aliases"),
        "url": event.get("url"),
        "rounds": event.get("rounds"),
        "participants": event.get("participants"),
        "playerCount": event.get("playerCount"),
        "gameCount": event.get("gameCount"),
        "detailStatus": "published" if event.get("detailPath") else "missing-detail",
        "detailPath": event.get("detailPath"),
        "roundsPendingVerification": event.get("roundsPendingVerification") or None,
        "canonicalEventID": event.get("canonicalEventID"),
    }


def excluded_from_public(event: dict[str, Any], today: str) -> str | None:
    """Build-layer exclusion filter for the public catalog (P1)."""
    name = " ".join(filter(None, [event.get("name"), event.get("displayName")]))
    if TEST_NAME_RE.search(name):
        return "test-or-demo-name"
    date = event.get("date") or ""
    if date and date > (dt.date.fromisoformat(today) + dt.timedelta(days=200)).isoformat():
        return "implausible-future-date"
    if not date and not event.get("chineseName") and not event.get("canonicalEventID"):
        return "undated-and-unmapped"
    return None


def public_catalog(events: list[dict[str, Any]], master_groups: dict[str, dict[str, str]],
                   today: str | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    today = today or dt.date.today().isoformat()
    rows: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for event in events:
        if (event.get("level") or "event") != "event":
            continue
        series = classify_series(event)
        if not series:
            continue
        reason = excluded_from_public(event, today)
        if reason:
            excluded.append({"tournamentID": event.get("tournamentID"), "id": event.get("id"), "reason": reason})
            continue
        projected = public_event(event, series, master_groups.get(clean(event.get("tournamentID")), {}))
        if projected is None:
            excluded.append({"tournamentID": event.get("tournamentID"), "id": event.get("id"), "reason": "missing-structured-fields"})
            continue
        rows.append(projected)
    rows.sort(key=lambda item: (item.get("date") or "", item.get("id") or ""), reverse=True)
    return rows, excluded


def detail_gap_audit(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """TNRs with evidence (snapshot/PGN/mapping) but no published detail.

    A missing detail must be a visible audit row, never a silent downgrade to
    metadata-only.
    """
    queue_status: dict[str, dict[str, Any]] = {}
    for target in (read_json(EVENT_QUEUE, {}) or {}).get("targets", []):
        tid = clean(target.get("tournamentID"))
        if tid:
            queue_status[tid] = target
    gaps = []
    for event in events:
        tid = clean(event.get("tournamentID"))
        if not tid or event.get("source") != "Chess-Results":
            continue
        if event.get("detailPath"):
            continue
        queue = queue_status.get(tid, {})
        has_snapshot = bool(queue.get("snapshotAudited") or queue.get("capturedPlayers"))
        has_pgn = int(event.get("gameCount") or 0) > 0
        has_mapping = bool(event.get("chineseName") or event.get("canonicalEventID"))
        if not (has_snapshot or has_pgn or has_mapping):
            continue
        gaps.append({
            "tournamentID": tid,
            "displayName": event.get("displayName"),
            "hasSnapshot": has_snapshot,
            "hasPGN": has_pgn,
            "hasMapping": has_mapping,
            "queueStatus": queue.get("ingestionStatus") or None,
            "reason": "detail-not-published",
        })
    gaps.sort(key=lambda item: item["tournamentID"])
    return gaps


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
    public_rows, excluded = public_catalog(events, load_master_groups())
    PUBLIC_OUTPUT.write_text(json.dumps({
        "schemaVersion": 1,
        "series": SERIES_LABELS,
        "totals": {
            "events": len(public_rows),
            "withDetail": sum(1 for row in public_rows if row["detailStatus"] == "published"),
            "excluded": len(excluded),
        },
        "excluded": excluded,
        "events": public_rows,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    gaps = detail_gap_audit(events)
    DETAIL_GAPS.parent.mkdir(parents=True, exist_ok=True)
    DETAIL_GAPS.write_text(json.dumps({
        "schemaVersion": 1,
        "totals": {"gaps": len(gaps)},
        "gaps": gaps,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    candidates = mapping_candidates(events)
    MAPPING_CANDIDATES.write_text(json.dumps(candidates, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    mapped = sum(1 for event in events if event.get("chineseName"))
    print(json.dumps({
        "events": len(events),
        "publicEvents": len(public_rows),
        "publicExcluded": len(excluded),
        "detailGaps": len(gaps),
        "canonicalEvents": len(canonical),
        "mappedChineseNames": mapped,
        "mappingCandidates": len(candidates),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
