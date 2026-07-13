#!/usr/bin/env python3
"""Local raw PGN scout for Chinese chess assets.

This script intentionally writes raw assets outside the repository by default:

~/Library/Application Support/ChinaChessPlayerPGN/RawPGNScout/

The repository keeps only the scout code, docs, and small curated metadata.
Large PGN/ZIP/ZST assets stay local until they are normalized and intentionally
promoted into docs/data/pgn by the static sync pipeline.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import html.parser
import io
import json
import os
import pathlib
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from typing import Any

from source_http import open_response
from source_policy import require_local_collector


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS_DATA = REPO_ROOT / "docs" / "data"
STATIC_INDEX_ROOT = DOCS_DATA / "index"
REGISTRY_ROOT = DOCS_DATA / "registry"
MANUAL_ALIAS_CSV = REPO_ROOT / "data" / "manual" / "player-aliases.csv"
DEFAULT_APP_SUPPORT = pathlib.Path.home() / "Library" / "Application Support" / "ChinaChessPlayerPGN"
DEFAULT_ROOT = DEFAULT_APP_SUPPORT / "RawPGNScout"
CHESS_RESULTS_FORM_URL = "https://chess-results.com/PartieSuche.aspx?lan=1"
LICHESS_DATABASE_URL = "https://database.lichess.org/"
USER_AGENT = "ChinaChessPlayerPGNScout/1.0"


@dataclass
class ScoutStats:
    assets_seen: int = 0
    assets_added: int = 0
    assets_skipped: int = 0
    requests: int = 0
    games: int = 0
    china_games: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class ChessResultsTarget:
    fide_id: str
    tournament_id: str
    event_name: str = ""
    source_url: str = ""


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


class LinkParser(html.parser.HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        href = values.get("href")
        if tag.lower() == "a" and href:
            self.links.append(urllib.parse.urljoin(self.base_url, href))


def main() -> int:
    parser = argparse.ArgumentParser(description="Local raw PGN scout for Chinese chess assets.")
    parser.add_argument("--root", type=pathlib.Path, default=DEFAULT_ROOT, help="local scout asset root")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="create local scout directories and SQLite manifest")

    seed_targets = subparsers.add_parser("seed-chess-results-targets", help="export Chess-Results targets from local indexes")
    seed_targets.add_argument("--output", type=pathlib.Path)

    cr = subparsers.add_parser("fetch-chess-results", help="probe Chess-Results PGN by FIDE ID and TournamentID")
    cr.add_argument("--targets", type=pathlib.Path, help="CSV from seed-chess-results-targets")
    cr.add_argument("--player", action="append", default=[], help="limit to FIDE ID; repeatable")
    cr.add_argument("--tournament", action="append", default=[], help="explicit TournamentID; repeatable")
    cr.add_argument("--max-requests", type=int, default=20)
    cr.add_argument("--delay", type=float, default=0.8)
    cr.add_argument("--dry-run", action="store_true")

    lichess = subparsers.add_parser("fetch-lichess", help="download Lichess broadcast/database PGN assets")
    lichess.add_argument("--url", action="append", default=[], help="broadcast page, raw PGN, PGN.ZST, or API URL")
    lichess.add_argument("--database-broadcasts", action="store_true", help="scan database.lichess.org for broadcast archives")
    lichess.add_argument("--year", action="append", default=[], help="year filter for Lichess database archive links")
    lichess.add_argument("--max-downloads", type=int, default=10)
    lichess.add_argument("--extract", action="store_true", help="extract PGN from .zst/.zip when possible")
    lichess.add_argument("--dry-run", action="store_true")

    chesscom = subparsers.add_parser("fetch-chesscom", help="download Chess.com public monthly PGN archives")
    chesscom.add_argument("--username", action="append", default=[], help="Chess.com username; repeatable")
    chesscom.add_argument("--country", default="", help="country code for player discovery, e.g. CN")
    chesscom.add_argument("--year", action="append", default=[], help="year filter")
    chesscom.add_argument("--month", action="append", default=[], help="month filter 1-12 or 01-12")
    chesscom.add_argument("--max-users", type=int, default=25)
    chesscom.add_argument("--max-downloads", type=int, default=20)
    chesscom.add_argument("--dry-run", action="store_true")

    twic = subparsers.add_parser("fetch-twic", help="download TWIC ZIP files by issue number")
    twic.add_argument("--start", type=int, required=True)
    twic.add_argument("--end", type=int, required=True)
    twic.add_argument("--extract", action="store_true")
    twic.add_argument("--dry-run", action="store_true")

    ingest = subparsers.add_parser("ingest", help="ingest local PGN/ZIP/ZST files")
    ingest.add_argument("paths", nargs="+", type=pathlib.Path)
    ingest.add_argument("--source", default="local")
    ingest.add_argument("--extract", action="store_true")

    scan = subparsers.add_parser("scan", help="scan local raw assets and write China-filtered PGN extracts")
    scan.add_argument("--source", action="append", default=[])
    scan.add_argument("--limit-assets", type=int, default=0)

    report = subparsers.add_parser("report", help="print local scout status")
    report.add_argument("--write", type=pathlib.Path, help="optional markdown report path")

    args = parser.parse_args()
    if args.command in {"fetch-chess-results", "fetch-lichess", "fetch-chesscom", "fetch-twic"}:
        require_local_collector(args.command)
    root = args.root.expanduser()
    ensure_store(root)

    if args.command == "init":
        print(json.dumps(store_summary(root), ensure_ascii=False, indent=2))
        return 0
    if args.command == "seed-chess-results-targets":
        output = args.output or root / "manifests" / "chess-results-targets.csv"
        targets = chess_results_targets()
        write_targets_csv(output, targets)
        print(json.dumps({"targets": len(targets), "output": str(output)}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "fetch-chess-results":
        stats = fetch_chess_results(root, args)
        print(json.dumps(stats.__dict__, ensure_ascii=False, indent=2))
        return 0 if not stats.errors else 1
    if args.command == "fetch-lichess":
        stats = fetch_lichess(root, args)
        print(json.dumps(stats.__dict__, ensure_ascii=False, indent=2))
        return 0 if not stats.errors else 1
    if args.command == "fetch-chesscom":
        stats = fetch_chesscom(root, args)
        print(json.dumps(stats.__dict__, ensure_ascii=False, indent=2))
        return 0 if not stats.errors else 1
    if args.command == "fetch-twic":
        stats = fetch_twic(root, args)
        print(json.dumps(stats.__dict__, ensure_ascii=False, indent=2))
        return 0 if not stats.errors else 1
    if args.command == "ingest":
        stats = ingest_paths(root, args.paths, args.source, args.extract)
        print(json.dumps(stats.__dict__, ensure_ascii=False, indent=2))
        return 0 if not stats.errors else 1
    if args.command == "scan":
        stats = scan_assets(root, args.source, args.limit_assets)
        print(json.dumps(stats.__dict__, ensure_ascii=False, indent=2))
        return 0 if not stats.errors else 1
    if args.command == "report":
        text = render_report(root)
        if args.write:
            args.write.parent.mkdir(parents=True, exist_ok=True)
            args.write.write_text(text, encoding="utf-8")
        print(text)
        return 0

    return 2


def ensure_store(root: pathlib.Path) -> None:
    for child in ["raw", "extracted", "filtered/china", "manifests", "reports"]:
        (root / child).mkdir(parents=True, exist_ok=True)
    conn = connect(root)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS assets (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            kind TEXT NOT NULL,
            source_url TEXT,
            local_path TEXT NOT NULL,
            sha256 TEXT NOT NULL UNIQUE,
            bytes INTEGER NOT NULL,
            downloaded_at TEXT NOT NULL,
            game_count INTEGER NOT NULL DEFAULT 0,
            china_game_count INTEGER NOT NULL DEFAULT 0,
            filtered_path TEXT,
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS games (
            id TEXT PRIMARY KEY,
            asset_id TEXT NOT NULL,
            game_index INTEGER NOT NULL,
            event TEXT,
            site TEXT,
            date TEXT,
            white TEXT,
            black TEXT,
            result TEXT,
            white_fide_id TEXT,
            black_fide_id TEXT,
            is_china INTEGER NOT NULL,
            FOREIGN KEY(asset_id) REFERENCES assets(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_games_asset ON games(asset_id);
        CREATE INDEX IF NOT EXISTS idx_games_china ON games(is_china);
        """
    )
    conn.commit()
    conn.close()


