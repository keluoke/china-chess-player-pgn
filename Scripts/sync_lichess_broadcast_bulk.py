#!/usr/bin/env python3
"""Mirror and index the Lichess broadcast PGN bulk dataset.

The Lichess broadcast archive is the fastest legal path to a million-game
static tournament PGN backbone. The raw archive stays compressed as monthly
.pgn.zst shards, while youth filters are generated as much smaller stage PGN
packs and JSON indexes.
"""

from __future__ import annotations

import argparse
import codecs
import datetime as dt
import hashlib
import html.parser
import json
import os
import pathlib
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Iterator

from source_http import download_to_path, fetch_bytes
from stable_json import write_json as write_stable_json


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
PUBLIC_DOCS_DATA = REPO_ROOT / "docs" / "data"
DOCS_DATA = pathlib.Path(os.environ.get("CHINA_CHESS_DOCS_DATA_OUTPUT") or PUBLIC_DOCS_DATA)
BULK_ROOT = DOCS_DATA / "bulk"
LICHESS_ROOT = BULK_ROOT / "lichess-broadcast"
SHARD_ROOT = LICHESS_ROOT / "shards"
YOUTH_ROOT = BULK_ROOT / "youth"
LICHESS_EVENT_ROOT = BULK_ROOT / "lichess-events"
OBJECT_STORAGE_BASE = "https://data.chessdb.aigclabs.cc"
EXISTING_SHARD_ROOT = PUBLIC_DOCS_DATA / "bulk" / "lichess-broadcast" / "shards"
REGISTRY_PLAYERS_JSON = PUBLIC_DOCS_DATA / "registry" / "players.json"
MANUAL_ALIAS_CSV = REPO_ROOT / "data" / "manual" / "player-aliases.csv"
EVENT_DETAILS_ROOT = REPO_ROOT / "data" / "generated" / "chess-results-event-details"
PUBLIC_EVENTS_JSON = PUBLIC_DOCS_DATA / "index" / "public-events.json"
DATABASE_URL = "https://database.lichess.org/"
USER_AGENT = "ChinaChessPlayerPGNBulkSync/1.0"
COMPETITION_YEAR = 2026


@dataclass
class BroadcastShard:
    month: str
    url: str
    file_name: str
    size_text: str
    size_bytes: int
    games: int
    calendar_url: str
    local_path: str = ""
    sha256: str = ""
    mirrored: bool = False

    @property
    def shard_path(self) -> pathlib.Path:
        output = SHARD_ROOT / self.file_name
        existing = EXISTING_SHARD_ROOT / self.file_name
        return output if output.exists() or not existing.exists() else existing

    @property
    def output_shard_path(self) -> pathlib.Path:
        return SHARD_ROOT / self.file_name

    def payload(self) -> dict[str, Any]:
        return without_empty(
            {
                "month": self.month,
                "url": self.url,
                "path": self.local_path,
                "fileName": self.file_name,
                "size": self.size_text,
                "sizeBytes": self.size_bytes,
                "games": self.games,
                "calendarURL": self.calendar_url,
                "sha256": self.sha256,
                "mirrored": self.mirrored,
            }
        )


@dataclass
class PlayerProfile:
    fide_id: str
    birth_year: int | None = None
    federation: str = ""
    display_name: str = ""
    name: str = ""
    names: set[str] = field(default_factory=set)


class BroadcastTableParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_broadcasts = False
        self.in_row = False
        self.in_cell = False
        self.current_text: list[str] = []
        self.cells: list[str] = []
        self.links: list[str] = []
        self.rows: list[tuple[list[str], list[str]]] = []
        self.total_games = 0
        self.capture_total = False
        self.license_text = ""
        self.capture_license = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() in {"section", "div"} and values.get("id") == "broadcasts":
            self.in_broadcasts = True
        if not self.in_broadcasts:
            return
        if tag.lower() == "strong" and not self.total_games:
            self.capture_total = True
            self.current_text = []
        if tag.lower() == "p" and values.get("class") == "license":
            self.capture_license = True
            self.current_text = []
        if tag.lower() == "tr":
            self.in_row = True
            self.cells = []
            self.links = []
        if self.in_row and tag.lower() in {"td", "th"}:
            self.in_cell = True
            self.current_text = []
        if self.in_row and tag.lower() == "a" and values.get("href"):
            self.links.append(urllib.parse.urljoin(DATABASE_URL, values["href"]))

    def handle_data(self, data: str) -> None:
        if self.capture_total or self.capture_license or self.in_cell:
            self.current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self.in_broadcasts:
            return
        if tag.lower() == "strong" and self.capture_total:
            text = clean("".join(self.current_text))
            if text and text.replace(",", "").isdigit():
                self.total_games = int(text.replace(",", ""))
            self.capture_total = False
            self.current_text = []
        if tag.lower() == "p" and self.capture_license:
            self.license_text = clean(" ".join(self.current_text))
            self.capture_license = False
            self.current_text = []
        if self.in_row and tag.lower() in {"td", "th"}:
            self.cells.append(clean(" ".join(self.current_text)))
            self.in_cell = False
            self.current_text = []
        if self.in_row and tag.lower() == "tr":
            if self.cells and any(".pgn.zst" in link for link in self.links):
                self.rows.append((self.cells, self.links))
            self.in_row = False


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync Lichess broadcast bulk PGN archive.")
    parser.add_argument("--metadata-only", action="store_true", help="only refresh bulk manifests")
    parser.add_argument("--mirror", action="store_true", help="download .pgn.zst shards into docs/data/bulk")
    parser.add_argument("--index-youth", action="store_true", help="build per-age CHN PGN packs (U8-U18 + adult 19+) from mirrored shards")
    parser.add_argument("--max-shards", type=int, default=0, help="limit shards for mirror/index; 0 means all")
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    all_shards, source_meta = fetch_broadcast_metadata()
    selected_shards = all_shards[: args.max_shards] if args.max_shards else all_shards

    if args.mirror:
        mirror_shards(selected_shards, args.force, args.delay, args.dry_run)

    enrich_local_shards(all_shards)
    write_bulk_manifest(all_shards, source_meta, args.dry_run)

    youth_stats = None
    target_event_stats = None
    if args.index_youth:
        youth_stats = build_youth_index(selected_shards, args.dry_run)
        target_event_stats = build_target_event_archives(selected_shards, args.dry_run)
    elif not args.dry_run and not (YOUTH_ROOT / "manifest.json").exists():
        write_empty_youth_manifest()

    summary = {
        "broadcastShards": len(all_shards),
        "broadcastGames": sum(shard.games for shard in all_shards),
        "mirroredShards": sum(1 for shard in all_shards if shard.shard_path.exists()),
        "mirroredBytes": sum(shard.shard_path.stat().st_size for shard in all_shards if shard.shard_path.exists()),
        "youth": youth_stats,
        "targetEvents": target_event_stats,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def fetch_broadcast_metadata() -> tuple[list[BroadcastShard], dict[str, Any]]:
    request = urllib.request.Request(DATABASE_URL, headers={"User-Agent": USER_AGENT})
    body, _final_url, _headers = fetch_bytes(
        request,
        timeout=90,
        retries=2,
        expected_types=("text/html", "application/xhtml+xml"),
    )
    html = decode_response(body)
    parser = BroadcastTableParser()
    parser.feed(html)

    shards: list[BroadcastShard] = []
    for cells, links in parser.rows:
        if len(cells) < 3:
            continue
        download_url = next(
            (
                link
                for link in links
                if "/broadcast/lichess_db_broadcast_" in urllib.parse.urlparse(link).path and link.endswith(".pgn.zst")
            ),
            "",
        )
        calendar_url = next((link for link in links if "/broadcast/calendar/" in link), "")
        if not download_url:
            continue
        file_name = pathlib.PurePosixPath(urllib.parse.urlparse(download_url).path).name
        shards.append(
            BroadcastShard(
                month=normalize_month(cells[0]),
                url=download_url,
                file_name=file_name,
                size_text=cells[1],
                size_bytes=parse_size(cells[1]),
                games=parse_int(cells[2]) or 0,
                calendar_url=calendar_url,
            )
        )

    shards.sort(key=lambda shard: shard.month, reverse=True)
    validate_metadata(shards)
    return shards, {
        "source": "Lichess Broadcasts",
        "sourceURL": DATABASE_URL,
        "totalGamesText": parser.total_games,
        "license": "Creative Commons Attribution-ShareAlike 4.0",
        "licenseURL": "https://creativecommons.org/licenses/by-sa/4.0/",
        "attributionURL": DATABASE_URL,
        "licenseNotice": parser.license_text,
    }


def validate_metadata(shards: list[BroadcastShard]) -> None:
    if len(shards) < 12:
        raise RuntimeError(f"PARSER_LAYOUT_CHANGED: Lichess Broadcast 目录仅解析到 {len(shards)} 个分片")
    months = [shard.month for shard in shards]
    files = [shard.file_name for shard in shards]
    if len(set(months)) != len(months) or len(set(files)) != len(files):
        raise RuntimeError("PARSER_LAYOUT_CHANGED: Lichess Broadcast 目录出现重复月份或文件")
    if any(
        not re.fullmatch(r"\d{4}-\d{2}", shard.month)
        or urllib.parse.urlparse(shard.url).hostname != "database.lichess.org"
        or not shard.file_name.endswith(".pgn.zst")
        or shard.games <= 0
        or shard.size_bytes <= 0
        for shard in shards
    ):
        raise RuntimeError("PARSER_LAYOUT_CHANGED: Lichess Broadcast 目录字段不完整")


def validate_local_shard(path: pathlib.Path, expected_size: int) -> None:
    size = path.stat().st_size
    if size <= 0 or (expected_size and size < int(expected_size * 0.5)):
        raise RuntimeError(f"VALIDATION_REGRESSION: Lichess 分片尺寸异常：{path.name} ({size}/{expected_size})")
    with path.open("rb") as handle:
        magic = handle.read(4)
        # RFC 8878 allows one or more skippable frames (0x184D2A50–5F)
        # before the first compressed frame.  Lichess 2026-06 uses this for a
        # four-byte metadata preamble, so checking only byte offset zero
        # falsely rejected a valid archive and triggered an endless re-fetch.
        for _ in range(16):
            value = int.from_bytes(magic, "little")
            if magic == b"\x28\xb5\x2f\xfd":
                return
            if not 0x184D2A50 <= value <= 0x184D2A5F:
                break
            length_bytes = handle.read(4)
            if len(length_bytes) != 4:
                break
            handle.seek(int.from_bytes(length_bytes, "little"), os.SEEK_CUR)
            magic = handle.read(4)
    raise RuntimeError(f"VALIDATION_REGRESSION: Lichess 分片签名无效：{path.name}")


def mirror_shards(shards: list[BroadcastShard], force: bool, delay: float, dry_run: bool) -> None:
    SHARD_ROOT.mkdir(parents=True, exist_ok=True)
    for shard in shards:
        target = shard.output_shard_path
        if shard.shard_path.exists() and not force:
            try:
                validate_local_shard(shard.shard_path, shard.size_bytes)
                continue
            except RuntimeError as error:
                print(f"WARNING: {error}; 重新下载", flush=True)
        print(f"mirror {shard.file_name} {shard.size_text}", flush=True)
        if dry_run:
            continue
        request = urllib.request.Request(shard.url, headers={"User-Agent": USER_AGENT})
        download_to_path(
            request,
            target,
            timeout=180,
            retries=2,
            expected_size=shard.size_bytes,
            minimum_ratio=0.5,
            # Zstandard permits a skippable-frame preamble; validate with the
            # format-aware checker below instead of a fixed offset-zero magic.
            magic=b"",
        )
        validate_local_shard(target, shard.size_bytes)
        if delay:
            time.sleep(delay)


def enrich_local_shards(shards: list[BroadcastShard]) -> None:
    for shard in shards:
        if shard.shard_path.exists():
            validate_local_shard(shard.shard_path, shard.size_bytes)
            shard.local_path = public_data_path(shard.shard_path)
            shard.size_bytes = shard.shard_path.stat().st_size
            shard.sha256 = sha256_file(shard.shard_path)
            shard.mirrored = True


def write_bulk_manifest(shards: list[BroadcastShard], source_meta: dict[str, Any], dry_run: bool) -> None:
    generated_at = now()
    mirrored = [shard for shard in shards if shard.mirrored]
    manifest = {
        "schemaVersion": 2,
        "generatedAt": generated_at,
        "storage": {
            "root": "data/bulk",
            "lichessBroadcastRoot": "data/bulk/lichess-broadcast/shards",
            "youthRoot": "data/bulk/youth",
            # Large immutable shards are distributed from object storage
            # (R2, docs/OBJECT_STORAGE_MIGRATION.md); repository copies stay
            # gitignored on the maintainer machine for offline rebuilds.
            "objectStorageBase": OBJECT_STORAGE_BASE,
            "objectStorageShardPattern": f"{OBJECT_STORAGE_BASE}/bulk/lichess-broadcast/shards/{{fileName}}",
        },
        "sources": [
            {
                **source_meta,
                "shards": len(shards),
                "games": sum(shard.games for shard in shards),
                "compressedBytesEstimated": sum(shard.size_bytes for shard in shards),
                "mirroredShards": len(mirrored),
                "mirroredBytes": sum(shard.size_bytes for shard in mirrored),
            }
        ],
        "totals": {
            "sources": 1,
            "shards": len(shards),
            "games": sum(shard.games for shard in shards),
            "mirroredShards": len(mirrored),
            "mirroredGames": sum(shard.games for shard in mirrored),
            "mirroredBytes": sum(shard.size_bytes for shard in mirrored),
        },
        "shards": [shard.payload() for shard in shards],
    }
    if not dry_run:
        write_json(BULK_ROOT / "manifest.json", manifest)
        write_json(LICHESS_ROOT / "manifest.json", manifest)


def build_youth_index(shards: list[BroadcastShard], dry_run: bool) -> dict[str, Any]:
    profiles, names = load_profiles()
    by_stage: dict[str, list[str]] = {stage["id"]: [] for stage in indexed_stage_list()}
    index_by_stage: dict[str, list[dict[str, Any]]] = {stage["id"]: [] for stage in indexed_stage_list()}
    seen_by_stage: dict[str, set[str]] = {stage["id"]: set() for stage in indexed_stage_list()}
    seen_index_by_stage: dict[str, set[tuple[str, str, str]]] = {stage["id"]: set() for stage in indexed_stage_list()}
    scanned_games = 0

    for shard in shards:
        if not shard.shard_path.exists():
            continue
        print(f"index-chn {shard.file_name}", flush=True)
        for game_index, game in enumerate(iter_zst_pgn_games(shard.shard_path), start=1):
            scanned_games += 1
            headers = pgn_headers(game)
            date = normalize_pgn_date(headers.get("EventDate") or headers.get("Date") or "")
            year = int(date[:4]) if date[:4].isdigit() else year_from_month(shard.month)
            matches = youth_matches(headers, profiles, names, year)
            if not matches:
                continue
            game_hash = stable_game_hash(game)
            for match in matches:
                stage_id = match["stage"]
                if game_hash not in seen_by_stage[stage_id]:
                    seen_by_stage[stage_id].add(game_hash)
                    by_stage[stage_id].append(game.strip())
                index_marker = (game_hash, match["fideID"], match["role"])
                if index_marker in seen_index_by_stage[stage_id]:
                    continue
                seen_index_by_stage[stage_id].add(index_marker)
                index_by_stage[stage_id].append(
                    {
                        "fideID": match["fideID"],
                        "name": match["name"],
                        "role": match["role"],
                        "stage": stage_id,
                        "event": headers.get("Event", ""),
                        "date": date,
                        "white": headers.get("White", ""),
                        "black": headers.get("Black", ""),
                        "result": headers.get("Result", ""),
                        "source": "Lichess Broadcasts",
                        "sourceShard": shard.local_path or public_data_path(shard.shard_path),
                        "gameIndex": game_index,
                    }
                )

    generated_at = now()
    stage_payloads = []
    for stage in indexed_stage_list():
        stage_id = stage["id"]
        games = by_stage[stage_id]
        pgn_path = YOUTH_ROOT / "pgn" / stage_id / ("lichess-broadcast-adult.pgn" if stage_id == "adult" else "lichess-broadcast-youth.pgn")
        index_path = YOUTH_ROOT / "index" / stage_id / "games.json"
        if not dry_run:
            pgn_path.parent.mkdir(parents=True, exist_ok=True)
            index_path.parent.mkdir(parents=True, exist_ok=True)
            pgn_path.write_text("\n\n".join(games).strip() + ("\n" if games else ""), encoding="utf-8")
            write_json(index_path, index_by_stage[stage_id])
        stage_payloads.append(
            {
                **stage,
                "games": len(games),
                "players": len({item["fideID"] for item in index_by_stage[stage_id]}),
                "pgnPath": public_data_path(pgn_path),
                "indexPath": public_data_path(index_path),
            }
        )

    manifest = {
        "schemaVersion": 1,
        "generatedAt": generated_at,
        "ageRule": stage_rules(),
        "source": "Lichess Broadcasts",
        "sourceURL": DATABASE_URL,
        "license": "Creative Commons Attribution-ShareAlike 4.0",
        "licenseURL": "https://creativecommons.org/licenses/by-sa/4.0/",
        "attributionURL": DATABASE_URL,
        "totals": {
            "scannedGames": scanned_games,
            "games": sum(stage["games"] for stage in stage_payloads),
            "players": len({item["fideID"] for items in index_by_stage.values() for item in items}),
            "stages": len(stage_payloads),
        },
        "stages": stage_payloads,
    }
    if not dry_run:
        write_json(YOUTH_ROOT / "manifest.json", manifest)
    return manifest["totals"]


def target_series_event(value: dict[str, Any]) -> bool:
    """Return the standard-play Asian/World youth events audited by TNR.

    The public catalog can contain schools, junior, cup, rapid and blitz rows
    under the broad youth labels.  Those are deliberately excluded here so a
    broadcast cannot be attached to the wrong competition.
    """
    series = clean(value.get("series")).casefold()
    name = clean(value.get("name")).casefold()
    excluded = ("rapid", "blitz", "schools", "school", "junior", "olympiad", "cup", "training", "eastern")
    if any(token in name for token in excluded):
        return False
    if series == "asian-youth":
        return "asian youth" in name and "championship" in name
    if series == "world-youth":
        return "world" in name and ("youth" in name or "cadet" in name) and "championship" in name
    return False


def target_event_rows() -> list[dict[str, Any]]:
    if not PUBLIC_EVENTS_JSON.exists():
        return []
    payload = read_json(PUBLIC_EVENTS_JSON)
    rows = payload.get("events", []) if isinstance(payload, dict) else payload
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        tid = clean(row.get("tournamentID"))
        if not tid or tid in result or not target_series_event(row):
            continue
        detail_path = EVENT_DETAILS_ROOT / f"tnr{tid}.json"
        if not detail_path.exists():
            continue
        detail = read_json(detail_path)
        year_match = re.search(r"\b(20(?:2[2-9]|3\d))\b", clean(row.get("date")) or clean(detail.get("sourceName")))
        if not year_match:
            continue
        result[tid] = {
            "tournamentID": tid,
            "series": clean(row.get("series")),
            "name": clean(detail.get("sourceName")) or clean(row.get("name")),
            "date": clean(row.get("date")),
            "year": year_match.group(1),
            "detail": detail,
        }
    return sorted(result.values(), key=lambda item: (item["year"], item["tournamentID"]))


def target_player_key(value: Any) -> str:
    """Order-insensitive Latin token key, with domestic region suffix removed."""
    text = re.sub(r"\([^)]*\)", "", clean(value).casefold())
    tokens = re.findall(r"[0-9a-z\u4e00-\u9fff]+", text)
    return "".join(sorted(tokens))


def target_round(value: Any) -> str:
    text = clean(value)
    direct = re.match(r"(\d+)", text)
    if direct:
        return direct.group(1)
    match = re.search(r"\b(?:round|rd)\s*([0-9]+)\b", text, flags=re.IGNORECASE)
    return match.group(1) if match else ""


def game_round(headers: dict[str, str]) -> str:
    for value in (headers.get("Round"), headers.get("StudyName"), headers.get("Event")):
        result = target_round(value)
        if result:
            return result
    return ""


def event_group(value: Any) -> tuple[str, str] | None:
    text = clean(value).casefold()
    matches = list(re.finditer(
        r"\b(girls?|girl|open|boys?|boy|[gou])\s*(?:under\s*)?(?:u\s*)?-?\s*0?(8|10|12|14|16|18)\b",
        text,
    ))
    if not matches:
        return None
    # Some official titles enumerate every age in the series prefix and put
    # the actual group after the final dash.  The last recognized group is
    # therefore the specific section represented by the TNR/broadcast.
    match = matches[-1]
    label, age = match.groups()
    sex = "G" if label.startswith("g") else "O"
    return sex, age


def compatible_target_broadcast(event: dict[str, Any], headers: dict[str, str]) -> bool:
    broadcast = clean(headers.get("BroadcastName") or headers.get("Event"))
    lowered = broadcast.casefold()
    excluded = ("rapid", "blitz", "schools", "school", "junior", "olympiad", "cup", "training", "eastern", "western")
    if any(token in lowered for token in excluded):
        return False
    if event["series"] == "asian-youth":
        if "asian youth" not in lowered or "championship" not in lowered:
            return False
    elif event["series"] == "world-youth":
        if "world" not in lowered or ("youth" not in lowered and "cadet" not in lowered) or "championship" not in lowered:
            return False
    target_group = event_group(event.get("name"))
    broadcast_group = event_group(broadcast)
    if target_group and broadcast_group and target_group != broadcast_group:
        return False

    event_date = normalize_pgn_date(clean(event.get("date")))
    game_date = normalize_pgn_date(
        headers.get("EventDate") or headers.get("Date") or headers.get("UTCDate") or ""
    )
    if event_date and game_date:
        try:
            expected = dt.date.fromisoformat(event_date)
            actual = dt.date.fromisoformat(game_date)
        except ValueError:
            return False
        # Catalog dates may be either the first or final round.  Youth events
        # in scope are shorter than six weeks; series/discipline/group checks
        # above keep adjacent rapid and blitz broadcasts out.
        if abs((actual - expected).days) > 45:
            return False
    return True


def pgn_with_archive_headers(game: str, tid: str, board: str, round_no: str, headers: dict[str, str]) -> str:
    additions = {
        "Round": round_no,
        "Board": board,
        "TournamentID": tid,
        "Source": "Lichess Broadcasts",
        "SourceURL": headers.get("BroadcastURL") or DATABASE_URL,
        "License": "CC BY-SA 4.0",
        "LicenseURL": "https://creativecommons.org/licenses/by-sa/4.0/",
    }
    existing = pgn_headers(game)
    lines = []
    for key, value in additions.items():
        if value and not existing.get(key):
            escaped = clean(value).replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'[{key} "{escaped}"]')
    if not lines:
        return game.strip()
    split = game.find("\n\n")
    if split < 0:
        return game.rstrip() + "\n" + "\n".join(lines)
    return game[:split].rstrip() + "\n" + "\n".join(lines) + game[split:]


