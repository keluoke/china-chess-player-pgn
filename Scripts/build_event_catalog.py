#!/usr/bin/env python3
"""Build the event catalogs from committed source and index artifacts.

Two catalogs come out of this script:

- ``events.json``: the full internal catalog (every recorded participation,
  Lichess broadcast fragments included). This is evidence/audit surface only —
  product pages must not read it.
- ``public-events.json``: the public lookup catalog. The four target series
  retain their structured classification, while every published event detail
  and event-level PGN archive receives a neutral, deep-linkable record.

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

from snapshot_context import stamp


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS_DATA = REPO_ROOT / "docs" / "data"
CATALOG = REPO_ROOT / "data" / "generated" / "chess-results-tournaments.json"
BY_PLAYER = DOCS_DATA / "index" / "by-player"
OUTPUT = REPO_ROOT / "data" / "generated" / "events-catalog.json"
PUBLIC_OUTPUT = DOCS_DATA / "index" / "public-events.json"
CANONICAL_OUTPUT = DOCS_DATA / "index" / "canonical-events.json"
MAPPING_CANDIDATES = REPO_ROOT / "data" / "generated" / "event-name-mapping-candidates.json"
DETAIL_GAPS = DOCS_DATA / "audit" / "event-detail-gaps.json"
EVENT_DETAILS = DOCS_DATA / "index" / "event-details" / "manifest.json"
EVENT_QUEUE = REPO_ROOT / "data" / "generated" / "audit" / "domestic-event-queue.json"
MAPPINGS = REPO_ROOT / "data" / "community" / "tournament-name-mappings.csv"
MASTER_GROUPS = REPO_ROOT / "data" / "community" / "master-tournament-groups.csv"

# The first four series remain the curated product groups. ``other`` closes
# the published-detail/catalog gap; ``archive`` exposes event-level PGN
# history without promoting round/chapter fragments to tournaments.
SERIES_LABELS = {
    "chess-association-master": "全国国际象棋棋协大师赛",
    "lichengzhi-cup": "全国国际象棋青少年锦标赛（个人）暨李成智杯",
    "world-youth": "世界青少年国际象棋锦标赛",
    "asian-youth": "亚洲青少年国际象棋锦标赛",
    "other": "其他已收录赛事",
    "archive": "棋谱归档赛事",
}

MASTER_GROUP_LABELS = {
    "OPEN": "棋协大师组",
    "MEN_CANDIDATE": "男子候补棋协大师组",
    "WOMEN_CANDIDATE": "女子候补棋协大师组",
    "MEN_LEVEL_1": "男子一级棋士组",
    "WOMEN_LEVEL_1": "女子一级棋士组",
}

MASTER_STATION_TRANSLATIONS = {
    "anji": "安吉站",
    "beijing": "北京站",
    "bengbu": "蚌埠站",
    "chengmai": "澄迈站",
    "chongqing": "重庆站",
    "dongtai": "东台站",
    "hefei": "合肥站",
    "hangzhou": "杭州站",
    "huhhot": "呼和浩特站",
    "jian": "吉安站",
    "liaocheng": "聊城站",
    "nanning": "南宁站",
    "panjin": "盘锦站",
    "qingdao": "青岛站",
    "qinhuangdao": "秦皇岛站",
    "qiqihar": "齐齐哈尔站",
    "shanwei": "汕尾站",
    "shaoxing": "绍兴站",
    "shenzhen": "深圳站",
    "xian": "西安站",
    "yancheng": "盐城站",
    "zhuhai": "珠海站",
}

TEST_NAME_RE = re.compile(r"\btest\b|测试|演示|\bdemo\b", re.IGNORECASE)
WORLD_YOUTH_RE = re.compile(r"world\s+(youth|cadets?)", re.IGNORECASE)
ASIAN_YOUTH_RE = re.compile(r"asian\s+(youth|schools?|junior)", re.IGNORECASE)
NATIONAL_MASTER_RE = re.compile(
    r"\bnational\s+(?:amateur\s+chess|cca)\s+master\s+tourn",
    re.IGNORECASE,
)
NATIONAL_MASTER_ZH_RE = re.compile(r"全国国际象棋棋协大师赛")
LICHENGZHI_ZH_RE = re.compile(r"李成智杯")
WORLD_YOUTH_ZH_RE = re.compile(r"世界青少年")
ASIAN_YOUTH_ZH_RE = re.compile(r"亚洲青少年")
GROUP_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])([UGBO])\s*(\d{1,2})(?!\d)", re.IGNORECASE)
UNDER_AGE_RE = re.compile(r"\bunder\s*(\d{1,2})(?!\d)", re.IGNORECASE)
PLAIN_AGE_RE = re.compile(r"(?<!\d)(0?[4-9]|1\d|2[0-5])(?!\d)")
TITLE_UMBRELLA_RE = re.compile(r"\b(?:championships?|cup)\b", re.IGNORECASE)
FEMALE_GROUP_RE = re.compile(
    r"girls?(?![A-Za-z])|women(?:['’´]s)?(?![A-Za-z])|female(?![A-Za-z])",
    re.IGNORECASE,
)
MALE_GROUP_RE = re.compile(
    # ``men`` is a suffix of ``women`` and ``male`` is a suffix of ``female``.
    # Guard their left edge so an explicit female title cannot be reclassified
    # as male; keep concatenated labels such as ``CUPBoys 12`` supported.
    r"boys?(?![A-Za-z])|(?<![A-Za-z])(?:men|male)(?![A-Za-z])|\bopen\b",
    re.IGNORECASE,
)
EDITION_RE = re.compile(r"\b(\d{1,3})(?:st|nd|rd|th)\b", re.IGNORECASE)
RAPID_RE = re.compile(r"\brapid\b|快棋", re.IGNORECASE)
BLITZ_RE = re.compile(r"\bblitz\b|超快棋", re.IGNORECASE)


def read_json(path: pathlib.Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def event_namespace_key(source: str, tournament_id: str, name: str = "", date: str = "") -> tuple[str, str]:
    """Return the single internal namespace for one source event.

    A Chess-Results TNR is a global event identity, even when a per-player PGN
    index labels its transport as ``Static PGN``.  Keeping that transport name
    as a second namespace produced two rows for tnr58153: the published detail
    lost its date/PGN facts while the static row lost the detailPath.  Collapse
    only the static transport alias; Lichess remains independently attributed.
    """
    source_key = clean(source).lower().replace("-", " ")
    tournament_id = clean(tournament_id)
    if tournament_id and source_key in {"chess results", "static pgn"}:
        return "chess-results", tournament_id
    return clean(source).lower(), tournament_id or f"name:{clean(name)}|{clean(date)}"


def event_id(
    source: str,
    tournament_id: str,
    name: str,
    date: str,
    canonical_event_id: str = "",
) -> str:
    """Return a stable internal key and a neutral key for non-TNR events."""
    source_key = source.lower().replace(" ", "-")
    if tournament_id:
        return f"{source_key}:{tournament_id}"
    if canonical_event_id:
        return canonical_event_id
    # The source/name/date combination is a fallback for providers without a
    # tournament ID. Use a digest instead of a raw string prefix: many Lichess
    # events share the same provider prefix, so a truncated hex encoding would
    # silently collide.
    seed = hashlib.sha256("|".join([source, name, date]).encode("utf-8")).hexdigest()[:16]
    return f"ev-{seed}"


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


def verified_master_group(master_group: dict[str, str]) -> dict[str, str]:
    """Only reviewed group labels may override an explicit event title."""
    status = clean(master_group.get("evidence_status")).lower()
    if status in {"verified", "page-verified", "manually-verified"}:
        return master_group
    return {}


def static_event_stats() -> dict[tuple[str, str], dict[str, Any]]:
    """Collect actual archived PGN coverage from per-player static indexes."""
    result: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "players": set(),
            "pgnCount": 0,
            "gameCount": 0,
            "names": set(),
            "dates": set(),
            "canonicalEventIDs": set(),
        }
    )
    for path in sorted(BY_PLAYER.glob("fide-*.json")):
        detail = read_json(path, {})
        fide_id = clean(detail.get("player", {}).get("fideID"))
        for event in detail.get("events", []):
            source = clean(event.get("source")) or "Static PGN"
            tournament_id = clean(event.get("tournamentID"))
            key = event_namespace_key(
                source,
                tournament_id,
                clean(event.get("name")),
                clean(event.get("date")),
            )
            row = result[key]
            if fide_id:
                row["players"].add(fide_id)
            row["pgnCount"] += 1
            row["gameCount"] += int(event.get("gameCount") or 0)
            if clean(event.get("name")):
                row["names"].add(clean(event["name"]))
            if clean(event.get("date")):
                row["dates"].add(clean(event["date"]))
            if clean(event.get("canonicalEventID")):
                row["canonicalEventIDs"].add(clean(event["canonicalEventID"]))
    return result


# Event hierarchy: canonical event → section/group → round → game.
# PGN-header derived rows from broadcast archives are frequently a single
# round/board/game title ("Round 6: A - B"), i.e. crawl/evidence units — NOT
# tournaments. They stay in the catalog for provenance but are tagged
# level="source-item" so product surfaces only treat level="event" rows as赛事.
ROUND_ITEM_RE = re.compile(
    r"^\s*(round|rd\.?|game|board|tiebreak)\s*\d+\b", re.IGNORECASE
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
    # The player-search catalog truncates many event titles.  The published
    # event-detail manifest is built from the tournament page itself, so its
    # display name is a stronger full-title candidate (without becoming an
    # authority for player identity fields).
    name_candidates = set(stats.get("names", set()))
    if clean(detail.get("displayName")):
        name_candidates.add(clean(detail.get("displayName")))
    name, aliases = best_source_name(clean(upstream.get("name")), name_candidates)
    chinese_name = mapping.get("chineseName", "")
    canonical_event_id = mapping.get("canonicalEventID", "")
    source_date = clean(upstream.get("date"))
    detail_date = clean(detail.get("dateEnd") or detail.get("dateBegin"))
    # When PGN is available, its EventDate is a direct record of the played
    # event and is more useful than a player-search row that may represent a
    # later rating/listing date.
    archived_dates = sorted(stats.get("dates", set()), reverse=True)
    date = archived_dates[0] if archived_dates else detail_date or source_date
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
        if detail.get("pgnAvailability"):
            event["pgnAvailability"] = detail.get("pgnAvailability")
        if detail.get("pgnSourceStatus"):
            event["pgnSourceStatus"] = detail.get("pgnSourceStatus")
        if detail.get("eventComplete"):
            event["eventComplete"] = True
        if detail.get("playableComplete"):
            event["playableComplete"] = True
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
    stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pure assembly of a reviewed-mapping-only event (no crawler row yet)."""
    source = "Chess-Results" if source_key == "chess-results" else source_key.title()
    chinese_name = mapping.get("chineseName") or ""
    stats = stats or {}
    year = master_group.get("year") or ""
    archived_dates = sorted(stats.get("dates", set()), reverse=True)
    date = archived_dates[0] if archived_dates else ""
    players = sorted(stats.get("players", set()))
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
        "date": date or None,
        "year": year or None,
        "url": mapping.get("evidenceURL") or None,
        "evidenceURL": mapping.get("evidenceURL") or None,
        "sourceRefs": [{"source": source, "tournamentID": tournament_id, "url": mapping.get("evidenceURL") or None}],
        "players": players,
        "playerCount": len(players),
        "pgnPlayerCount": len(players),
        "pgnCount": int(stats.get("pgnCount") or 0),
        "gameCount": int(stats.get("gameCount") or 0),
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


