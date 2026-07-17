#!/usr/bin/env python3
"""Promote public, quality-checked PGN into docs/data/pgn.

Two inputs are supported:

1. Chess-Results player search by FIDE ID. This uses the public PGN download
   form with only a FIDE ID and splits returned games by event.
2. RawPGNScout assets. This reads the local scout SQLite store and promotes
   only source-allowlisted games that match a Chinese FIDE ID.

The script writes per-player/per-event PGN files. Run sync_static_pgn.py after
promotion to refresh docs/data/index.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import html.parser
import json
import pathlib
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from source_http import open_response
from source_policy import require_chess_results_publication


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS_DATA = REPO_ROOT / "docs" / "data"
STATIC_PGN_ROOT = DOCS_DATA / "pgn"
REGISTRY_PLAYERS_JSON = DOCS_DATA / "registry" / "players.json"
INDEX_PLAYERS_JSON = DOCS_DATA / "index" / "players.json"
YOUTH_JSON = REPO_ROOT / "data" / "generated" / "youth-leaderboards.json"
MANUAL_ALIAS_CSV = REPO_ROOT / "data" / "manual" / "player-aliases.csv"
SOURCE_POLICY_CSV = REPO_ROOT / "data" / "manual" / "public-pgn-sources.csv"
DEFAULT_SCOUT_ROOT = pathlib.Path.home() / "Library" / "Application Support" / "ChinaChessPlayerPGN" / "RawPGNScout"
CHESS_RESULTS_FORM_URL = "https://chess-results.com/PartieSuche.aspx?lan=1"
USER_AGENT = "ChinaChessPlayerPGNPublicPromoter/1.0"


@dataclass
class PlayerProfile:
    fide_id: str
    names: set[str] = field(default_factory=set)


@dataclass
class PromotionStats:
    chessResultsPlayersScanned: int = 0
    scoutAssetsScanned: int = 0
    promotedFiles: int = 0
    promotedGames: int = 0
    skippedExisting: int = 0
    skippedSources: int = 0
    skippedUnmatched: int = 0
    skippedLowQuality: int = 0
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
            name = values.get("name")
            if name:
                self.fields[name] = values.get("value", "")


def main() -> int:
    parser = argparse.ArgumentParser(description="Promote public PGN into docs/data/pgn.")
    parser.add_argument("--player", action="append", default=[], help="FIDE ID to scan/promote; repeatable")
    parser.add_argument("--source", action="append", default=[], help="RawPGNScout source to promote; repeatable")
    parser.add_argument("--root", type=pathlib.Path, default=DEFAULT_SCOUT_ROOT, help="RawPGNScout root")
    parser.add_argument("--policy", type=pathlib.Path, default=SOURCE_POLICY_CSV, help="source redistribution policy CSV")
    parser.add_argument("--scan-chess-results", action="store_true", help="scan Chess-Results by FIDE ID")
    parser.add_argument("--promote-scout", action="store_true", help="promote allowlisted RawPGNScout assets")
    parser.add_argument("--max-players", type=int, default=25, help="Chess-Results player scan limit; 0 means no limit")
    parser.add_argument("--max-assets", type=int, default=0, help="RawPGNScout asset limit; 0 means no limit")
    parser.add_argument("--max-games", type=int, default=0, help="maximum promoted games; 0 means no limit")
    parser.add_argument("--delay", type=float, default=0.8, help="delay between Chess-Results player scans")
    parser.add_argument("--include-review-sources", action="store_true", help="allow policy rows with status=review")
    parser.add_argument("--allow-name-match", action="store_true", help="promote games matched by unique normalized player name")
    parser.add_argument("--skip-index", action="store_true", help="do not run sync_static_pgn.py after promotion")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.scan_chess_results and not args.promote_scout:
        args.scan_chess_results = True
        args.promote_scout = True
    if args.scan_chess_results:
        require_chess_results_publication()

    STATIC_PGN_ROOT.mkdir(parents=True, exist_ok=True)
    profiles = load_player_profiles()
    policies = load_source_policies(args.policy)
    stats = PromotionStats()

    if args.scan_chess_results:
        scan_chess_results_by_player(args, profiles, stats)
    if args.promote_scout:
        promote_scout_assets(args, profiles, policies, stats)

    if not args.dry_run and not args.skip_index:
        subprocess.run([sys.executable, str(REPO_ROOT / "Scripts" / "sync_static_pgn.py")], check=True)

    print(json.dumps(stats.__dict__, ensure_ascii=False, indent=2))
    return 1 if stats.errors else 0


def scan_chess_results_by_player(args: argparse.Namespace, profiles: dict[str, PlayerProfile], stats: PromotionStats) -> None:
    fide_ids = selected_fide_ids(args.player, profiles)
    if args.max_players:
        fide_ids = fide_ids[: args.max_players]
    existing_hashes = existing_game_hashes_by_fide()

    for fide_id in fide_ids:
        if args.max_games and stats.promotedGames >= args.max_games:
            break
        stats.chessResultsPlayersScanned += 1
        try:
            pgn = download_chess_results_player_pgn(fide_id)
            games = [game for game in split_pgn_games(pgn) if quality_game(game)]
            if not games:
                stats.skippedLowQuality += 1
                continue
            groups: dict[str, list[str]] = {}
            for game in games:
                game_hash = stable_game_hash(game)
                if game_hash in existing_hashes.get(fide_id, set()):
                    stats.skippedExisting += 1
                    continue
                headers = pgn_headers(game)
                event_key = event_key_for_headers("chess-results", headers)
                groups.setdefault(event_key, []).append(game)
            for event_key, event_games in groups.items():
                promoted = write_promoted_pgn("chess-results", event_key, fide_id, event_games, args.dry_run)
                if promoted:
                    stats.promotedFiles += 1
                    stats.promotedGames += len(event_games)
            if args.delay:
                time.sleep(args.delay)
        except Exception as error:  # noqa: BLE001 - batch promotion should continue.
            stats.errors.append(f"chess-results fide-{fide_id}: {error}")


def promote_scout_assets(
    args: argparse.Namespace,
    profiles: dict[str, PlayerProfile],
    policies: dict[str, dict[str, str]],
    stats: PromotionStats,
) -> None:
    db_path = args.root.expanduser() / "pgn_scout.sqlite"
    if not db_path.exists():
        return

    allowed_sources = {slug(source) for source in args.source}
    unique_names = unique_name_index(profiles)
    existing_hashes = existing_game_hashes_by_fide()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id, source, source_url, local_path, filtered_path, china_game_count
        FROM assets
        WHERE china_game_count > 0
        ORDER BY downloaded_at ASC
        """
    ).fetchall()
    conn.close()

    for row in rows[: args.max_assets or None]:
        source_slug = slug(row["source"])
        if allowed_sources and source_slug not in allowed_sources:
            continue
        if source_slug == "static-seed" and not allowed_sources:
            continue
        if not source_allowed(source_slug, policies, args.include_review_sources):
            stats.skippedSources += 1
            continue
        stats.scoutAssetsScanned += 1

        path_value = row["filtered_path"] or row["local_path"]
        path = pathlib.Path(path_value)
        if not path.is_absolute():
            path = args.root.expanduser() / path
        if not path.exists() or path.suffix.lower() != ".pgn":
            stats.skippedLowQuality += 1
            continue

        try:
            groups: dict[tuple[str, str], list[str]] = {}
            for game in split_pgn_games(path.read_text(encoding="utf-8", errors="replace")):
                if args.max_games and stats.promotedGames >= args.max_games:
                    break
                if not quality_game(game):
                    stats.skippedLowQuality += 1
                    continue
                headers = pgn_headers(game)
                fide_ids = matched_fide_ids(headers, profiles, unique_names, args.allow_name_match)
                if args.player:
                    allowed_players = {str(item) for item in args.player}
                    fide_ids = [fide_id for fide_id in fide_ids if fide_id in allowed_players]
                if not fide_ids:
                    stats.skippedUnmatched += 1
                    continue
                event_key = event_key_for_headers(source_slug, headers, asset_id=row["id"])
                game_hash = stable_game_hash(game)
                for fide_id in fide_ids:
                    if game_hash in existing_hashes.get(fide_id, set()):
                        stats.skippedExisting += 1
                        continue
                    groups.setdefault((event_key, fide_id), []).append(game)

            for (event_key, fide_id), games in groups.items():
                promoted = write_promoted_pgn(source_slug, event_key, fide_id, games, args.dry_run)
                if promoted:
                    stats.promotedFiles += 1
                    stats.promotedGames += len(games)
        except Exception as error:  # noqa: BLE001 - batch promotion should continue.
            stats.errors.append(f"{row['source']} asset {row['id']}: {error}")