def build_target_event_archives(shards: list[BroadcastShard], dry_run: bool) -> dict[str, Any]:
    """Cross-match Lichess broadcast games to TNR pairings and archive them.

    Matching is intentionally strict: year + round + both player identities.
    FIDE IDs are preferred; normalized names are fallback evidence.  Only a
    unique TNR/board match is accepted, so this projection cannot guess across
    rapid/standard events or repeated opponents.
    """
    events = target_event_rows()
    by_id: dict[tuple[str, str, tuple[str, str]], list[tuple[str, str]]] = {}
    by_name: dict[tuple[str, str, tuple[str, str]], list[tuple[str, str]]] = {}
    event_meta: dict[str, dict[str, Any]] = {}

    for event in events:
        detail = event.pop("detail")
        roster = {
            clean(player.get("playerNo")): player
            for player in detail.get("players", [])
            if clean(player.get("playerNo"))
        }
        played = 0
        for round_row in detail.get("rounds", []):
            round_no = target_round(round_row.get("round"))
            for pairing in round_row.get("pairings", []):
                white = pairing.get("white") or {}
                black = pairing.get("black") or {}
                board = clean(pairing.get("board"))
                if not round_no or not board or not white.get("playerNo") or not black.get("playerNo"):
                    continue
                result = clean(pairing.get("result"))
                if not result or re.fullmatch(r"[+\-]\s*-\s*[+\-]", result):
                    continue
                white_player = roster.get(clean(white.get("playerNo")), {})
                black_player = roster.get(clean(black.get("playerNo")), {})
                id_pair = tuple(sorted((clean(white_player.get("fideID")), clean(black_player.get("fideID")))))
                name_pair = tuple(sorted((target_player_key(white.get("name")), target_player_key(black.get("name")))))
                ref = (event["tournamentID"], board)
                if all(id_pair):
                    by_id.setdefault((event["year"], round_no, id_pair), []).append(ref)
                if all(name_pair):
                    by_name.setdefault((event["year"], round_no, name_pair), []).append(ref)
                played += 1
        event_meta[event["tournamentID"]] = {
            **event,
            "playedGames": played,
            "games": [],
            "gameHashes": set(),
            "matchedBoards": set(),
            "broadcastNames": set(),
            "sourceShards": set(),
            "idMatches": 0,
            "nameMatches": 0,
        }

    ambiguous = 0
    scanned = 0
    container_games: dict[str, int] = {}
    container_matches: dict[str, int] = {}
    for shard in shards:
        if not shard.shard_path.exists() or shard.month[:4] not in {event["year"] for event in events}:
            continue
        print(f"index-target-events {shard.file_name}", flush=True)
        for game in iter_zst_pgn_games(shard.shard_path):
            scanned += 1
            headers = pgn_headers(game)
            container = clean(headers.get("BroadcastName") or headers.get("Event"))
            if container:
                container_games[container] = container_games.get(container, 0) + 1
            year = normalize_pgn_date(
                headers.get("EventDate") or headers.get("Date") or headers.get("UTCDate") or ""
            )[:4] or shard.month[:4]
            round_no = game_round(headers)
            if not round_no:
                continue
            id_pair = tuple(sorted((clean(headers.get("WhiteFideId") or headers.get("WhiteFideID")),
                                    clean(headers.get("BlackFideId") or headers.get("BlackFideID")))))
            name_pair = tuple(sorted((target_player_key(headers.get("White")), target_player_key(headers.get("Black")))))
            refs: list[tuple[str, str]] = []
            match_kind = ""
            if all(id_pair):
                refs = by_id.get((year, round_no, id_pair), [])
                match_kind = "id"
            if not refs and all(name_pair):
                refs = by_name.get((year, round_no, name_pair), [])
                match_kind = "name"
            refs = [
                ref
                for ref in dict.fromkeys(refs)
                if compatible_target_broadcast(event_meta[ref[0]], headers)
            ]
            if len(refs) != 1:
                ambiguous += int(len(refs) > 1)
                continue
            tid, board = refs[0]
            meta = event_meta[tid]
            marker = stable_game_hash(game)
            if marker in meta["gameHashes"]:
                continue
            meta["gameHashes"].add(marker)
            meta["matchedBoards"].add((round_no, board))
            meta["broadcastNames"].add(clean(headers.get("BroadcastName") or headers.get("Event")))
            meta["sourceShards"].add(shard.local_path or public_data_path(shard.shard_path))
            meta[f"{match_kind}Matches"] += 1
            meta["games"].append(pgn_with_archive_headers(game, tid, board, round_no, headers))
            if container:
                container_matches[container] = container_matches.get(container, 0) + 1

    event_rows = []
    total_games = 0
    for tid, meta in sorted(event_meta.items()):
        games = meta.pop("games")
        hashes = meta.pop("gameHashes")
        matched_boards = meta.pop("matchedBoards")
        broadcast_names = sorted(meta.pop("broadcastNames"))
        source_shards = sorted(meta.pop("sourceShards"))
        linked_container_games = sum(container_games.get(name, 0) for name in broadcast_names)
        linked_container_matches = sum(container_matches.get(name, 0) for name in broadcast_names)
        linked_container_unmatched = max(0, linked_container_games - linked_container_matches)
        path = LICHESS_EVENT_ROOT / "pgn" / f"tnr{tid}.pgn"
        if games and not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n\n".join(games).rstrip() + "\n", encoding="utf-8")
        elif not games and path.exists() and not dry_run:
            path.unlink()
        total_games += len(games)
        event_rows.append({
            **meta,
            "broadcastGames": len(games),
            "matchedPairings": len(matched_boards),
            "broadcastNames": broadcast_names,
            "sourceShards": source_shards,
            "linkedContainerGames": linked_container_games,
            "linkedContainerMatchedToTargetEvents": linked_container_matches,
            "linkedContainerUnmatchedGames": linked_container_unmatched,
            "broadcastComplete": bool(games) and linked_container_unmatched == 0,
            **({
                "pgnPath": public_data_path(path),
                "sha256": sha256_file(path) if path.exists() and not dry_run else "",
            } if games else {}),
        })

    manifest = {
        "schemaVersion": 1,
        "generatedAt": now(),
        "source": "Lichess Broadcasts",
        "sourceURL": DATABASE_URL,
        "license": "Creative Commons Attribution-ShareAlike 4.0",
        "licenseURL": "https://creativecommons.org/licenses/by-sa/4.0/",
        "matchingRule": "unique year + round + player pair; FIDE IDs preferred, normalized names fallback",
        "totals": {
            "targetEvents": len(events),
            "eventsWithBroadcasts": sum(bool(row["broadcastGames"]) for row in event_rows),
            "broadcastGames": total_games,
            "scannedGames": scanned,
            "ambiguousGamesRejected": ambiguous,
        },
        "events": event_rows,
        "containers": [
            {
                "broadcastName": name,
                "games": container_games[name],
                "matchedToTargetEvents": container_matches.get(name, 0),
                "unmatchedGames": container_games[name] - container_matches.get(name, 0),
            }
            for name in sorted({name for row in event_rows for name in row["broadcastNames"]})
        ],
    }
    if not dry_run:
        write_json(LICHESS_EVENT_ROOT / "manifest.json", manifest)
    return manifest["totals"]