def connect(root: pathlib.Path) -> sqlite3.Connection:
    conn = sqlite3.connect(root / "pgn_scout.sqlite")
    conn.row_factory = sqlite3.Row
    return conn


def store_summary(root: pathlib.Path) -> dict[str, Any]:
    conn = connect(root)
    row = conn.execute(
        """
        SELECT COUNT(*) AS assets,
               COALESCE(SUM(bytes), 0) AS bytes,
               COALESCE(SUM(game_count), 0) AS games,
               COALESCE(SUM(china_game_count), 0) AS china_games
        FROM assets
        """
    ).fetchone()
    by_source = [
        dict(item)
        for item in conn.execute(
            "SELECT source, COUNT(*) AS assets, SUM(game_count) AS games, SUM(china_game_count) AS china_games FROM assets GROUP BY source ORDER BY source"
        )
    ]
    conn.close()
    return {"root": str(root), **dict(row), "bySource": by_source}


def chess_results_targets() -> list[ChessResultsTarget]:
    targets: dict[tuple[str, str], ChessResultsTarget] = {}
    for target in static_chess_results_targets():
        targets[(target.fide_id, target.tournament_id)] = target
    return sorted(targets.values(), key=lambda item: (item.tournament_id, item.fide_id))


def static_chess_results_targets() -> list[ChessResultsTarget]:
    targets: list[ChessResultsTarget] = []
    players_root = STATIC_INDEX_ROOT / "players"
    for path in sorted(players_root.glob("fide-*.json")):
        data = read_json(path)
        fide_id = str(data.get("fideID") or "").strip()
        for event in data.get("events", []):
            if slug(event.get("source", "")) != "chess-results":
                continue
            tournament_id = str(event.get("tournamentID") or "").strip()
            if fide_id and tournament_id:
                targets.append(
                    ChessResultsTarget(
                        fide_id=fide_id,
                        tournament_id=tournament_id,
                        event_name=str(event.get("name") or ""),
                        source_url=str(event.get("sourceURL") or ""),
                    )
                )
    return targets


