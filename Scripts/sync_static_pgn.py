#!/usr/bin/env python3
"""Sync cached/fetched PGN files into the GitHub Pages static data tree.

The static site is deliberately repository-backed:

docs/data/pgn/<source>/tnr<event-id>/fide-<fide-id>-<event-id>.pgn
docs/data/index/manifest.json
docs/data/index/players.json
docs/data/index/players/fide-<fide-id>.json

The public event catalog (docs/data/index/events.json) is owned by
Scripts/build_event_catalog.py and is intentionally not written here.

Records are read from the committed static indexes; missing PGNs are then
fetched from configured public sources (Chess-Results).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html.parser
import json
import os
import pathlib
import re
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from source_http import SourceHTTPError, fetch_bytes
from source_policy import require_chess_results_publication
from stable_json import write_json as write_stable_json


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS_DATA = REPO_ROOT / "docs" / "data"
STATIC_PGN_ROOT = DOCS_DATA / "pgn"
INDEX_ROOT = DOCS_DATA / "index"
PLAYER_INDEX_ROOT = INDEX_ROOT / "players"
LEADERBOARD_JSON = DOCS_DATA / "youth-leaderboards.json"
REGISTRY_PLAYERS_JSON = DOCS_DATA / "registry" / "players.json"
USER_AGENT = "ChinaChessPlayerPGNStaticSync/1.0"
_REGISTRY_PROFILES: dict[str, dict[str, Any]] | None = None


@dataclass
class EventRecord:
    fide_id: str
    player_id: str
    display_name: str
    chinese_name: str = ""
    pinyin_name: str = ""
    english_name: str = ""
    federation: str = "CHN"
    birth_year: int | None = None
    standard_rating: int | None = None
    rapid_rating: int | None = None
    blitz_rating: int | None = None
    event_id: str = ""
    source: str = "Chess-Results"
    tournament_id: str = ""
    event_name: str = ""
    end_date: str = ""
    rank: str = ""
    rounds: str = ""
    participants: str = ""
    pgn_path: str = ""
    game_count: int = 0
    bytes: int = 0
    sha256: str = ""
    source_url: str = ""

    @property
    def static_relative_path(self) -> str:
        source_slug = slug(self.source)
        event_slug = slug(self.tournament_id or self.event_id)
        if source_slug == "chess-results" and str(self.tournament_id).isdigit():
            event_folder = f"tnr{self.tournament_id}"
            file_event_id = str(self.tournament_id)
        else:
            event_folder = event_slug
            file_event_id = event_slug
        return f"{source_slug}/{event_folder}/fide-{self.fide_id}-{file_event_id}.pgn"

    @property
    def static_path(self) -> pathlib.Path:
        return STATIC_PGN_ROOT / self.static_relative_path

    @property
    def public_pgn_path(self) -> str:
        return f"data/pgn/{self.static_relative_path}"


@dataclass
class SyncStats:
    copied: int = 0
    downloaded: int = 0
    skipped: int = 0
    empty: int = 0
    failed: int = 0
    manifest_players: int = 0
    manifest_events: int = 0
    manifest_pgn: int = 0
    manifest_games: int = 0
    errors: list[str] = field(default_factory=list)


class FormParser(html.parser.HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.action_url = base_url
        self.fields: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "form":
            action = values.get("action")
            if action:
                self.action_url = urllib.parse.urljoin(self.base_url, action)
        if tag.lower() == "input":
            # Never pre-collect submit-style buttons: posting two buttons at
            # once makes ASP.NET fire the wrong handler. Chess-Results added a
            # "Download Excel-File" button to SpielerSuche (2026-07), which
            # turned every scripted search into an Excel download and broke
            # the crawler with zero-row HTML parses. Callers explicitly set
            # the one button they intend to "press".
            if values.get("type", "").lower() in {"submit", "button", "image", "reset"}:
                return
            name = values.get("name")
            if name:
                self.fields[name] = values.get("value", "")


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync static PGN archive for GitHub Pages.")
    parser.add_argument("--fetch-missing", action="store_true", help="fetch missing PGNs from supported sources")
    parser.add_argument("--player", action="append", default=[], help="limit to one FIDE ID; repeatable")
    parser.add_argument("--source", action="append", default=[], help="limit to one source slug/name; repeatable")
    parser.add_argument("--max-downloads", type=int, default=0, help="maximum source requests; 0 means no explicit limit")
    parser.add_argument("--delay", type=float, default=0.7, help="delay between network downloads")
    parser.add_argument("--strict", action="store_true", help="exit non-zero when any source request fails")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.fetch_missing:
        require_chess_results_publication()

    stats = SyncStats()
    ensure_dirs()

    all_records = records_from_static_indexes()
    # A derived index may describe events for a registry player, but it may
    # never recreate a player identity that is absent from the authoritative
    # registry. Transfer exceptions belong in federation-overrides first.
    all_records = [record for record in all_records if profile_for_fide(record.fide_id)]
    records = all_records
    if args.player:
        allowed = {str(item) for item in args.player}
        records = [record for record in records if record.fide_id in allowed]
    if args.source:
        allowed_sources = {slug(item) for item in args.source}
        records = [record for record in records if slug(record.source) in allowed_sources]

    if args.fetch_missing:
        fetch_missing(records, stats, args.max_downloads, args.delay, dry_run=args.dry_run)

    manifest_records = merge_static_pgn_metadata(all_records)
    write_indexes(manifest_records, stats, dry_run=args.dry_run)
    update_leaderboard_json(manifest_records, dry_run=args.dry_run)

    print(json.dumps(stats.__dict__, ensure_ascii=False, indent=2))
    return 1 if args.strict and stats.failed else 0


def ensure_dirs() -> None:
    STATIC_PGN_ROOT.mkdir(parents=True, exist_ok=True)
    PLAYER_INDEX_ROOT.mkdir(parents=True, exist_ok=True)


def records_from_static_indexes() -> list[EventRecord]:
    records: list[EventRecord] = []
    for player_file in sorted(PLAYER_INDEX_ROOT.glob("fide-*.json")):
        data = read_json(player_file)
        for event in data.get("events", []):
            records.append(
                EventRecord(
                    fide_id=str(data.get("fideID", "")).strip(),
                    player_id=f"fide-{data.get('fideID', '')}",
                    display_name=str(data.get("displayName") or data.get("name") or data.get("fideID")),
                    chinese_name=str(data.get("chineseName") or ""),
                    pinyin_name=str(data.get("pinyin") or ""),
                    english_name=str(data.get("name") or ""),
                    federation=str(data.get("federation") or "CHN"),
                    birth_year=data.get("birthYear"),
                    standard_rating=data.get("standard"),
                    rapid_rating=data.get("rapid"),
                    blitz_rating=data.get("blitz"),
                    event_id=str(event.get("id") or ""),
                    source=str(event.get("source") or "Chess-Results"),
                    tournament_id=str(event.get("tournamentID") or ""),
                    event_name=str(event.get("name") or ""),
                    end_date=str(event.get("date") or ""),
                    rank=str(event.get("rank") or ""),
                    rounds=str(event.get("rounds") or ""),
                    participants=str(event.get("participants") or ""),
                    pgn_path=str(event.get("pgnPath") or "").replace("data/pgn/", ""),
                    game_count=int(event.get("gameCount") or 0),
                    source_url=str(event.get("sourceURL") or ""),
                )
            )
    return [record for record in records if record.fide_id and record.tournament_id]


def fetch_missing(
    records: list[EventRecord],
    stats: SyncStats,
    max_downloads: int,
    delay: float,
    dry_run: bool,
) -> None:
    attempted = 0
    for record in records:
        if record.source.lower() != "chess-results":
            stats.skipped += 1
            continue
        if record.static_path.exists():
            stats.skipped += 1
            continue
        if max_downloads and attempted >= max_downloads:
            break
        if not record.fide_id or not record.tournament_id:
            stats.skipped += 1
            continue

        try:
            print(f"fetch {record.fide_id} tnr{record.tournament_id} {record.event_name}", file=sys.stderr)
            attempted += 1
            pgn = download_chess_results_pgn(record.fide_id, record.tournament_id)
            if count_pgn_games(pgn) == 0:
                stats.empty += 1
                continue
            stats.downloaded += 1
            if not dry_run:
                record.static_path.parent.mkdir(parents=True, exist_ok=True)
                record.static_path.write_text(pgn.strip() + "\n", encoding="utf-8")
            if delay:
                time.sleep(delay)
        except Exception as error:  # noqa: BLE001 - batch sync should continue.
            stats.failed += 1
            stats.errors.append(f"{record.fide_id} tnr{record.tournament_id}: {error}")


def download_chess_results_pgn(fide_id: str, tournament_id: str) -> str:
    form = load_form("https://chess-results.com/PartieSuche.aspx?lan=1")
    fields = dict(form["fields"])
    fields["ctl00$P1$Txt_FideID"] = fide_id
    fields["ctl00$P1$txt_dbkey"] = tournament_id
    fields["ctl00$P1$combo_anzahl_zeilen"] = "5"
    fields["ctl00$P1$cb_DownLoadPGN"] = "Download as PGN-File"
    body = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(
        form["action_url"],
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": USER_AGENT,
            "Referer": form["base_url"],
        },
        method="POST",
    )
    def validate(data: bytes, _headers: Any) -> None:
        if data.lstrip().lower().startswith((b"<!doctype html", b"<html")):
            raise SourceHTTPError("SOURCE_UNEXPECTED_CONTENT_TYPE", "Chess-Results PGN 下载返回了 HTML 页面。")

    data, _final_url, _headers = fetch_bytes(
        request,
        timeout=60,
        retries=2,
        validator=validate,
    )
    return decode_response(data)


def load_form(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    data, final_url, _headers = fetch_bytes(
        request,
        timeout=60,
        retries=2,
        expected_types=("text/html", "application/xhtml+xml"),
    )
    html = decode_response(data)
    parser = FormParser(final_url)
    parser.feed(html)
    return {"base_url": final_url, "action_url": parser.action_url, "fields": parser.fields}


def merge_static_pgn_metadata(records: list[EventRecord]) -> list[EventRecord]:
    by_key: dict[tuple[str, str, str], EventRecord] = {}
    for record in records:
        if not record.fide_id or not record.tournament_id:
            continue
        by_key[(record.fide_id, slug(record.source), record.tournament_id)] = record

    for path in sorted(STATIC_PGN_ROOT.glob("*/*/*.pgn")):
        match = re.match(r"fide-(\d+)-(.+)\.pgn$", path.name)
        if not match:
            continue
        fide_id, event_key = match.groups()
        source_slug = path.parts[-3]
        source = display_source_name(source_slug)
        tournament_id = event_key
        key = (fide_id, source_slug, tournament_id)
        record = by_key.get(key)
        if record is None:
            profile = profile_for_fide(fide_id)
            record = EventRecord(
                fide_id=fide_id,
                player_id=f"fide-{fide_id}",
                display_name=str(profile.get("displayName") or profile.get("name") or f"FIDE {fide_id}"),
                chinese_name=str(profile.get("chineseName") or ""),
                pinyin_name=str(profile.get("pinyin") or ""),
                english_name=str(profile.get("name") or ""),
                federation=str(profile.get("federation") or "CHN"),
                birth_year=profile.get("birthYear"),
                standard_rating=profile.get("standard"),
                rapid_rating=profile.get("rapid"),
                blitz_rating=profile.get("blitz"),
                source=source,
                tournament_id=tournament_id,
                event_id=f"{source}-{tournament_id}",
            )
            by_key[key] = record
        record.pgn_path = str(path.relative_to(STATIC_PGN_ROOT))
        pgn_text = path.read_text(encoding="utf-8", errors="replace")
        record.game_count = count_pgn_games(pgn_text)
        if record.game_count == 0:
            record.pgn_path = ""
            record.bytes = 0
            record.sha256 = ""
            continue
        record.bytes = path.stat().st_size
        record.sha256 = sha256_file(path)
        record.event_name = first_pgn_header(pgn_text, "Event") or record.event_name
        if not record.end_date:
            record.end_date = normalize_pgn_date(first_pgn_header(pgn_text, "EventDate") or first_pgn_header(pgn_text, "Date") or "")

    return sorted(by_key.values(), key=lambda item: (item.fide_id, item.end_date or "", item.tournament_id), reverse=False)


def write_indexes(records: list[EventRecord], stats: SyncStats, dry_run: bool) -> None:
    generated_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    by_player: dict[str, list[EventRecord]] = {}
    by_event: dict[str, list[EventRecord]] = {}
    for record in records:
        by_player.setdefault(record.fide_id, []).append(record)
        by_event.setdefault(f"{slug(record.source)}:{record.tournament_id}", []).append(record)

    player_summaries = []
    for fide_id, player_records in sorted(by_player.items()):
        profile = player_profile(player_records)
        pgn_records = [record for record in player_records if has_static_pgn(record)]
        player_summaries.append(
            {
                "fideID": fide_id,
                "displayName": profile["displayName"],
                "chineseName": profile.get("chineseName", ""),
                "pinyin": profile.get("pinyin", ""),
                "name": profile.get("name", ""),
                "federation": profile.get("federation", "CHN"),
                "birthYear": profile.get("birthYear"),
                "standard": profile.get("standard"),
                "rapid": profile.get("rapid"),
                "blitz": profile.get("blitz"),
                "eventCount": len(player_records),
                "pgnCount": len(pgn_records),
                "gameCount": sum(record.game_count for record in pgn_records),
                "detailPath": f"data/index/players/fide-{fide_id}.json",
            }
        )
        detail = {
            **profile,
            "fideID": fide_id,
            "id": f"fide-{fide_id}",
            "generatedAt": generated_at,
            "events": [event_payload(record) for record in sorted(player_records, key=lambda item: item.end_date or "", reverse=True)],
        }
        if not dry_run:
            write_json(PLAYER_INDEX_ROOT / f"fide-{fide_id}.json", detail)

    pgn_records = [record for record in records if has_static_pgn(record)]
    manifest = {
        "schemaVersion": 1,
        "generatedAt": generated_at,
        "storage": {
            "pgnRoot": "data/pgn",
            "playerIndexRoot": "data/index/players",
            "pathPattern": "data/pgn/<source>/<eventKey>/fide-<fideID>-<eventKey>.pgn",
        },
        "totals": {
            "players": len(by_player),
            "events": len(by_event),
            "pgnFiles": len(pgn_records),
            "games": sum(record.game_count for record in pgn_records),
            "bytes": sum(record.bytes for record in pgn_records),
        },
        "sources": sorted({record.source for record in records}),
    }
    manifest = preserve_source_manifest_generated_at(
        read_json(INDEX_ROOT / "manifest.json") if (INDEX_ROOT / "manifest.json").exists() else {},
        manifest,
    )

    stats.manifest_players = len(by_player)
    stats.manifest_events = len(by_event)
    stats.manifest_pgn = len(pgn_records)
    stats.manifest_games = sum(record.game_count for record in pgn_records)

    if not dry_run:
        write_json(INDEX_ROOT / "manifest.json", manifest)
        write_json(INDEX_ROOT / "players.json", player_summaries)


def preserve_source_manifest_generated_at(previous: Any, current: dict[str, Any]) -> dict[str, Any]:
    """Keep source freshness stable across the public-metrics enrichment pass.

    ``build_public_metrics.py`` enriches the legacy index manifest in place,
    moving its source totals into ``sourceTotals``.  On the next offline run
    this builder must compare against those source totals, otherwise it
    temporarily replaces the enriched manifest and advances ``generatedAt``
    even when the source snapshot is identical.
    """
    if not isinstance(previous, dict) or not previous.get("generatedAt"):
        return current
    previous_source = {
        key: value
        for key, value in previous.items()
        if key not in {"generatedAt", "sourceTotals", "metricContract"}
    }
    previous_source["totals"] = previous.get("sourceTotals", previous.get("totals", {}))
    current_source = {key: value for key, value in current.items() if key != "generatedAt"}
    if previous_source != current_source:
        return current
    stable = dict(current)
    stable["generatedAt"] = previous["generatedAt"]
    return stable


def update_leaderboard_json(records: list[EventRecord], dry_run: bool) -> None:
    if not LEADERBOARD_JSON.exists():
        return
    data = read_json(LEADERBOARD_JSON)
    players = {str(player.get("fideID")): player for player in data.get("players", [])}
    authority_text = (
        "displayName", "chineseName", "pinyin", "name", "federation",
        "sex", "title", "womenTitle", "formerFederation",
    )
    authority_optional = ("birthYear", "standard", "rapid", "blitz")
    authority_structured = ("transfer", "aliases")
    # Purge stale identity/rating values across the whole legacy leaderboard,
    # including players with no PGN rows in this rebuild.
    retained = []
    for player in data.get("players", []):
        fide_id = str(player.get("fideID") or "")
        profile = profile_for_fide(fide_id)
        if not profile:
            continue
        for key in authority_text:
            player[key] = profile.get(key) or (f"FIDE {fide_id}" if key == "displayName" else "")
        for key in authority_optional:
            value = profile.get(key)
            if value is None:
                player.pop(key, None)
            else:
                player[key] = value
        player["inactive"] = bool(profile.get("inactive"))
        for key in authority_structured:
            value = profile.get(key)
            if value in (None, "", [], {}):
                player.pop(key, None)
            else:
                player[key] = value
        retained.append(player)
    data["players"] = retained
    players = {str(player.get("fideID")): player for player in retained}
    by_player: dict[str, list[EventRecord]] = {}
    for record in records:
        by_player.setdefault(record.fide_id, []).append(record)

    for fide_id, player_records in by_player.items():
        profile = player_profile(player_records)
        player = players.get(fide_id)
        if player is None:
            player = {
                "fideID": fide_id,
                "name": profile.get("name") or profile.get("displayName") or f"FIDE {fide_id}",
                "birthYear": profile.get("birthYear"),
            }
            data.setdefault("players", []).append(player)
            players[fide_id] = player
        for key in authority_text:
            player[key] = profile.get(key) or (f"FIDE {fide_id}" if key == "displayName" else "")
        for key in authority_optional:
            value = profile.get(key)
            if value is None:
                player.pop(key, None)
            else:
                player[key] = value
        player["inactive"] = bool(profile.get("inactive"))
        for key in authority_structured:
            value = profile.get(key)
            if value in (None, "", [], {}):
                player.pop(key, None)
            else:
                player[key] = value
        player["detailPath"] = f"data/index/players/fide-{fide_id}.json"
        player["eventCount"] = len(player_records)
        player["pgnCount"] = sum(1 for record in player_records if has_static_pgn(record))
        player["gameCount"] = sum(record.game_count for record in player_records if has_static_pgn(record))

        compact_events = [
            event_payload(record)
            for record in sorted(player_records, key=lambda item: item.end_date or "", reverse=True)
            if has_static_pgn(record)
        ]
        if compact_events:
            player["events"] = compact_events[:12]

    if not dry_run:
        write_json(LEADERBOARD_JSON, data)


def player_profile(records: list[EventRecord]) -> dict[str, Any]:
    first = records[0]
    registry = profile_for_fide(first.fide_id)
    if not registry:
        return {
            "displayName": f"FIDE {first.fide_id}",
            "chineseName": "", "pinyin": "", "name": "", "federation": "",
            "sex": "", "title": "", "womenTitle": "", "formerFederation": "",
            "birthYear": None, "standard": None, "rapid": None, "blitz": None,
            "inactive": False, "transfer": None, "aliases": [],
        }
    # Every identity/rating field comes directly from the registry, including
    # empty values. Falling back to last build's output would make a bad name
    # or stale rating self-perpetuating.
    return {
        "displayName": registry.get("displayName") or registry.get("name") or f"FIDE {first.fide_id}",
        "chineseName": registry.get("chineseName") or "",
        "pinyin": registry.get("pinyin") or "",
        "name": registry.get("name") or "",
        "federation": registry.get("federation") or "",
        "sex": registry.get("sex") or "",
        "title": registry.get("title") or "",
        "womenTitle": registry.get("womenTitle") or "",
        "formerFederation": registry.get("formerFederation") or "",
        "birthYear": registry.get("birthYear"),
        "standard": registry.get("standard"),
        "rapid": registry.get("rapid"),
        "blitz": registry.get("blitz"),
        "inactive": bool(registry.get("inactive")),
        "transfer": registry.get("transfer"),
        "aliases": list(registry.get("aliases") or []),
    }


def profile_for_fide(fide_id: str) -> dict[str, Any]:
    return registry_profiles().get(str(fide_id), {})


def registry_profiles() -> dict[str, dict[str, Any]]:
    global _REGISTRY_PROFILES
    if _REGISTRY_PROFILES is not None:
        return _REGISTRY_PROFILES

    profiles: dict[str, dict[str, Any]] = {}
    if REGISTRY_PLAYERS_JSON.exists():
        data = read_json(REGISTRY_PLAYERS_JSON)
        players = data.get("players", []) if isinstance(data, dict) else data
        for player in players:
            if not isinstance(player, dict):
                continue
            fide_id = str(player.get("fideID") or "").strip()
            if fide_id:
                profiles[fide_id] = dict(player)
    _REGISTRY_PROFILES = profiles
    return profiles


def event_payload(record: EventRecord) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": f"{slug(record.source)}:{record.tournament_id}",
        "source": record.source,
        "tournamentID": record.tournament_id,
        "name": record.event_name,
        "date": record.end_date,
        "rank": int(record.rank) if str(record.rank).isdigit() else record.rank,
        "rounds": int(record.rounds) if str(record.rounds).isdigit() else record.rounds,
        "participants": int(record.participants) if str(record.participants).isdigit() else record.participants,
    }
    if record.source_url:
        payload["sourceURL"] = record.source_url
    if has_static_pgn(record):
        payload.update(
            {
                "pgnPath": record.public_pgn_path,
                "gameCount": record.game_count,
                "pgnBytes": record.bytes,
                "sha256": record.sha256,
            }
        )
    if is_li_chengzhi_like(record.event_name):
        payload["kind"] = "li-chengzhi"
    return payload


def has_static_pgn(record: EventRecord) -> bool:
    return record.static_path.exists() and record.game_count > 0 and record.bytes > 0


def read_json(path: pathlib.Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: pathlib.Path, data: Any) -> None:
    write_stable_json(path, data, ensure_ascii=False, indent=2)


def decode_response(data: bytes) -> str:
    for encoding in ("utf-8", "iso-8859-1", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def count_pgn_games(text: str) -> int:
    return len(re.findall(r'^\[Event\s+"', text, flags=re.MULTILINE | re.IGNORECASE))


def first_pgn_header(text: str, key: str) -> str | None:
    match = re.search(rf'^\[{re.escape(key)}\s+"(.*)"\]', text, flags=re.MULTILINE | re.IGNORECASE)
    return match.group(1) if match else None


def normalize_pgn_date(text: str) -> str:
    if re.match(r"^\d{4}\.\d{2}\.\d{2}$", text):
        return text.replace(".", "-")
    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        return text
    return ""


def slug(value: str) -> str:
    lowered = str(value or "").strip().lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return cleaned or "unknown"


def display_source_name(source_slug: str) -> str:
    if slug(source_slug) == "chess-results":
        return "Chess-Results"
    if slug(source_slug) == "lichess":
        return "Lichess"
    if slug(source_slug) == "twic":
        return "TWIC"
    if slug(source_slug) == "chesscom":
        return "Chess.com"
    return source_slug


def is_li_chengzhi_like(name: str) -> bool:
    normalized = re.sub(r"[\s,.'\"()，。·_\-]+", "", name.lower())
    return "李成智" in normalized or "lichengzhi" in normalized or "nationalyouthchesschampionship" in normalized


if __name__ == "__main__":
    raise SystemExit(main())