def write_empty_youth_manifest() -> None:
    stages = [{**stage, "games": 0, "players": 0, "pgnPath": "", "indexPath": ""} for stage in indexed_stage_list()]
    write_json(
        YOUTH_ROOT / "manifest.json",
        {
            "schemaVersion": 1,
            "generatedAt": now(),
            "ageRule": stage_rules(),
            "source": "Lichess Broadcasts",
            "sourceURL": DATABASE_URL,
            "license": "Creative Commons Attribution-ShareAlike 4.0",
            "licenseURL": "https://creativecommons.org/licenses/by-sa/4.0/",
            "attributionURL": DATABASE_URL,
            "totals": {"scannedGames": 0, "games": 0, "players": 0, "stages": len(stages)},
            "stages": stages,
        },
    )


def youth_matches(
    headers: dict[str, str],
    profiles: dict[str, PlayerProfile],
    names: dict[str, str],
    year: int | None,
) -> list[dict[str, str]]:
    if not year:
        return []
    result = []
    for role, fide_key, name_key in [
        ("white", "WhiteFideId", "White"),
        ("white", "WhiteFideID", "White"),
        ("black", "BlackFideId", "Black"),
        ("black", "BlackFideID", "Black"),
    ]:
        fide_id = clean(headers.get(fide_key))
        if not fide_id:
            fide_id = names.get(normalize_name(headers.get(name_key, "")), "")
        profile = profiles.get(fide_id)
        if not profile or profile.federation != "CHN" or not profile.birth_year:
            continue
        stage = stage_for_age(year - profile.birth_year)
        if stage:
            result.append(
                {
                    "fideID": fide_id,
                    "name": profile.name,
                    "displayName": profile.display_name,
                    "sourcePlayerName": headers.get(name_key, ""),
                    "role": role,
                    "stage": stage,
                }
            )
    return ordered_unique_dicts(result, ["fideID", "stage", "role"])