def write_targets_csv(path: pathlib.Path, targets: list[ChessResultsTarget]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["fide_id", "tournament_id", "event_name", "source_url"])
        writer.writeheader()
        for target in targets:
            writer.writerow(target.__dict__)


def read_targets_csv(path: pathlib.Path) -> list[ChessResultsTarget]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [
            ChessResultsTarget(
                fide_id=str(row.get("fide_id") or "").strip(),
                tournament_id=str(row.get("tournament_id") or "").strip(),
                event_name=str(row.get("event_name") or "").strip(),
                source_url=str(row.get("source_url") or "").strip(),
            )
            for row in csv.DictReader(handle)
            if row.get("fide_id") and row.get("tournament_id")
        ]


def fetch_chess_results(root: pathlib.Path, args: argparse.Namespace) -> ScoutStats:
    stats = ScoutStats()
    targets = read_targets_csv(args.targets) if args.targets else chess_results_targets()
    if args.player:
        allowed = set(args.player)
        targets = [target for target in targets if target.fide_id in allowed]
    if args.tournament:
        explicit = [
            ChessResultsTarget(fide_id=fide_id, tournament_id=tournament_id)
            for fide_id in (args.player or load_known_fide_ids()[:50])
            for tournament_id in args.tournament
        ]
        targets = explicit if not args.targets else targets + explicit

    seen: set[tuple[str, str]] = set()
    unique_targets: list[ChessResultsTarget] = []
    for target in targets:
        key = (target.fide_id, target.tournament_id)
        if key not in seen:
            seen.add(key)
            unique_targets.append(target)

    for target in unique_targets:
        if args.max_requests and stats.requests >= args.max_requests:
            break
        stats.requests += 1
        try:
            text = download_chess_results_pgn(target.fide_id, target.tournament_id)
            if count_pgn_games(text) == 0:
                stats.assets_skipped += 1
                continue
            relative = pathlib.Path("raw") / "chess-results" / f"tnr{target.tournament_id}" / f"fide-{target.fide_id}-{target.tournament_id}.pgn"
            if args.dry_run:
                stats.assets_seen += 1
            else:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text.strip() + "\n", encoding="utf-8")
                added, games, china_games = ingest_asset(
                    root,
                    path,
                    "chess-results",
                    f"tnr{target.tournament_id}",
                    target.source_url or CHESS_RESULTS_FORM_URL,
                )
                if added:
                    stats.assets_added += 1
                    stats.games += games
                    stats.china_games += china_games
            if args.delay:
                time.sleep(args.delay)
        except Exception as error:
            stats.errors.append(f"{target.fide_id} tnr{target.tournament_id}: {error}")
    return stats


