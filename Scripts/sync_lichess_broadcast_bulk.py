#!/usr/bin/env python3
"""Mirror and index the Lichess broadcast PGN bulk dataset.

The Lichess broadcast archive is the fastest legal path to a million-game
static tournament PGN backbone. The raw archive stays compressed as monthly
.pgn.zst shards, while youth filters are generated as much smaller stage PGN
packs and JSON indexes.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html.parser
import json
import pathlib
import re
import shutil
import ssl
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Iterator


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS_DATA = REPO_ROOT / "docs" / "data"
BULK_ROOT = DOCS_DATA / "bulk"
LICHESS_ROOT = BULK_ROOT / "lichess-broadcast"
SHARD_ROOT = LICHESS_ROOT / "shards"
YOUTH_ROOT = BULK_ROOT / "youth"
REGISTRY_PLAYERS_JSON = DOCS_DATA / "registry" / "players.json"
MANUAL_ALIAS_CSV = REPO_ROOT / "data" / "manual" / "player-aliases.csv"
DATABASE_URL = "https://database.lichess.org/"
USER_AGENT = "ChinaChessPlayerPGNBulkSync/1.0"
COMPETITION_YEAR = 2026
_TLS_CONTEXT: ssl.SSLContext | None = None


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

    shards, source_meta = fetch_broadcast_metadata()
    if args.max_shards:
        shards = shards[: args.max_shards]

    if args.mirror:
        mirror_shards(shards, args.force, args.delay, args.dry_run)

    all_shards, _ = fetch_broadcast_metadata()
    enrich_local_shards(all_shards)
    write_bulk_manifest(all_shards, source_meta, args.dry_run)

    youth_stats = None
    if args.index_youth:
        youth_stats = build_youth_index(all_shards[: args.max_shards or None], args.dry_run)
    elif not args.dry_run and not (YOUTH_ROOT / "manifest.json").exists():
        write_empty_youth_manifest()

    summary = {
        "broadcastShards": len(all_shards),
        "broadcastGames": sum(shard.games for shard in all_shards),
        "mirroredShards": sum(1 for shard in all_shards if shard.shard_path.exists()),
        "mirroredBytes": sum(shard.shard_path.stat().st_size for shard in all_shards if shard.shard_path.exists()),
        "youth": youth_stats,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def fetch_broadcast_metadata() -> tuple[list[BroadcastShard], dict[str, Any]]:
    request = urllib.request.Request(DATABASE_URL, headers={"User-Agent": USER_AGENT})
    with open_url(request) as response:
        html = decode_response(response.read())
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
    return shards, {
        "source": "Lichess Broadcasts",
        "sourceURL": DATABASE_URL,
        "totalGamesText": parser.total_games,
        "license": "Creative Commons Attribution-ShareAlike 4.0",
        "licenseURL": "https://creativecommons.org/licenses/by-sa/4.0/",
    }


def mirror_shards(shards: list[BroadcastShard], force: bool, delay: float, dry_run: bool) -> None:
    SHARD_ROOT.mkdir(parents=True, exist_ok=True)
    for shard in shards:
        target = shard.shard_path
        if target.exists() and not force and target.stat().st_size > 0:
            continue
        print(f"mirror {shard.file_name} {shard.size_text}", flush=True)
        if dry_run:
            continue
        tmp = target.with_suffix(target.suffix + ".tmp")
        request = urllib.request.Request(shard.url, headers={"User-Agent": USER_AGENT})
        with open_url(request, timeout=180) as response, tmp.open("wb") as handle:
            shutil.copyfileobj(response, handle)
        tmp.replace(target)
        if delay:
            time.sleep(delay)


def enrich_local_shards(shards: list[BroadcastShard]) -> None:
    for shard in shards:
        if shard.shard_path.exists():
            shard.local_path = public_data_path(shard.shard_path)
            shard.size_bytes = shard.shard_path.stat().st_size
            shard.sha256 = sha256_file(shard.shard_path)
            shard.mirrored = True


def write_bulk_manifest(shards: list[BroadcastShard], source_meta: dict[str, Any], dry_run: bool) -> None:
    generated_at = now()
    mirrored = [shard for shard in shards if shard.mirrored]
    manifest = {
        "schemaVersion": 1,
        "generatedAt": generated_at,
        "storage": {
            "root": "data/bulk",
            "lichessBroadcastRoot": "data/bulk/lichess-broadcast/shards",
            "youthRoot": "data/bulk/youth",
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
                    "name": headers.get(name_key, profile.display_name),
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


def iter_zst_pgn_games(path: pathlib.Path) -> Iterator[str]:
    import zstandard as zstd  # type: ignore[import-not-found]

    decoder = zstd.ZstdDecompressor()
    with path.open("rb") as raw, decoder.stream_reader(raw) as reader:
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


class TextChunkReader:
    def __init__(self, reader: Any) -> None:
        self.reader = reader
        self.pending = b""

    def read(self, size: int) -> str:
        data = self.reader.read(size)
        if not data:
            pending = self.pending
            self.pending = b""
            return pending.decode("utf-8", errors="replace")
        data = self.pending + data
        try:
            text = data.decode("utf-8")
            self.pending = b""
            return text
        except UnicodeDecodeError as error:
            valid = data[: error.start]
            self.pending = data[error.start :]
            return valid.decode("utf-8", errors="replace")


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


def open_url(request: urllib.request.Request, timeout: int = 90):
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            return urllib.request.urlopen(request, timeout=timeout, context=tls_context())
        except Exception as error:
            last_error = error
            if attempt < 2:
                time.sleep(1.2 * (attempt + 1))
    assert last_error is not None
    raise last_error


def tls_context() -> ssl.SSLContext:
    global _TLS_CONTEXT
    if _TLS_CONTEXT is not None:
        return _TLS_CONTEXT
    try:
        import certifi  # type: ignore[import-not-found]

        _TLS_CONTEXT = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        _TLS_CONTEXT = ssl.create_default_context()
    if hasattr(ssl, "OP_IGNORE_UNEXPECTED_EOF"):
        _TLS_CONTEXT.options |= ssl.OP_IGNORE_UNEXPECTED_EOF
    return _TLS_CONTEXT


def decode_response(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def parse_size(value: str) -> int:
    match = re.search(r"([0-9.]+)\s*([KMG]B)", value, flags=re.IGNORECASE)
    if not match:
        return 0
    amount = float(match.group(1))
    unit = match.group(2).upper()
    factor = {"KB": 1024, "MB": 1024 * 1024, "GB": 1024 * 1024 * 1024}[unit]
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
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def public_data_path(path: pathlib.Path) -> str:
    return "data/" + str(path.relative_to(DOCS_DATA))


if __name__ == "__main__":
    raise SystemExit(main())