def load_profiles() -> tuple[dict[str, PlayerProfile], dict[str, str]]:
    profiles: dict[str, PlayerProfile] = {}
    duplicate_names: set[str] = set()
    name_owner: dict[str, str] = {}

    def add_name(profile: PlayerProfile, value: Any) -> None:
        text = clean(value)
        if not text:
            return
        profile.names.add(text)
        key = normalize_name(text)
        if not key or key.isdigit():
            return
        existing = name_owner.get(key)
        if existing and existing != profile.fide_id:
            duplicate_names.add(key)
        else:
            name_owner[key] = profile.fide_id

    if REGISTRY_PLAYERS_JSON.exists():
        data = read_json(REGISTRY_PLAYERS_JSON)
        for player in data:
            fide_id = clean(player.get("fideID"))
            if not fide_id:
                continue
            profile = profiles.setdefault(fide_id, PlayerProfile(fide_id=fide_id))
            profile.birth_year = parse_int(player.get("birthYear")) or profile.birth_year
            profile.federation = clean(player.get("federation")) or profile.federation
            profile.display_name = clean(player.get("displayName") or player.get("name") or fide_id)
            profile.name = clean(player.get("name"))
            add_name(profile, fide_id)
            for key in ["displayName", "name", "chineseName", "pinyin"]:
                add_name(profile, player.get(key))
            for alias in player.get("aliases", []) or []:
                add_name(profile, alias)

    if MANUAL_ALIAS_CSV.exists():
        import csv

        with MANUAL_ALIAS_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                fide_id = clean(row.get("fide_id"))
                profile = profiles.get(fide_id)
                if not profile:
                    continue
                for key in ["chinese_name", "pinyin_name"]:
                    add_name(profile, row.get(key))
                for alias in re.split(r"[|;]", row.get("aliases") or ""):
                    add_name(profile, alias)

    unique_names = {key: fide_id for key, fide_id in name_owner.items() if key not in duplicate_names}
    return profiles, unique_names