def download_chess_results_pgn(fide_id: str, tournament_id: str) -> str:
    form = load_form(CHESS_RESULTS_FORM_URL)
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


def fetch_lichess(root: pathlib.Path, args: argparse.Namespace) -> ScoutStats:
    stats = ScoutStats()
    urls: list[str] = []
    for url in args.url:
        urls.extend(discover_lichess_urls(url))
    if args.database_broadcasts:
        urls.extend(discover_lichess_database_broadcasts(args.year))

    urls = ordered_unique([url for url in urls if is_pgn_like_url(url)])
    for url in urls:
        if args.max_downloads and stats.requests >= args.max_downloads:
            break
        stats.requests += 1
        try:
            if args.dry_run:
                stats.assets_seen += 1
                continue
            path = download_asset(root, url, "lichess")
            added, games, china_games = ingest_asset(root, path, "lichess", kind_from_path(path), url)
            if added:
                stats.assets_added += 1
                stats.games += games
                stats.china_games += china_games
            if args.extract:
                added_count, games, china_games = extract_container(root, path, "lichess", url)
                stats.assets_added += added_count
                stats.games += games
                stats.china_games += china_games
        except Exception as error:
            stats.errors.append(f"{url}: {error}")
    return stats


def discover_lichess_urls(url: str) -> list[str]:
    if is_pgn_like_url(url):
        return [url]
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with open_url(request) as response:
        final_url = response.geturl()
        html = decode_response(response.read())
    parser = LinkParser(final_url)
    parser.feed(html)
    links = [link for link in parser.links if is_pgn_like_url(link) or "/api/broadcast/" in link]
    api_links = []
    for link in links:
        if "/api/broadcast/" in link and not is_pgn_like_url(link):
            api_links.append(link.rstrip("/") + ".pgn")
        else:
            api_links.append(link)
    return api_links


def discover_lichess_database_broadcasts(years: list[str]) -> list[str]:
    request = urllib.request.Request(LICHESS_DATABASE_URL, headers={"User-Agent": USER_AGENT})
    with open_url(request) as response:
        final_url = response.geturl()
        html = decode_response(response.read())
    parser = LinkParser(final_url)
    parser.feed(html)
    year_filter = set(years)
    links = [
        link
        for link in parser.links
        if "broadcast" in link.lower() and is_pgn_like_url(link) and (not year_filter or any(year in link for year in year_filter))
    ]
    return links