def selected_fide_ids(requested: list[str], profiles: dict[str, PlayerProfile]) -> list[str]:
    if requested:
        return ordered_unique([str(item).strip() for item in requested if str(item).strip()])

    indexed: list[str] = []
    if INDEX_PLAYERS_JSON.exists():
        data = read_json(INDEX_PLAYERS_JSON)
        for player in data if isinstance(data, list) else data.get("players", []):
            fide_id = str(player.get("fideID") or "").strip()
            if fide_id:
                indexed.append(fide_id)
    return ordered_unique([*indexed, *sorted(profiles)])


def download_chess_results_player_pgn(fide_id: str) -> str:
    form = load_form(CHESS_RESULTS_FORM_URL)
    fields = dict(form["fields"])
    fields["ctl00$P1$Txt_FideID"] = fide_id
    fields["ctl00$P1$txt_dbkey"] = ""
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
    with open_url(request) as response:
        return decode_response(response.read())


def load_form(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with open_url(request) as response:
        final_url = response.geturl()
        html = decode_response(response.read())
    parser = FormParser(final_url)
    parser.feed(html)
    return {"base_url": final_url, "action_url": parser.action_url, "fields": parser.fields}


def write_promoted_pgn(source: str, event_key: str, fide_id: str, games: list[str], dry_run: bool) -> bool:
    if not games:
        return False
    source_slug = slug(source)
    event_slug = slug(event_key)
    path = STATIC_PGN_ROOT / source_slug / event_slug / f"fide-{fide_id}-{event_slug}.pgn"
    existing_games = split_pgn_games(path.read_text(encoding="utf-8", errors="replace")) if path.exists() else []
    merged = merge_games(existing_games, games)
    if len(merged) == len(existing_games):
        return False
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n\n".join(merged).strip() + "\n", encoding="utf-8")
    return True


def existing_game_hashes_by_fide() -> dict[str, set[str]]:
    hashes: dict[str, set[str]] = {}
    for path in STATIC_PGN_ROOT.glob("*/*/fide-*.pgn"):
        match = re.match(r"fide-(\d+)-", path.name)
        if not match:
            continue
        fide_id = match.group(1)
        values = hashes.setdefault(fide_id, set())
        for game in split_pgn_games(path.read_text(encoding="utf-8", errors="replace")):
            values.add(stable_game_hash(game))
    return hashes


def merge_games(existing: list[str], incoming: list[str]) -> list[str]:
    seen = {stable_game_hash(game) for game in existing}
    merged = list(existing)
    for game in incoming:
        key = stable_game_hash(game)
        if key not in seen:
            seen.add(key)
            merged.append(game.strip())
    return merged


def matched_fide_ids(
    headers: dict[str, str],
    profiles: dict[str, PlayerProfile],
    unique_names: dict[str, str],
    allow_name_match: bool,
) -> list[str]:
    fide_ids: list[str] = []
    for key in ["WhiteFideId", "WhiteFideID", "BlackFideId", "BlackFideID"]:
        fide_id = headers.get(key, "").strip()
        if fide_id in profiles:
            fide_ids.append(fide_id)
    if allow_name_match:
        for key in ["White", "Black"]:
            fide_id = unique_names.get(normalize_name(headers.get(key, "")))
            if fide_id:
                fide_ids.append(fide_id)
    return ordered_unique(fide_ids)


def load_player_profiles() -> dict[str, PlayerProfile]:
    profiles: dict[str, PlayerProfile] = {}

    def add_player(player: dict[str, Any]) -> None:
        fide_id = str(player.get("fideID") or "").strip()
        if not fide_id:
            return
        profile = profiles.setdefault(fide_id, PlayerProfile(fide_id=fide_id))
        profile.names.add(fide_id)
        for key in ["displayName", "name", "chineseName", "pinyin"]:
            value = str(player.get(key) or "").strip()
            if value:
                profile.names.add(value)
        for alias in player.get("aliases", []) or []:
            value = str(alias).strip()
            if value:
                profile.names.add(value)

    for path in [REGISTRY_PLAYERS_JSON, INDEX_PLAYERS_JSON, YOUTH_JSON]:
        if not path.exists():
            continue
        data = read_json(path)
        players = data.get("players", []) if isinstance(data, dict) else data
        for player in players:
            if isinstance(player, dict):
                add_player(player)

    if MANUAL_ALIAS_CSV.exists():
        with MANUAL_ALIAS_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                fide_id = str(row.get("fide_id") or "").strip()
                if not fide_id:
                    continue
                profile = profiles.setdefault(fide_id, PlayerProfile(fide_id=fide_id))
                for key in ["chinese_name", "pinyin_name"]:
                    value = str(row.get(key) or "").strip()
                    if value:
                        profile.names.add(value)
                for alias in re.split(r"[|;]", row.get("aliases") or ""):
                    if alias.strip():
                        profile.names.add(alias.strip())
    return profiles


def unique_name_index(profiles: dict[str, PlayerProfile]) -> dict[str, str]:
    owner: dict[str, str | None] = {}
    for fide_id, profile in profiles.items():
        for name in profile.names:
            key = normalize_name(name)
            if not key or key.isdigit():
                continue
            if key in owner and owner[key] != fide_id:
                owner[key] = None
            else:
                owner[key] = fide_id
    return {key: value for key, value in owner.items() if value}


def load_source_policies(path: pathlib.Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {
            "chess-results": {"redistributable": "yes", "status": "approved"},
            "lichess": {"redistributable": "yes", "status": "approved"},
        }
    policies: dict[str, dict[str, str]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            source = slug(row.get("source"))
            if source:
                policies[source] = {key: str(value or "").strip() for key, value in row.items()}
    return policies


def source_allowed(source: str, policies: dict[str, dict[str, str]], include_review: bool) -> bool:
    policy = policies.get(slug(source))
    if not policy:
        return False
    redistributable = policy.get("redistributable", "").casefold()
    status = policy.get("status", "").casefold()
    if redistributable in {"yes", "true", "1"} and status in {"approved", "yes", "public", ""}:
        return True
    return include_review and status == "review"


def event_key_for_headers(source: str, headers: dict[str, str], asset_id: str = "") -> str:
    event = headers.get("Event", "")
    site = headers.get("Site", "")
    date = normalize_pgn_date(headers.get("EventDate") or headers.get("Date") or "")
    year = date[:4] if date else "undated"
    base = slug(event)[:52] or slug(source)
    suffix = hashlib.sha256("|".join([source, event, site, date, asset_id]).encode("utf-8")).hexdigest()[:8]
    return f"{base}-{year}-{suffix}"


def quality_game(game: str) -> bool:
    if "<html" in game[:200].casefold():
        return False
    headers = pgn_headers(game)
    if not headers.get("Event") or not headers.get("White") or not headers.get("Black"):
        return False
    if low_quality_event(headers):
        return False
    return bool(re.search(r"\]\s*\n\s*\n", game) or re.search(r"\n1\.", game))


def low_quality_event(headers: dict[str, str]) -> bool:
    event = normalize_name(headers.get("Event", ""))
    site = normalize_name(headers.get("Site", ""))
    combined = event + " " + site
    return any(term in combined for term in ["examen", "exmane"])


def split_pgn_games(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    matches = list(re.finditer(r'^\[Event\s+"', normalized, flags=re.MULTILINE | re.IGNORECASE))
    games = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
        game = normalized[start:end].strip()
        if game:
            games.append(game)
    return games


def pgn_headers(game: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for match in re.finditer(r'^\[([A-Za-z0-9_]+)\s+"(.*)"\]', game, flags=re.MULTILINE):
        headers[match.group(1)] = match.group(2)
    return headers


def stable_game_hash(game: str) -> str:
    normalized = re.sub(r"\s+", " ", game).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def normalize_pgn_date(text: str) -> str:
    if re.match(r"^\d{4}\.\d{2}\.\d{2}$", text):
        return text.replace(".", "-")
    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        return text
    return ""


def read_json(path: pathlib.Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def decode_response(data: bytes) -> str:
    for encoding in ("utf-8", "gb18030", "iso-8859-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def open_url(request: urllib.request.Request):
    return open_response(request, timeout=90, retries=2)


def normalize_name(value: str) -> str:
    return re.sub(r"[\s,.'`’\"()，。·_\-]+", "", str(value or "").casefold())


def slug(value: Any) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return cleaned or "unknown"


def ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