def _open_zst_stream(path: pathlib.Path):
    """Streaming .zst reader: zstandard module preferred, zstd CLI fallback."""
    try:
        import zstandard as zstd  # type: ignore[import-not-found]

        raw = path.open("rb")
        return zstd.ZstdDecompressor().stream_reader(raw), raw, None
    except ImportError:
        import shutil
        import subprocess

        zstd_bin = shutil.which("zstd")
        if not zstd_bin:
            raise SystemExit(
                "缺少 zst 解压能力:请安装 python 模块(python3 -m pip install --user zstandard)"
                "或命令行工具(brew install zstd)后重试。"
            )
        proc = subprocess.Popen([zstd_bin, "-dc", str(path)], stdout=subprocess.PIPE)
        assert proc.stdout is not None
        return proc.stdout, proc.stdout, proc


def iter_zst_pgn_games(path: pathlib.Path) -> Iterator[str]:
    reader, closer, proc = _open_zst_stream(path)
    try:
        text_reader = TextChunkReader(reader)
        buffer = ""
        while True:
            chunk = text_reader.read(1024 * 1024)
            if not chunk:
                break
            buffer += chunk
            parts = re.split(r"(?=^\[Event\s+\")", buffer, flags=re.MULTILINE)
            if len(parts) <= 1:
                continue
            buffer = parts.pop()
            for part in parts:
                game = part.strip()
                if game:
                    yield game
        if buffer.strip():
            yield buffer.strip()
    finally:
        try:
            closer.close()
        except Exception:
            pass
        if proc is not None:
            return_code = proc.wait()
            if return_code != 0:
                raise RuntimeError(f"VALIDATION_REGRESSION: zstd 解压失败（exit {return_code}）: {path.name}")


