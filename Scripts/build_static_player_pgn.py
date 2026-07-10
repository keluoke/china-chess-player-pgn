#!/usr/bin/env python3
"""Build player-centric static PGN packs from committed PGN assets.

This script is intentionally a derived-data builder. It does not fetch from the
network. It reads the existing static event PGNs and bulk youth PGN packs, then
writes a single lookup surface for all frontends:

docs/data/index/by-player/
docs/data/pgn/by-player/
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import re
import shutil
from dataclasses import dataclass, field
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS_DATA = REPO_ROOT / "docs" / "data"
STATIC_INDEX_ROOT = DOCS_DATA / "index"
STATIC_PLAYER_ROOT = STATIC_INDEX_ROOT / "players"
REGISTRY_PLAYERS_JSON = DOCS_DATA / "registry" / "players.json"
LEADERBOARD_JSON = DOCS_DATA / "youth-leaderboards.json"
BULK_YOUTH_MANIFEST = DOCS_DATA / "bulk" / "youth" / "manifest.json"
OUTPUT_INDEX_ROOT = STATIC_INDEX_ROOT / "by-player"
OUTPUT_PGN_ROOT = DOCS_DATA / "pgn" / "by-player"
SCHEMA_VERSION = 1


@dataclass
class PlayerProfile:
    fide_id: str
    display_name: str = ""
    chinese_name: str = ""
    pinyin: str = ""
    name: str = ""
    federation: str = "CHN"
    birth_year: int | None = None
    standard: int | None = None
    rapid: int | None = None
    blitz: int | None = None
    aliases: list[str] = field(default_factory=list)

    def payload(self) -> dict[str, Any]:
        return without_empty(
            {
                "fideID": self.fide_id,
                "id": f"fide-{self.fide_id}",
                "displayName": self.display_name or self.name or f"FIDE {self.fide_id}",
                "chineseName": self.chinese_name,
                "pinyin": self.pinyin,
                "name": self.name,
                "federation": self.federation or "CHN",
                "birthYear": self.birth_year,
                "standard": self.standard,
                "rapid": self.rapid,
                "blitz": self.blitz,
                "aliases": self.aliases,
            }
        )


@dataclass
class PlayerGame:
    pgn: str
    event: str
    date: str
    white: str
    black: str
    result: str
    source: str
    stage: str = ""
    natural_stage: str = ""
    event_stage: str = ""
    source_pgn_path: str = ""
    source_index_path: str = ""
    source_shard: str = ""
    role: str = ""
    rank: Any = ""
    tournament_id: str = ""
    sha256: str = ""

    def payload(self) -> dict[str, Any]:
        return without_empty(
            {
                "id": self.sha256,
                "event": self.event,
                "date": self.date,
                "white": self.white,
                "black": self.black,
                "result": self.result,
                "source": self.source,
                "stage": self.stage,
                "naturalStage": self.natural_stage,
                "eventStage": self.event_stage,
                "role": self.role,
                "rank": self.rank,
                "tournamentID": self.tournament_id,
                "sourcePgnPath": self.source_pgn_path,
                "sourceIndexPath": self.source_index_path,
                "sourceShard": self.source_shard,
                "sha256": self.sha256,
            }
        )


@dataclass
class PlayerBucket:
    profile: PlayerProfile
    games: list[PlayerGame] = field(default_factory=list)
    seen_hashes: set[str] = field(default_factory=set)

    def add(self, game: PlayerGame) -> bool:
        if game.sha256 in self.seen_hashes:
            return False
        self.seen_hashes.add(game.sha256)
        self.games.append(game)
        return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Build static by-player PGN packs.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    profiles = load_profiles()
    buckets: dict[str, PlayerBucket] = {}
    stats = {
        "eventGames": 0,
        "bulkYouthGames": 0,
        "dedupedGames": 0,
    }

    stats["eventGames"] = ingest_static_event_pgns(buckets, profiles)
    stats["bulkYouthGames"] = ingest_bulk_youth_pgns(buckets, profiles)
    stats["dedupedGames"] = sum(len(bucket.games) for bucket in buckets.values())

    if not args.dry_run:
        reset_output_roots()
    manifest = write_outputs(buckets, dry_run=args.dry_run)
    summary = {**stats, **manifest["totals"]}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def ingest_static_event_pgns(
    buckets: dict[str, PlayerBucket],
    profiles: dict[str, PlayerProfile],
) -> int:
    total = 0
    for player_file in sorted(STATIC_PLAYER_ROOT.glob("fide-*.json")):
        detail = read_json(player_file)
        fide_id = clean(detail.get("fideID"))
        if not fide_id:
            continue
        # The static detail file is last build's OUTPUT; the registry-backed
        # profile (if present) must win over it, or a rating freezes at
        # whatever value first entered the index.
        existing = profiles.get(fide_id)
        detail_profile = profile_from_static_detail(detail)
        profile = merge_profile(detail_profile, existing) if existing else detail_profile
        profiles[fide_id] = profile
        for event in detail.get("events", []):
            pgn_path = clean(event.get("pgnPath"))
            if not pgn_path:
                continue
            path = docs_path(pgn_path)
            if not path.exists():
                continue
            pgn_text = path.read_text(encoding="utf-8", errors="replace")
            for game in split_pgn_games(pgn_text):
                game = repair_pgn_text(game)
                headers = pgn_headers(game)
                date = normalize_pgn_date(headers.get("EventDate") or headers.get("Date") or clean(event.get("date")))
                event_name = headers.get("Event") or clean(event.get("name"))
                natural_stage = natural_stage_for_player(profile, date)
                entered_stage = event_stage_from_name(event_name)
                game_record = PlayerGame(
                    pgn=game,
                    event=event_name,
                    date=date,
                    white=headers.get("White", ""),
                    black=headers.get("Black", ""),
                    result=headers.get("Result", ""),
                    source=clean(event.get("source")) or "Static PGN",
                    stage=natural_stage or entered_stage,
                    natural_stage=natural_stage,
                    event_stage=entered_stage,
                    source_pgn_path=pgn_path,
                    role=role_for_profile(profile, headers),
                    rank=event.get("rank", ""),
                    tournament_id=clean(event.get("tournamentID")),
                    sha256=stable_game_hash(game),
                )
                if bucket_for(buckets, profile).add(game_record):
                    total += 1
    return total


def ingest_bulk_youth_pgns(
    buckets: dict[str, PlayerBucket],
    profiles: dict[str, PlayerProfile],
) -> int:
    if not BULK_YOUTH_MANIFEST.exists():
        return 0

    manifest = read_json(BULK_YOUTH_MANIFEST)
    total = 0
    for stage in manifest.get("stages", []):
        stage_id = clean(stage.get("id"))
        index_path = clean(stage.get("indexPath"))
        pgn_path = clean(stage.get("pgnPath"))
        if not stage_id or not index_path or not pgn_path:
            continue
        index_file = docs_path(index_path)
        pgn_file = docs_path(pgn_path)
        if not index_file.exists() or not pgn_file.exists():
            continue

        entries = read_json(index_file)
        games = [repair_pgn_text(game) for game in split_pgn_games(pgn_file.read_text(encoding="utf-8", errors="replace"))]
        games_by_key: dict[str, list[str]] = {}
        for game in games:
            key = game_key_from_headers(pgn_headers(game))
            games_by_key.setdefault(key, []).append(game)

        for entry in entries:
            fide_id = clean(entry.get("fideID"))
            if not fide_id:
                continue
            profile = profiles.get(fide_id) or PlayerProfile(
                fide_id=fide_id,
                display_name=clean(entry.get("name")) or f"FIDE {fide_id}",
                name=clean(entry.get("name")),
            )
            profiles[fide_id] = profile
            game = first_matching_game(games_by_key, games, entry)
            if not game:
                continue
            headers = pgn_headers(game)
            event_name = headers.get("Event") or clean(entry.get("event"))
            game_record = PlayerGame(
                pgn=game,
                event=event_name,
                date=normalize_pgn_date(headers.get("EventDate") or headers.get("Date") or clean(entry.get("date"))),
                white=headers.get("White") or clean(entry.get("white")),
                black=headers.get("Black") or clean(entry.get("black")),
                result=headers.get("Result") or clean(entry.get("result")),
                source=clean(entry.get("source")) or "Lichess Broadcasts",
                stage=stage_id,
                natural_stage=stage_id,
                event_stage=event_stage_from_name(event_name),
                source_pgn_path=pgn_path,
                source_index_path=index_path,
                source_shard=clean(entry.get("sourceShard")),
                role=clean(entry.get("role")),
                sha256=stable_game_hash(game),
            )
            if bucket_for(buckets, profile).add(game_record):
                total += 1
    return total


def write_outputs(buckets: dict[str, PlayerBucket], dry_run: bool) -> dict[str, Any]:
    generated_at = now()
    player_summaries: list[dict[str, Any]] = []
    all_packages = 0
    all_bytes = 0
    all_games = 0
    sources: set[str] = set()

    for fide_id, bucket in sorted(buckets.items(), key=lambda item: item[0]):
        if not bucket.games:
            continue
        bucket.games.sort(key=lambda game: (game.date, game.event, game.white, game.black), reverse=True)
        player_dir = OUTPUT_PGN_ROOT / f"fide-{fide_id}"
        packages = []

        all_package = build_package(
            fide_id=fide_id,
            package_id="all",
            label="全部 PGN",
            games=bucket.games,
            target=player_dir / "all.pgn",
            dry_run=dry_run,
        )
        packages.append(all_package)

        for stage_id in ["U8", "U10", "U12", "U14", "U16", "U18", "adult"]:
            stage_games = [game for game in bucket.games if game.stage == stage_id]
            if not stage_games:
                continue
            packages.append(
                build_package(
                    fide_id=fide_id,
                    package_id=stage_id,
                    label=f"{stage_id} PGN",
                    games=stage_games,
                    target=player_dir / f"{stage_id}.pgn",
                    dry_run=dry_run,
                )
            )

        detail_path = OUTPUT_INDEX_ROOT / f"fide-{fide_id}.json"
        game_payloads = [game.payload() for game in bucket.games]
        event_payloads = event_summaries(bucket.games)
        stage_counts = stage_game_counts(bucket.games)
        detail = {
            "schemaVersion": SCHEMA_VERSION,
            "generatedAt": generated_at,
            "player": bucket.profile.payload(),
            "totals": {
                "games": len(bucket.games),
                "events": len(event_payloads),
                "packages": len(packages),
                "bytes": sum(package["pgnBytes"] for package in packages),
                "stages": stage_counts,
            },
            "packages": packages,
            "events": event_payloads,
            "games": game_payloads,
        }
        if not dry_run:
            write_json(detail_path, detail)

        all_packages += len(packages)
        all_bytes += sum(package["pgnBytes"] for package in packages)
        all_games += len(bucket.games)
        sources.update(game.source for game in bucket.games if game.source)

        summary = {
            **bucket.profile.payload(),
            "gameCount": len(bucket.games),
            "eventCount": len(event_payloads),
            "packageCount": len(packages),
            "playerPgnPath": all_package["pgnPath"],
            "playerPgnGameCount": all_package["gameCount"],
            "playerIndexPath": public_data_path(detail_path),
            "stages": stage_counts,
            "sources": sorted({game.source for game in bucket.games if game.source}),
        }
        player_summaries.append(summary)

    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": generated_at,
        "storage": {
            "playerPgnRoot": "data/pgn/by-player",
            "playerIndexRoot": "data/index/by-player",
            "playerPgnPattern": "data/pgn/by-player/fide-<fideID>/<package>.pgn",
            "playerIndexPattern": "data/index/by-player/fide-<fideID>.json",
        },
        "totals": {
            "players": len(player_summaries),
            "games": all_games,
            "packages": all_packages,
            "bytes": all_bytes,
        },
        "sources": sorted(sources),
    }

    if not dry_run:
        write_json(OUTPUT_INDEX_ROOT / "manifest.json", manifest)
        write_json(OUTPUT_INDEX_ROOT / "players.json", player_summaries)
    return manifest


def build_package(
    fide_id: str,
    package_id: str,
    label: str,
    games: list[PlayerGame],
    target: pathlib.Path,
    dry_run: bool,
) -> dict[str, Any]:
    body = "\n\n".join(game.pgn.strip() for game in games if game.pgn.strip()).strip()
    text = "\n".join(
        [
            "% Built by 中国棋手 PGN static by-player index",
            f"% FIDE: {fide_id}",
            f"% Package: {package_id}",
            f"% Games: {len(games)}",
            f"% Created: {now()}",
            "",
            body,
            "",
        ]
    )
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    byte_count = len(text.encode("utf-8"))
    return {
        "id": package_id,
        "label": label,
        "pgnPath": public_data_path(target),
        "gameCount": len(games),
        "pgnBytes": byte_count,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "stages": stage_game_counts(games),
        "sources": sorted({game.source for game in games if game.source}),
    }


def event_summaries(games: list[PlayerGame]) -> list[dict[str, Any]]:
    events: dict[tuple[str, str, str], dict[str, Any]] = {}
    for game in games:
        key = (game.source, game.event, game.date)
        event = events.setdefault(
            key,
            {
                "source": game.source,
                "name": game.event,
                "date": game.date,
                "tournamentID": game.tournament_id,
                "stage": game.stage,
                "naturalStage": game.natural_stage,
                "eventStage": game.event_stage,
                "gameCount": 0,
                "results": {},
            },
        )
        event["gameCount"] += 1
        event["results"][game.result] = event["results"].get(game.result, 0) + 1
    return sorted(events.values(), key=lambda item: (item.get("date") or "", item.get("name") or ""), reverse=True)


def stage_game_counts(games: list[PlayerGame]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for game in games:
        if game.stage:
            counts[game.stage] = counts.get(game.stage, 0) + 1
    return {key: counts[key] for key in ["U8", "U10", "U12", "U14", "U16", "U18"] if counts.get(key)}


def load_profiles() -> dict[str, PlayerProfile]:
    profiles: dict[str, PlayerProfile] = {}
    # Ascending authority: merge_profile prefers the later (incoming) value,
    # so the live FIDE registry must be loaded LAST — otherwise stale
    # leaderboard ratings permanently mask fresh FIDE downloads.
    for path in [LEADERBOARD_JSON, REGISTRY_PLAYERS_JSON]:
        if not path.exists():
            continue
        data = read_json(path)
        players = data.get("players", []) if isinstance(data, dict) else data
        for player in players:
            if not isinstance(player, dict):
                continue
            profile = profile_from_mapping(player)
            if not profile.fide_id:
                continue
            profiles[profile.fide_id] = merge_profile(profiles.get(profile.fide_id), profile)
    return profiles


def profile_from_static_detail(detail: dict[str, Any]) -> PlayerProfile:
    return profile_from_mapping(detail)


def profile_from_mapping(player: dict[str, Any]) -> PlayerProfile:
    fide_id = clean(player.get("fideID"))
    return PlayerProfile(
        fide_id=fide_id,
        display_name=clean(player.get("displayName")) or clean(player.get("chineseName")) or clean(player.get("name")),
        chinese_name=clean(player.get("chineseName")),
        pinyin=clean(player.get("pinyin")),
        name=clean(player.get("name")),
        federation=clean(player.get("federation")) or "CHN",
        birth_year=parse_int(player.get("birthYear")),
        standard=parse_int(player.get("standard")),
        rapid=parse_int(player.get("rapid")),
        blitz=parse_int(player.get("blitz")),
        aliases=[clean(alias) for alias in player.get("aliases", []) if clean(alias)],
    )


def merge_profile(current: PlayerProfile | None, incoming: PlayerProfile) -> PlayerProfile:
    if current is None:
        return incoming
    aliases = ordered_unique(current.aliases + incoming.aliases)
    return PlayerProfile(
        fide_id=current.fide_id or incoming.fide_id,
        display_name=first_non_empty(incoming.display_name, current.display_name),
        chinese_name=first_non_empty(incoming.chinese_name, current.chinese_name),
        pinyin=first_non_empty(incoming.pinyin, current.pinyin),
        name=first_non_empty(incoming.name, current.name),
        federation=first_non_empty(incoming.federation, current.federation, "CHN"),
        birth_year=incoming.birth_year or current.birth_year,
        standard=incoming.standard or current.standard,
        rapid=incoming.rapid or current.rapid,
        blitz=incoming.blitz or current.blitz,
        aliases=aliases,
    )


def bucket_for(buckets: dict[str, PlayerBucket], profile: PlayerProfile) -> PlayerBucket:
    bucket = buckets.get(profile.fide_id)
    if bucket is None:
        bucket = PlayerBucket(profile=profile)
        buckets[profile.fide_id] = bucket
    else:
        bucket.profile = merge_profile(bucket.profile, profile)
    return bucket


def first_matching_game(games_by_key: dict[str, list[str]], games: list[str], entry: dict[str, Any]) -> str:
    key = game_key_from_entry(entry)
    exact = games_by_key.get(key)
    if exact:
        return exact[0]
    for game in games:
        if loose_match(pgn_headers(game), entry):
            return game
    return ""


def game_key_from_entry(entry: dict[str, Any]) -> str:
    return "|".join(
        [
            normalize_key(entry.get("event")),
            date_key(entry.get("date")),
            normalize_key(entry.get("white")),
            normalize_key(entry.get("black")),
            normalize_key(entry.get("result")),
        ]
    )


def game_key_from_headers(headers: dict[str, str]) -> str:
    return "|".join(
        [
            normalize_key(headers.get("Event")),
            date_key(headers.get("EventDate") or headers.get("Date")),
            normalize_key(headers.get("White")),
            normalize_key(headers.get("Black")),
            normalize_key(headers.get("Result")),
        ]
    )


def loose_match(headers: dict[str, str], entry: dict[str, Any]) -> bool:
    return (
        normalize_key(headers.get("Event")) == normalize_key(entry.get("event"))
        and date_key(headers.get("EventDate") or headers.get("Date")) == date_key(entry.get("date"))
        and normalize_key(entry.get("white")) in normalize_key(headers.get("White"))
        and normalize_key(entry.get("black")) in normalize_key(headers.get("Black"))
    )


def split_pgn_games(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n")
    starts = [match.start() for match in re.finditer(r'^\[Event\s+"', normalized, flags=re.MULTILINE | re.IGNORECASE)]
    result = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(normalized)
        game = normalized[start:end].strip()
        if game:
            result.append(game)
    return result


def pgn_headers(game: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for match in re.finditer(r'^\[([A-Za-z0-9_]+)\s+"((?:\\"|[^"])*)"\]', game, flags=re.MULTILINE):
        headers[match.group(1)] = clean(match.group(2).replace('\\"', '"'))
    return headers


def repair_pgn_text(text: str) -> str:
    def replacement(match: re.Match[str]) -> str:
        tag = match.group(1)
        value = clean(match.group(2).replace('\\"', '"'))
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'[{tag} "{escaped}"]'

    return re.sub(r'^\[([A-Za-z0-9_]+)\s+"((?:\\"|[^"])*)"\]', replacement, text, flags=re.MULTILINE)


def stable_game_hash(game: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", " ", game).strip().encode("utf-8")).hexdigest()


def natural_stage_for_player(profile: PlayerProfile, date: str) -> str:
    if profile.birth_year and date[:4].isdigit():
        stage = stage_for_age(int(date[:4]) - profile.birth_year)
        if stage:
            return stage
    return ""


def event_stage_from_name(event_name: str) -> str:
    upper = event_name.upper()
    for stage in ["U18", "U16", "U14", "U12", "U10", "U8"]:
        if stage in upper:
            return stage
    return ""


def stage_for_player(profile: PlayerProfile, date: str, event_name: str) -> str:
    """Backward-compatible aggregate stage: prefer natural age over entered section."""
    return natural_stage_for_player(profile, date) or event_stage_from_name(event_name)


def stage_for_age(age: int) -> str:
    # Delegates to the shared age_groups module: youth U8-U18 plus the
    # exclusive "adult" stage (19+) so adult games get their own PGN pack.
    from age_groups import stage_for_age as _shared
    return _shared(age)


def _unused_legacy_stage_for_age(age: int) -> str:
    for stage, lower, upper in [
        ("U8", 7, 8),
        ("U10", 9, 10),
        ("U12", 11, 12),
        ("U14", 13, 14),
        ("U16", 15, 16),
        ("U18", 17, 18),
    ]:
        if lower <= age <= upper:
            return stage
    return ""


def role_for_profile(profile: PlayerProfile, headers: dict[str, str]) -> str:
    names = [profile.display_name, profile.name, profile.chinese_name, profile.pinyin, *profile.aliases]
    normalized_names = {normalize_key(name) for name in names if name}
    if normalize_key(headers.get("White")) in normalized_names:
        return "white"
    if normalize_key(headers.get("Black")) in normalized_names:
        return "black"
    return ""


def reset_output_roots() -> None:
    for root in [OUTPUT_INDEX_ROOT, OUTPUT_PGN_ROOT]:
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True, exist_ok=True)


def docs_path(public_path: str) -> pathlib.Path:
    clean_path = public_path[5:] if public_path.startswith("data/") else public_path
    return DOCS_DATA / clean_path


def public_data_path(path: pathlib.Path) -> str:
    return "data/" + str(path.relative_to(DOCS_DATA))


def normalize_pgn_date(text: Any) -> str:
    value = clean(text)
    if re.match(r"^\d{4}\.\d{2}\.\d{2}$", value):
        return value.replace(".", "-")
    if re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        return value
    return ""


def date_key(value: Any) -> str:
    return re.sub(r"\D+", "", str(value or ""))


def normalize_key(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", clean(value).casefold())


def parse_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    text = re.sub(r"[,\s]", "", str(value or ""))
    return int(text) if text.isdigit() else None


def first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return ""


def ordered_unique(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def without_empty(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value not in (None, "", [], {})}


def clean(value: Any) -> str:
    # Repair BEFORE collapsing whitespace: mojibake often contains NBSP
    # (\xa0) and NEL (\x85), which count as Unicode whitespace, so collapsing
    # first would destroy the byte sequence and make the text unrecoverable.
    return re.sub(r"\s+", " ", repair_mojibake(str(value or ""))).strip()


def repair_mojibake(text: str) -> str:
    if not text or not looks_like_mojibake(text):
        return text
    try:
        decoded = text.encode("latin-1").decode("utf-8")
    except UnicodeError:
        return _repair_space_damaged_mojibake(text)
    if "\ufffd" in decoded:
        return text
    return decoded if mojibake_score(decoded) < mojibake_score(text) else text


def _repair_space_damaged_mojibake(text: str) -> str:
    """Salvage mojibake whose whitespace bytes were collapsed to plain spaces
    by an earlier pipeline run (e.g. "\u00e5 \u00a8\u00e5\u00bd" for \u5168\u56fd, where NEL \\x85 became
    a space). Spaces adjacent to high latin-1 bytes are re-tried as \\x85/\\xa0."""
    try:
        raw = bytearray(text.encode("latin-1"))
    except UnicodeError:
        return text
    suspects = [
        i for i, b in enumerate(raw)
        if b == 0x20
        and ((i > 0 and raw[i - 1] >= 0x80) or (i + 1 < len(raw) and raw[i + 1] >= 0x80))
    ]
    if not suspects or len(suspects) > 8:  # cap the search space
        return text
    best = None
    for mask in range(3 ** len(suspects)):
        candidate = bytearray(raw)
        m = mask
        for pos in suspects:
            choice = m % 3
            m //= 3
            if choice == 1:
                candidate[pos] = 0x85
            elif choice == 2:
                candidate[pos] = 0xA0
        try:
            decoded = bytes(candidate).decode("utf-8")
        except UnicodeError:
            continue
        if "\ufffd" in decoded:
            continue
        score = mojibake_score(decoded)
        if score >= mojibake_score(text):
            continue
        # Ambiguous bytes (e.g. \x85 vs \xa0 both decode) are resolved by
        # preferring decodings with more domain-common CJK characters.
        rank = (score - sum(2 for ch in decoded if ch in _COMMON_CJK), decoded)
        if best is None or rank < best:
            best = rank
    return best[1] if best else text


_COMMON_CJK = (
    "年全国国际象棋公开赛站锦标青少联乙甲级杯组个人团体届冠军中协大师"
    "省市区学校俱乐部锦标赛春夏秋冬季月日第轮"
)


def looks_like_mojibake(text: str) -> bool:
    return bool(re.search(r"[ÃÂâåæèéäï]|[\u0080-\u009f]", text))


def mojibake_score(text: str) -> int:
    marker_count = len(re.findall(r"[ÃÂâåæèéäï]|[\u0080-\u009f]", text))
    replacement_count = text.count("\ufffd")
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", text))
    return marker_count * 3 + replacement_count * 20 - cjk_count


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


if __name__ == "__main__":
    raise SystemExit(main())