def fetch_twic(root: pathlib.Path, args: argparse.Namespace) -> ScoutStats:
    stats = ScoutStats()
    for issue in range(args.start, args.end + 1):
        urls = twic_candidate_urls(issue)
        downloaded = False
        for url in urls:
            try:
                if args.dry_run:
                    stats.assets_seen += 1
                    downloaded = True
                    break
                path = download_asset(root, url, "twic")
                added, games, china_games = ingest_asset(root, path, "twic", "zip", url)
                if added:
                    stats.assets_added += 1
                    stats.games += games
                    stats.china_games += china_games
                if args.extract:
                    added_count, games, china_games = extract_container(root, path, "twic", url)
                    stats.assets_added += added_count
                    stats.games += games
                    stats.china_games += china_games
                downloaded = True
                break
            except Exception:
                continue
        stats.requests += 1
        if not downloaded:
            stats.errors.append(f"TWIC issue {issue}: no candidate URL succeeded")
    return stats


def fetch_chesscom(root: pathlib.Path, args: argparse.Namespace) -> ScoutStats:
    stats = ScoutStats()
    usernames = ordered_unique([*args.username, *discover_chesscom_country_players(args.country, args.max_users)])
    years = {str(year) for year in args.year}
    months = {f"{int(month):02d}" for month in args.month} if args.month else set()

    for username in usernames:
        try:
            archive_urls = chesscom_archive_urls(username, years, months)
            for archive_url in archive_urls:
                if args.max_downloads and stats.requests >= args.max_downloads:
                    return stats
                stats.requests += 1
                pgn_url = archive_url.rstrip("/") + "/pgn"
                if args.dry_run:
                    stats.assets_seen += 1
                    continue
                path = download_chesscom_pgn(root, username, pgn_url)
                added, games, china_games = ingest_asset(root, path, "chesscom", "pgn", pgn_url)
                if added:
                    stats.assets_added += 1
                    stats.games += games
                    stats.china_games += china_games
        except Exception as error:
            stats.errors.append(f"{username}: {error}")
    return stats


def discover_chesscom_country_players(country: str, max_users: int) -> list[str]:
    if not country:
        return []
    url = f"https://api.chess.com/pub/country/{urllib.parse.quote(country.upper())}/players"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with open_url(request) as response:
        data = json.loads(decode_response(response.read()))
    players = [str(username) for username in data.get("players", [])]
    return players[:max_users] if max_users else players


def chesscom_archive_urls(username: str, years: set[str], months: set[str]) -> list[str]:
    url = f"https://api.chess.com/pub/player/{urllib.parse.quote(username)}/games/archives"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with open_url(request) as response:
        data = json.loads(decode_response(response.read()))
    urls = [str(item) for item in data.get("archives", [])]
    if years:
        urls = [url for url in urls if any(f"/{year}/" in url for year in years)]
    if months:
        urls = [url for url in urls if pathlib.PurePosixPath(urllib.parse.urlparse(url).path).name in months]
    return urls


def download_chesscom_pgn(root: pathlib.Path, username: str, pgn_url: str) -> pathlib.Path:
    parsed = urllib.parse.urlparse(pgn_url)
    pieces = pathlib.PurePosixPath(parsed.path).parts
    year = pieces[-3] if len(pieces) >= 3 else "unknown-year"
    month = pieces[-2] if len(pieces) >= 2 else "unknown-month"
    target = root / "raw" / "chesscom" / slug(username) / f"{year}-{month}.pgn"
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(pgn_url, headers={"User-Agent": USER_AGENT, "Accept": "application/x-chess-pgn,*/*"})
    with open_url(request) as response, target.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    return target


def twic_candidate_urls(issue: int) -> list[str]:
    return [
        f"https://theweekinchess.com/zips/twic{issue}g.zip",
        f"https://theweekinchess.com/zips/twic{issue}.zip",
        f"https://theweekinchess.com/assets/files/pgn/twic{issue}g.zip",
        f"https://theweekinchess.com/assets/files/pgn/twic{issue}.zip",
    ]