class TextChunkReader:
    def __init__(self, reader: Any) -> None:
        self.reader = reader
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def read(self, size: int) -> str:
        data = self.reader.read(size)
        if not data:
            return self.decoder.decode(b"", final=True)
        return self.decoder.decode(data, final=False)


def pgn_headers(game: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for match in re.finditer(r'^\[([A-Za-z0-9_]+)\s+"(.*)"\]', game, flags=re.MULTILINE):
        headers[match.group(1)] = match.group(2)
    return headers


def stage_rules() -> dict[str, Any]:
    return {
        "title": "李成智杯自然年龄组",
        "competitionYear": COMPETITION_YEAR,
        "description": "以赛事年度减出生年份，两年一组。",
        "stages": [
            {"id": "U8", "lowerAge": 7, "upperAge": 8, "birthYears": "2018-2019"},
            {"id": "U10", "lowerAge": 9, "upperAge": 10, "birthYears": "2016-2017"},
            {"id": "U12", "lowerAge": 11, "upperAge": 12, "birthYears": "2014-2015"},
            {"id": "U14", "lowerAge": 13, "upperAge": 14, "birthYears": "2012-2013"},
            {"id": "U16", "lowerAge": 15, "upperAge": 16, "birthYears": "2010-2011"},
            {"id": "U18", "lowerAge": 17, "upperAge": 18, "birthYears": "2008-2009"},
        ],
    }


def indexed_stage_list() -> list[dict[str, Any]]:
    """Stages actually indexed from broadcasts: youth U8-U18 plus adult (19+),
    so EVERY CHN player with a known birth year gets their broadcast games."""
    return stage_rules()["stages"] + [
        {"id": "adult", "lowerAge": 19, "upperAge": 199, "birthYears": "2007 及更早"},
    ]


def stage_for_age(age: int) -> str:
    for stage in indexed_stage_list():
        if stage["lowerAge"] <= age <= stage["upperAge"]:
            return stage["id"]
    return ""


def decode_response(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def parse_size(value: str) -> int:
    match = re.search(r"([0-9.]+)\s*([KMGT]?i?B)", value, flags=re.IGNORECASE)
    if not match:
        return 0
    amount = float(match.group(1))
    unit = match.group(2).upper()
    factor = {
        "B": 1,
        "KB": 1024, "KIB": 1024,
        "MB": 1024**2, "MIB": 1024**2,
        "GB": 1024**3, "GIB": 1024**3,
        "TB": 1024**4, "TIB": 1024**4,
    }[unit]
    return int(amount * factor)


def parse_int(value: Any) -> int | None:
    text = re.sub(r"[,\s]", "", str(value or ""))
    return int(text) if text.isdigit() else None


def normalize_month(value: str) -> str:
    match = re.search(r"(\d{4})\s*-\s*([A-Za-z]+|\d{1,2})", value)
    if not match:
        return clean(value)
    year, month = match.groups()
    month_number = {
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
    }.get(month.lower(), int(month) if month.isdigit() else 0)
    return f"{year}-{month_number:02d}" if month_number else clean(value)


def year_from_month(value: str) -> int | None:
    return int(value[:4]) if re.match(r"^\d{4}-\d{2}$", value) else None


def normalize_pgn_date(text: str) -> str:
    if re.match(r"^\d{4}\.\d{2}\.\d{2}$", text):
        return text.replace(".", "-")
    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        return text
    return ""


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_game_hash(game: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", " ", game).strip().encode("utf-8")).hexdigest()


def normalize_name(value: str) -> str:
    return re.sub(r"[\s,.'`’\"()，。·_\-]+", "", str(value or "").casefold())


def ordered_unique_dicts(values: list[dict[str, str]], keys: list[str]) -> list[dict[str, str]]:
    seen = set()
    result = []
    for value in values:
        marker = tuple(value.get(key, "") for key in keys)
        if marker not in seen:
            seen.add(marker)
            result.append(value)
    return result


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def without_empty(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value not in (None, "", [], {})}


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: pathlib.Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: pathlib.Path, data: Any) -> None:
    write_stable_json(path, data, ensure_ascii=False, indent=2)


def public_data_path(path: pathlib.Path) -> str:
    for root in (DOCS_DATA, PUBLIC_DOCS_DATA):
        try:
            return "data/" + str(path.relative_to(root))
        except ValueError:
            continue
    raise ValueError(f"path is outside public/staged docs data: {path}")


if __name__ == "__main__":
    raise SystemExit(main())