def build_detail_only_event(
    tournament_id: str,
    detail: dict[str, Any],
    mapping: dict[str, str],
    master_group: dict[str, str],
    stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create the catalog row promised by a published event-detail manifest."""
    stats = stats or {}
    display_name = clean(mapping.get("chineseName") or detail.get("displayName")) or f"赛事 {tournament_id}"
    name, aliases = best_source_name(display_name, set(stats.get("names", set())))
    archived_dates = sorted(stats.get("dates", set()), reverse=True)
    date = clean(detail.get("dateEnd") or detail.get("dateBegin")) or (
        archived_dates[0] if archived_dates else ""
    )
    players = sorted(stats.get("players", set()))
    year_match = re.search(r"(20\d{2})", " ".join([
        display_name,
        date,
        clean(mapping.get("canonicalEventID")),
        clean(master_group.get("year")),
    ]))
    return {
        "id": event_id("Chess-Results", tournament_id, display_name, date),
        "source": "Chess-Results",
        "tournamentID": tournament_id,
        "canonicalEventID": mapping.get("canonicalEventID") or None,
        "name": name or display_name,
        "aliases": aliases or None,
        "chineseName": mapping.get("chineseName") or None,
        "displayName": display_name,
        "date": date or None,
        "dateBegin": clean(detail.get("dateBegin")) or None,
        "dateEnd": clean(detail.get("dateEnd")) or None,
        "year": year_match.group(1) if year_match else None,
        "rounds": detail.get("roundCount") or None,
        "participants": detail.get("standingCount") or None,
        "players": players,
        "playerCount": len(players),
        "pgnPlayerCount": len(players),
        "pgnCount": int(stats.get("pgnCount") or 0),
        "gameCount": int(stats.get("gameCount") or 0),
        "detailPath": detail.get("path"),
        "standingCount": detail.get("standingCount"),
        "coverageScope": "domestic-full",
        "sectionID": master_group.get("section_id") or None,
        "groupCode": master_group.get("group_code") or None,
        "station": master_group.get("station") or None,
        "level": "event",
        **({"roundsPendingVerification": True} if detail.get("roundsPendingVerification") else {}),
        **({"pgnAvailability": detail.get("pgnAvailability")} if detail.get("pgnAvailability") else {}),
        **({"pgnSourceStatus": detail.get("pgnSourceStatus")} if detail.get("pgnSourceStatus") else {}),
        **({"eventComplete": True} if detail.get("eventComplete") else {}),
        **({"playableComplete": True} if detail.get("playableComplete") else {}),
    }


def build_pgn_only_event(source_key: str, key_id: str, stats: dict[str, Any]) -> dict[str, Any]:
    """Pure assembly of an event known only from PGN archives."""
    name = sorted(stats["names"])[0] if stats["names"] else "未命名赛事"
    date = sorted(stats["dates"], reverse=True)[0] if stats["dates"] else ""
    source = "Chess-Results" if source_key == "chess-results" else source_key.title()
    tournament_id = "" if key_id.startswith("name:") else key_id
    canonical_event_id = sorted(stats.get("canonicalEventIDs", set()))[0] if stats.get("canonicalEventIDs") else ""
    is_lichess = source.lower().startswith("lichess")
    return {
        "id": event_id(source, tournament_id, name, date, canonical_event_id),
        "source": source,
        "tournamentID": tournament_id or None,
        "canonicalEventID": canonical_event_id or None,
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
        **({
            "attribution": "Lichess Broadcasts",
            "license": "CC BY-SA 4.0",
            "licenseURL": "https://creativecommons.org/licenses/by-sa/4.0/",
        } if is_lichess else {}),
    }


def build_catalog() -> list[dict[str, Any]]:
    mappings = load_mappings()
    master_groups = load_master_groups()
    coverage = static_event_stats()
    details: dict[str, dict[str, Any]] = {}
    for item in read_json(EVENT_DETAILS, {}).get("events", []):
        tournament_id = clean(item.get("tournamentID"))
        if not tournament_id:
            continue
        if tournament_id in details:
            raise ValueError(f"duplicate event-detail manifest row: tnr{tournament_id}")
        details[tournament_id] = item
    events: dict[tuple[str, str], dict[str, Any]] = {}

    # The crawler catalog supplies all recorded Chess-Results participations,
    # including events for which an openly distributable PGN is unavailable.
    for upstream in read_json(CATALOG, []):
        tournament_id = clean(upstream.get("tournamentID"))
        if not tournament_id:
            continue
        source = clean(upstream.get("source")) or "Chess-Results"
        key = event_namespace_key(source, tournament_id)
        events[key] = build_upstream_event(
            upstream,
            mappings.get(key, {}),
            verified_master_group(master_groups.get(tournament_id, {})),
            details.get(tournament_id, {}),
            coverage.pop(key, {}),
        )

    # A reviewed mapping is useful metadata even before the player crawler has
    # discovered participants. Keep these sections visible and let later
    # player/PGN refreshes enrich the same stable source key.
    for (source_key, tournament_id), mapping in mappings.items():
        key = event_namespace_key(source_key, tournament_id)
        if key in events:
            continue
        events[key] = build_mapping_only_event(
            source_key,
            tournament_id,
            mapping,
            verified_master_group(master_groups.get(tournament_id, {})),
            details.get(tournament_id, {}),
            coverage.pop(key, {}),
        )

    # A published detail is itself sufficient catalog evidence. Queue-direct
    # captures do not necessarily appear in player-search rows or mappings.
    for tournament_id, detail in details.items():
        key = ("chess-results", tournament_id)
        if key in events:
            continue
        events[key] = build_detail_only_event(
            tournament_id,
            detail,
            mappings.get(key, {}),
            verified_master_group(master_groups.get(tournament_id, {})),
            coverage.pop(key, {}),
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
    if LICHENGZHI_ZH_RE.search(haystack):
        return "lichengzhi-cup"
    if WORLD_YOUTH_ZH_RE.search(haystack):
        return "world-youth"
    if ASIAN_YOUTH_ZH_RE.search(haystack):
        return "asian-youth"
    if WORLD_YOUTH_RE.search(haystack):
        return "world-youth"
    if ASIAN_YOUTH_RE.search(haystack):
        return "asian-youth"
    if NATIONAL_MASTER_RE.search(haystack) or NATIONAL_MASTER_ZH_RE.search(haystack):
        return "chess-association-master"
    if event.get("detailPath"):
        return "other"
    if clean(event.get("source")).lower().startswith(("lichess", "static pgn")):
        return "archive"
    return None


def parse_group_token(text: str) -> tuple[str | None, str | None, str | None]:
    """Extract one concrete youth section from a potentially umbrella title.

    Explicit Girls/Women/Female and Boys/Men/Open wording wins over a generic
    ``U`` token.  The age nearest that wording (or the right-most section token)
    wins, so umbrella prefixes such as ``U14, U16 & U18 ... - G16`` cannot leak
    their first age into every child section.
    """
    value = clean(text)
    if not value:
        return None, None, None

    ages: list[dict[str, Any]] = []
    occupied: list[tuple[int, int]] = []
    for match in GROUP_TOKEN_RE.finditer(value):
        age = match.group(2)
        if 4 <= int(age) <= 25:
            ages.append({"start": match.start(), "end": match.end(), "age": age, "kind": "token"})
            occupied.append((match.start(), match.end()))
    for match in UNDER_AGE_RE.finditer(value):
        age = match.group(1)
        if 4 <= int(age) <= 25:
            ages.append({"start": match.start(), "end": match.end(), "age": age, "kind": "under"})
            occupied.append((match.start(), match.end()))
    for match in PLAIN_AGE_RE.finditer(value):
        if any(start <= match.start() and match.end() <= end for start, end in occupied):
            continue
        ages.append({"start": match.start(), "end": match.end(), "age": match.group(1), "kind": "plain"})

    markers: list[dict[str, Any]] = []
    for match in FEMALE_GROUP_RE.finditer(value):
        markers.append({"start": match.start(), "end": match.end(), "sex": "F", "explicit": True})
    for match in MALE_GROUP_RE.finditer(value):
        markers.append({"start": match.start(), "end": match.end(), "sex": "M", "explicit": True})
    for match in GROUP_TOKEN_RE.finditer(value):
        letter = match.group(1).upper()
        if letter in {"G", "B", "O"}:
            markers.append({
                "start": match.start(), "end": match.end(),
                "sex": "F" if letter == "G" else "M", "explicit": False,
            })

    umbrella_end = max((match.end() for match in TITLE_UMBRELLA_RE.finditer(value)), default=-1)
    distinct_ages = {item["age"] for item in ages}

    def span_gap(marker: dict[str, Any], age: dict[str, Any]) -> int:
        if age["end"] < marker["start"]:
            return marker["start"] - age["end"]
        if marker["end"] < age["start"]:
            return age["start"] - marker["end"]
        return 0

    sex: str | None = None
    chosen_age: dict[str, Any] | None = None
    if markers:
        pairs = [
            (span_gap(marker, age), -max(marker["start"], age["start"]), marker, age)
            for marker in markers for age in ages
        ]
        if pairs:
            _, _, marker, chosen_age = min(pairs, key=lambda item: (item[0], item[1]))
            sex = marker["sex"]
        else:
            marker = max(markers, key=lambda item: item["start"])
            sex = marker["sex"]
    elif ages:
        token_ages = [item for item in ages if item["kind"] == "token"]
        chosen_age = max(token_ages or ages, key=lambda item: item["start"])
        sex = "M"

    # A title listing several ages before "Championships/Cup" is an umbrella,
    # not the last child section.  Keep an explicit gender if present, but do
    # not invent an age unless a concrete suffix appears after the umbrella.
    if chosen_age and len(distinct_ages) > 1 and chosen_age["end"] <= umbrella_end:
        chosen_age = None
        if not markers:
            sex = None

    if not sex:
        return None, None, None
    if not chosen_age:
        return ("女子组" if sex == "F" else "男子组"), sex, None
    age_group = f"U{chosen_age['age']}"
    return f"{'女子' if sex == 'F' else '男子'}{age_group}组", sex, age_group


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


def has_chinese_text(value: Any) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", clean(value)))


def parse_master_title_hints(event: dict[str, Any]) -> tuple[str | None, str | None]:
    """Extract only explicit station/group facts from reviewed or source titles.

    Romanised stations are translated through an allowlist. Unknown stations
    remain unresolved so the public-catalog gate can isolate them for review
    instead of publishing another generic same-year title.
    """
    title = " ".join(filter(None, [
        event.get("chineseName"), event.get("name"), event.get("displayName"),
        *(event.get("aliases") or []),
    ]))
    station = None
    chinese_station = re.search(r"（([^（）]{1,20}?站)(?:[·）])", title)
    if chinese_station:
        station = clean(chinese_station.group(1))
    if not station:
        english_station = re.search(r"\b([A-Za-z][A-Za-z' -]{1,40}?)\s+Station\b", title, re.IGNORECASE)
        if english_station:
            station_key = re.sub(r"[^a-z]", "", english_station.group(1).lower())
            station = next((
                translated for key, translated in MASTER_STATION_TRANSLATIONS.items()
                if station_key.endswith(key)
            ), None)

    group_label = next((label for label in MASTER_GROUP_LABELS.values() if label in title), None)
    if not group_label:
        explicit_group = re.search(
            r"(?:男子|女子)候补(?:棋协)?大师组|(?:男子|女子)一级棋士(?:[ABC])?组|公开组",
            title,
        )
        group_label = clean(explicit_group.group(0)) if explicit_group else None
    if not group_label and re.search(r"\bOpen\b", title, re.IGNORECASE):
        group_label = MASTER_GROUP_LABELS["OPEN"]
    return station, group_label


def localized_public_name(
    event: dict[str, Any],
    series: str,
    year: str,
    *,
    edition: str | None = None,
    station: str | None = None,
    group_label: str | None = None,
) -> str:
    """Return a Chinese-only public title while retaining source names internally."""
    reviewed = clean(event.get("chineseName"))
    if has_chinese_text(reviewed):
        if group_label and group_label not in reviewed:
            # The reviewed name and the structural group table are maintained
            # separately.  If an old reviewed title still contains a different
            # master-group suffix, replace it with the verified structural
            # label instead of publishing two contradictory groups.
            master_suffix = re.compile(
                r"(?:男子|女子)候补(?:棋协)?大师组|"
                r"(?:男子|女子)一级棋士(?:[ABC])?组|"
                r"棋协大师组|公开组"
            )
            if series == "chess-association-master" and master_suffix.search(reviewed):
                return master_suffix.sub(group_label, reviewed)
            return f"{reviewed}（{group_label}）"
        return reviewed

    if series == "chess-association-master":
        location = f"（{station}）" if station else ""
        return f"{year}年{SERIES_LABELS[series]}{location}{group_label or ''}"

    base = f"{year}年{edition or ''}{SERIES_LABELS[series]}"
    source_names = " ".join(filter(None, [
        event.get("name"), event.get("displayName"), *(event.get("aliases") or []),
    ]))
    format_label = "超快棋" if BLITZ_RE.search(source_names) else "快棋" if RAPID_RE.search(source_names) else ""
    qualifiers = "·".join(filter(None, [format_label, group_label]))
    return f"{base}（{qualifiers}）" if qualifiers else base


def public_event(event: dict[str, Any], series: str, master_group: dict[str, str]) -> dict[str, Any] | None:
    """Project one internal event into the structured public-catalog shape.

    Pure function; returns None when required structured fields cannot be
    established (those are reported by the audit, never silently admitted).
    """
    tournament_id = clean(event.get("tournamentID"))
    stable_id = clean(event.get("id"))
    if not tournament_id and not stable_id:
        return None
    date = event.get("date")
    year = (date or "")[:4] or clean(master_group.get("year")) or (str(event.get("year") or ""))[:4]
    if not year:
        canonical = str(event.get("canonicalEventID") or "")
        match = re.search(r"(\d{4})", canonical)
        year = match.group(1) if match else ""
    if not year:
        names = " ".join(filter(None, [
            event.get("name"), event.get("displayName"), *(event.get("aliases") or []),
        ]))
        match = re.search(r"\b(19|20)\d{2}\b", names)
        year = match.group(0) if match else ""
    if not year and series not in {"other", "archive"}:
        return None

    station = clean(master_group.get("station")) or None
    group_label = sex = age_group = None
    level = clean(master_group.get("level")) or None
    edition = None

    if series == "chess-association-master":
        title_station, title_group = parse_master_title_hints(event)
        station = station or title_station
        code = clean(master_group.get("group_code"))
        group_label = MASTER_GROUP_LABELS.get(code) or title_group
        if not level and group_label == MASTER_GROUP_LABELS["OPEN"]:
            level = "OPEN"
        sex = clean(master_group.get("sex")) or None
    elif series == "lichengzhi-cup":
        chinese = event.get("chineseName") or ""
        group_label, sex, age_group = parse_chinese_group(chinese)
    elif series in {"world-youth", "asian-youth"}:
        haystack = " ".join(filter(None, [event.get("name"), *(event.get("aliases") or [])]))
        group_label, sex, age_group = parse_group_token(haystack)
        edition_match = EDITION_RE.search(haystack)
        edition = f"第{edition_match.group(1)}届" if edition_match else None
    if series in {"other", "archive"}:
        display_name = clean(event.get("chineseName") or event.get("displayName") or event.get("name"))
        if not group_label:
            group_label, sex, age_group = parse_chinese_group(display_name)
    else:
        display_name = localized_public_name(
            event,
            series,
            year,
            edition=edition,
            station=station,
            group_label=group_label,
        )

    return {
        "id": stable_id,
        "series": series,
        "seriesLabel": SERIES_LABELS[series],
        "year": year,
        "edition": edition,
        "station": station,
        "groupLabel": group_label,
        "sex": sex,
        "ageGroup": age_group,
        "level": level,
        "tournamentID": tournament_id or None,
        "date": date,
        "displayName": display_name,
        "name": event.get("name") or None,
        "chineseName": event.get("chineseName"),
        "aliases": event.get("aliases"),
        "rounds": event.get("rounds"),
        "participants": event.get("participants"),
        "playerCount": event.get("playerCount"),
        "gameCount": event.get("gameCount"),
        "detailStatus": "published" if event.get("detailPath") else "missing-detail",
        "detailPath": event.get("detailPath"),
        "roundsPendingVerification": event.get("roundsPendingVerification") or None,
        "pgnAvailability": event.get("pgnAvailability") or None,
        "pgnSourceStatus": event.get("pgnSourceStatus") or None,
        "eventComplete": event.get("eventComplete") or None,
        "playableComplete": event.get("playableComplete") or None,
        "canonicalEventID": event.get("canonicalEventID"),
        "nameTranslationPending": not has_chinese_text(display_name) or None,
        "attribution": event.get("attribution"),
        "license": event.get("license"),
        "licenseURL": event.get("licenseURL"),
    }


def excluded_from_public(event: dict[str, Any], today: str) -> str | None:
    """Build-layer exclusion filter for the public catalog (P1)."""
    name = " ".join(filter(None, [event.get("name"), event.get("displayName")]))
    if TEST_NAME_RE.search(name):
        return "test-or-demo-name"
    date = event.get("date") or ""
    if date and date > (dt.date.fromisoformat(today) + dt.timedelta(days=200)).isoformat():
        return "implausible-future-date"
    if (
        not date
        and not event.get("chineseName")
        and not event.get("canonicalEventID")
        and not event.get("detailPath")
        and not re.search(r"\b(19|20)\d{2}\b", name)
    ):
        return "undated-and-unmapped"
    return None


def public_catalog(events: list[dict[str, Any]], master_groups: dict[str, dict[str, str]],
                   today: str | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    today = today or dt.date.today().isoformat()
    rows_by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
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
        projected = public_event(
            event,
            series,
            verified_master_group(master_groups.get(clean(event.get("tournamentID")), {})),
        )
        if projected is None:
            excluded.append({"tournamentID": event.get("tournamentID"), "id": event.get("id"), "reason": "missing-structured-fields"})
            continue
        if series == "chess-association-master" and not projected.get("station"):
            # Keep the deep link promised by a published detail, but do not
            # invent a generic master-series title when the station is still
            # unverified.
            if event.get("detailPath"):
                projected = public_event(event, "other", {})
            else:
                excluded.append({"tournamentID": event.get("tournamentID"), "id": event.get("id"), "reason": "master-station-missing"})
                continue
        grouping_key = clean(projected.get("tournamentID")) or clean(projected.get("id"))
        rows_by_event[grouping_key].append(projected)
    rows = [merge_public_event_rows(group) for group in rows_by_event.values()]
    rows.sort(key=lambda item: (item.get("date") or "", item.get("id") or ""), reverse=True)
    return rows, excluded


def merge_public_event_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Coalesce duplicate source/PGN projections for one TNR into one page."""
    if not rows:
        raise ValueError("cannot merge an empty public event group")

    def score(row: dict[str, Any]) -> tuple[int, int, int, int, str]:
        return (
            1 if row.get("detailPath") else 0,
            1 if str(row.get("id") or "").startswith("chess-results:") else 0,
            1 if row.get("participants") else 0,
            1 if row.get("groupLabel") else 0,
            str(row.get("date") or ""),
        )

    primary = max(rows, key=score)
    merged = dict(primary)
    aliases: set[str] = set(primary.get("aliases") or [])
    for row in rows:
        for key, value in row.items():
            if merged.get(key) in (None, "", [], {}) and value not in (None, "", [], {}):
                merged[key] = value
        aliases.update(row.get("aliases") or [])
        if row.get("name") and row.get("name") != merged.get("name"):
            aliases.add(str(row["name"]))
        merged["playerCount"] = max(int(merged.get("playerCount") or 0), int(row.get("playerCount") or 0))
        merged["gameCount"] = max(int(merged.get("gameCount") or 0), int(row.get("gameCount") or 0))
    aliases.discard(str(merged.get("name") or ""))
    merged["aliases"] = sorted(aliases) or None
    return merged


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
    PUBLIC_OUTPUT.write_text(json.dumps(stamp({
        "schemaVersion": 2,
        "series": SERIES_LABELS,
        "totals": {
            "events": len(public_rows),
            "withDetail": sum(1 for row in public_rows if row["detailStatus"] == "published"),
            "excluded": len(excluded),
        },
        "excluded": excluded,
        "events": public_rows,
    }), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