def ingest_paths(root: pathlib.Path, paths: list[pathlib.Path], source: str, extract: bool) -> ScoutStats:
    stats = ScoutStats()
    for input_path in paths:
        try:
            if input_path.is_dir():
                children = [path for path in input_path.rglob("*") if path.is_file() and is_supported_asset(path)]
            else:
                children = [input_path]
            for child in children:
                target = copy_local_asset(root, child, source)
                added, games, china_games = ingest_asset(root, target, source, kind_from_path(target), str(child))
                if added:
                    stats.assets_added += 1
                    stats.games += games
                    stats.china_games += china_games
                if extract:
                    added_count, games, china_games = extract_container(root, target, source, str(child))
                    stats.assets_added += added_count
                    stats.games += games
                    stats.china_games += china_games
        except Exception as error:
            stats.errors.append(f"{input_path}: {error}")
    return stats


def download_asset(root: pathlib.Path, url: str, source: str) -> pathlib.Path:
    parsed = urllib.parse.urlparse(url)
    file_name = pathlib.Path(parsed.path).name or f"{sha256_text(url)[:12]}.pgn"
    if not is_supported_name(file_name):
        file_name += ".pgn"
    target = root / "raw" / slug(source) / file_name
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/x-chess-pgn,*/*"})
    with open_url(request) as response, target.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    return target


def copy_local_asset(root: pathlib.Path, input_path: pathlib.Path, source: str) -> pathlib.Path:
    target = root / "raw" / slug(source) / input_path.name
    target.parent.mkdir(parents=True, exist_ok=True)
    if input_path.resolve() != target.resolve():
        shutil.copy2(input_path, target)
    return target


def extract_container(root: pathlib.Path, path: pathlib.Path, source: str, source_url: str) -> tuple[int, int, int]:
    count = 0
    games = 0
    china_games = 0
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                if not name.lower().endswith(".pgn"):
                    continue
                target = root / "extracted" / slug(source) / slug(path.stem) / pathlib.Path(name).name
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(name) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                added, parsed_games, parsed_china_games = ingest_asset(root, target, source, "pgn", source_url)
                if added:
                    count += 1
                    games += parsed_games
                    china_games += parsed_china_games
    elif str(path).lower().endswith(".pgn.zst") or path.suffix.lower() == ".zst":
        target = root / "extracted" / slug(source) / f"{path.name}.pgn"
        target.parent.mkdir(parents=True, exist_ok=True)
        decompress_zst(path, target)
        added, games, china_games = ingest_asset(root, target, source, "pgn", source_url)
        count += 1 if added else 0
    return count, games, china_games


def decompress_zst(source: pathlib.Path, target: pathlib.Path) -> None:
    try:
        import zstandard as zstd  # type: ignore[import-not-found]

        with source.open("rb") as src, target.open("wb") as dst:
            reader = zstd.ZstdDecompressor().stream_reader(src)
            shutil.copyfileobj(reader, dst)
        return
    except Exception:
        pass

    zstd_bin = shutil.which("zstd")
    if not zstd_bin:
        raise RuntimeError("Need python zstandard or zstd binary to extract .zst")
    with target.open("wb") as dst:
        subprocess.run([zstd_bin, "-dc", str(source)], check=True, stdout=dst)


def ingest_asset(root: pathlib.Path, path: pathlib.Path, source: str, kind: str, source_url: str = "") -> tuple[bool, int, int]:
    conn = connect(root)
    sha = sha256_file(path)
    asset_id = sha[:16]
    existing = conn.execute("SELECT id FROM assets WHERE sha256 = ?", [sha]).fetchone()
    if existing:
        conn.close()
        return False, 0, 0

    games = parse_games_from_asset(path)
    aliases = load_china_aliases()
    filtered_games: list[str] = []
    rows = []
    for index, game in enumerate(games, start=1):
        headers = pgn_headers(game)
        is_china = is_chinese_game(headers, aliases)
        if is_china:
            filtered_games.append(game)
        game_id = f"{asset_id}-{index:06d}"
        rows.append(
            [
                game_id,
                asset_id,
                index,
                headers.get("Event", ""),
                headers.get("Site", ""),
                headers.get("Date", ""),
                headers.get("White", ""),
                headers.get("Black", ""),
                headers.get("Result", ""),
                headers.get("WhiteFideId", headers.get("WhiteFideID", "")),
                headers.get("BlackFideId", headers.get("BlackFideID", "")),
                1 if is_china else 0,
            ]
        )

    filtered_path = ""
    if filtered_games:
        filtered = root / "filtered" / "china" / slug(source) / f"{asset_id}.pgn"
        filtered.parent.mkdir(parents=True, exist_ok=True)
        filtered.write_text("\n\n".join(filtered_games).strip() + "\n", encoding="utf-8")
        filtered_path = str(filtered.relative_to(root))

    conn.execute(
        """
        INSERT INTO assets(id, source, kind, source_url, local_path, sha256, bytes, downloaded_at, game_count, china_game_count, filtered_path, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            asset_id,
            source,
            kind,
            source_url,
            str(path.relative_to(root)) if path.is_relative_to(root) else str(path),
            sha,
            path.stat().st_size,
            dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
            len(games),
            len(filtered_games),
            filtered_path,
            "",
        ],
    )
    conn.executemany(
        """
        INSERT INTO games(id, asset_id, game_index, event, site, date, white, black, result, white_fide_id, black_fide_id, is_china)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    conn.close()
    return True, len(games), len(filtered_games)


def scan_assets(root: pathlib.Path, sources: list[str], limit_assets: int) -> ScoutStats:
    stats = ScoutStats()
    allowed = {slug(source) for source in sources}
    all_paths = [
        path
        for base in [root / "raw", root / "extracted"]
        for path in base.rglob("*")
        if path.is_file() and is_supported_asset(path) and (not allowed or slug(path.parts[len(base.parts)]) in allowed)
    ]
    for path in all_paths[: limit_assets or None]:
        try:
            added, games, china_games = ingest_asset(
                root,
                path,
                path.parts[path.parts.index("raw") + 1] if "raw" in path.parts else "extracted",
                kind_from_path(path),
            )
            stats.assets_seen += 1
            if added:
                stats.assets_added += 1
                stats.games += games
                stats.china_games += china_games
        except Exception as error:
            stats.errors.append(f"{path}: {error}")
    return stats


def parse_games_from_asset(path: pathlib.Path) -> list[str]:
    if path.suffix.lower() != ".pgn":
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    return split_pgn_games(text)


def split_pgn_games(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    matches = list(re.finditer(r'^\[Event\s+"', normalized, flags=re.MULTILINE | re.IGNORECASE))
    if not matches:
        return []
    games = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
        game = normalized[start:end].strip()
        if game:
            games.append(game)
    return games


def pgn_headers(game: str) -> dict[str, str]:
    headers = {}
    for match in re.finditer(r'^\[([A-Za-z0-9_]+)\s+"(.*)"\]', game, flags=re.MULTILINE):
        headers[match.group(1)] = match.group(2)
    return headers


def is_chinese_game(headers: dict[str, str], aliases: dict[str, set[str]]) -> bool:
    fide_ids = aliases["fide_ids"]
    name_aliases = aliases["name_aliases"]
    event_terms = aliases["event_terms"]
    site_terms = aliases["site_terms"]

    if headers.get("WhiteFideId") in fide_ids or headers.get("WhiteFideID") in fide_ids:
        return True
    if headers.get("BlackFideId") in fide_ids or headers.get("BlackFideID") in fide_ids:
        return True

    white = normalize_name(headers.get("White", ""))
    black = normalize_name(headers.get("Black", ""))
    if white in name_aliases or black in name_aliases:
        return True

    event = normalize_text(headers.get("Event", ""))
    site = normalize_text(headers.get("Site", ""))
    return any(term in event for term in event_terms) or any(term in site for term in site_terms)


def load_china_aliases() -> dict[str, set[str]]:
    fide_ids: set[str] = set()
    name_aliases: set[str] = set()

    def add_player(player: dict[str, Any]) -> None:
        fide_id = str(player.get("fideID") or "").strip()
        if fide_id:
            fide_ids.add(fide_id)
        for key in ["displayName", "name", "chineseName", "pinyin"]:
            value = player.get(key)
            if value:
                name_aliases.add(normalize_name(str(value)))
        for alias in player.get("aliases", []) or []:
            name_aliases.add(normalize_name(str(alias)))

    for path in [STATIC_INDEX_ROOT / "players.json", REGISTRY_ROOT / "players.json", DOCS_DATA / "youth-leaderboards.json"]:
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
                if row.get("fide_id"):
                    fide_ids.add(row["fide_id"].strip())
                for key in ["chinese_name", "pinyin_name"]:
                    if row.get(key):
                        name_aliases.add(normalize_name(row[key]))
                for alias in re.split(r"[|;]", row.get("aliases") or ""):
                    if alias.strip():
                        name_aliases.add(normalize_name(alias))

    return {
        "fide_ids": fide_ids,
        "name_aliases": {value for value in name_aliases if value},
        "event_terms": {"china", "chinese", "chn", "中国", "全国", "李成智", "甲级", "棋协"},
        "site_terms": {"china", "chn", "中国", "beijing", "shanghai", "shenzhen", "hangzhou", "qingdao", "wuxi"},
    }


def load_known_fide_ids() -> list[str]:
    aliases = load_china_aliases()
    return sorted(aliases["fide_ids"])


def count_pgn_games(text: str) -> int:
    return len(re.findall(r'^\[Event\s+"', text, flags=re.MULTILINE | re.IGNORECASE))


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


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def is_supported_asset(path: pathlib.Path) -> bool:
    return is_supported_name(path.name)


def is_supported_name(name: str) -> bool:
    lowered = name.lower()
    return lowered.endswith((".pgn", ".pgn.zip", ".zip", ".pgn.zst", ".zst"))


def is_pgn_like_url(url: str) -> bool:
    path = urllib.parse.urlparse(url).path.lower()
    return path.endswith((".pgn", ".pgn.zip", ".zip", ".pgn.zst", ".zst")) or "/api/broadcast/" in path


def kind_from_path(path: pathlib.Path) -> str:
    lowered = path.name.lower()
    if lowered.endswith(".pgn.zst") or lowered.endswith(".zst"):
        return "pgn.zst"
    if lowered.endswith(".zip"):
        return "zip"
    if lowered.endswith(".pgn"):
        return "pgn"
    return path.suffix.lower().lstrip(".") or "unknown"


def normalize_name(value: str) -> str:
    return re.sub(r"[\s,.'’\"()，。·_\-]+", "", str(value or "").casefold())


def normalize_text(value: str) -> str:
    return str(value or "").casefold()


def slug(value: Any) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return cleaned or "unknown"


def ordered_unique(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def render_report(root: pathlib.Path) -> str:
    summary = store_summary(root)
    conn = connect(root)
    recent = [
        dict(row)
        for row in conn.execute(
            """
            SELECT id, source, kind, local_path, game_count, china_game_count, downloaded_at
            FROM assets
            ORDER BY downloaded_at DESC
            LIMIT 20
            """
        )
    ]
    conn.close()

    lines = [
        "# PGN Scout Report",
        "",
        f"- Root: `{summary['root']}`",
        f"- Assets: {summary['assets']}",
        f"- Bytes: {summary['bytes']}",
        f"- Games: {summary['games']}",
        f"- China games: {summary['china_games']}",
        "",
        "## By Source",
        "",
    ]
    for row in summary["bySource"]:
        lines.append(f"- {row['source']}: {row['assets']} assets, {row['games'] or 0} games, {row['china_games'] or 0} China games")
    lines.extend(["", "## Recent Assets", ""])
    for row in recent:
        lines.append(
            f"- `{row['id']}` {row['source']} {row['kind']}: {row['game_count']} games, {row['china_game_count']} China games, `{row['local_path']}`"
        )
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
