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
import os
import pathlib
import re
from dataclasses import dataclass, field
from typing import Any

from build_event_catalog import ROUND_ITEM_RE, TEST_NAME_RE, has_chinese_text
from snapshot_context import stamp
from stable_json import write_json as write_stable_json


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS_DATA = REPO_ROOT / "docs" / "data"
STATIC_INDEX_ROOT = DOCS_DATA / "index"
STATIC_PLAYER_ROOT = STATIC_INDEX_ROOT / "players"
REGISTRY_PLAYERS_JSON = DOCS_DATA / "registry" / "players.json"
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
    federation: str = ""
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
                "federation": self.federation,
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
    broadcast_name: str = ""
    round: str = ""
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
                # De-sourcing contract: the Chess-Results identity never enters
                # public projections. Lichess stays for CC BY-SA attribution.
                "source": "" if self.source.lower().startswith("chess-results") else self.source,
                "round": self.round,
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
        # Canonical PGN fingerprint (players + result + normalized movetext):
        # the same game delivered by two providers with different headers is
        # still one Game (plan §5, "同指纹棋局只保留一个").
        fingerprint = game_fingerprint(game.pgn)
        if game.sha256 in self.seen_hashes or fingerprint in self.seen_hashes:
            return False
        self.seen_hashes.add(game.sha256)
        self.seen_hashes.add(fingerprint)
        self.games.append(game)
        return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Build static by-player PGN packs.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--shard",
        default="",
        help="i/N: only ingest/write players with hash(fideID) %% N == i; "
             "manifest/players.json are NOT written (run --finalize after).",
    )
    parser.add_argument(
        "--finalize",
        action="store_true",
        help="Skip ingest; rebuild players.json + manifest from written per-player details.",
    )
    args = parser.parse_args()

    if args.finalize:
        manifest = finalize_from_details()
        print(json.dumps(manifest["totals"], ensure_ascii=False, indent=2))
        return 0

    shard_index, shard_count = parse_shard(args.shard)
    profiles = load_profiles()
    if shard_count > 1:
        profiles = {
            fide_id: profile for fide_id, profile in profiles.items()
            if shard_of(fide_id, shard_count) == shard_index
        }
    buckets: dict[str, PlayerBucket] = {}
    stats = {
        "eventGames": 0,
        "bulkYouthGames": 0,
        "dedupedGames": 0,
    }

    stats["eventGames"] = ingest_static_event_pgns(buckets, profiles)
    stats["bulkYouthGames"] = ingest_bulk_youth_pgns(buckets, profiles)
    stats["dedupedGames"] = sum(len(bucket.games) for bucket in buckets.values())

    existing_packages = load_existing_package_metadata() if not args.dry_run else {}
    if not args.dry_run:
        ensure_output_roots()
    manifest = write_outputs(
        buckets,
        dry_run=args.dry_run,
        existing_packages=existing_packages,
        write_aggregates=shard_count <= 1,
    )
    if not args.dry_run and shard_count <= 1:
        prune_stale_outputs(buckets)
    summary = {**stats, **manifest["totals"]}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def parse_shard(text: str) -> tuple[int, int]:
    if not text:
        return 0, 1
    index_text, _, count_text = text.partition("/")
    index, count = int(index_text), int(count_text or "1")
    if not (0 <= index < count):
        raise SystemExit(f"invalid --shard {text}")
    return index, count


def shard_of(fide_id: str, shard_count: int) -> int:
    return int(hashlib.sha256(fide_id.encode("utf-8")).hexdigest(), 16) % shard_count


def finalize_from_details() -> dict[str, Any]:
    """Aggregate manifest/players.json from per-player detail files (used
    after sharded ingest runs so aggregates always describe every player)."""
    player_summaries: list[dict[str, Any]] = []
    all_packages = all_bytes = all_games = 0
    sources: set[str] = set()
    profiles = load_profiles()
    for detail_path in sorted(OUTPUT_INDEX_ROOT.glob("fide-*.json")):
        detail = read_json(detail_path)
        player = detail.get("player") or {}
        fide_id = clean(player.get("fideID"))
        if not fide_id or fide_id not in profiles:
            continue
        packages = detail.get("packages") or []
        totals = detail.get("totals") or {}
        games = detail.get("games") or []
        all_package = next((p for p in packages if p.get("id") == "all"), packages[0] if packages else {})
        summary = {
            **player,
            "gameCount": totals.get("games", len(games)),
            "eventCount": totals.get("events", 0),
            "packageCount": totals.get("packages", len(packages)),
            "playerPgnPath": all_package.get("pgnPath"),
            "playerPgnGameCount": all_package.get("gameCount"),
            "playerIndexPath": public_data_path(detail_path),
            "stages": totals.get("stages") or {},
            "sources": sorted({s for g in games for s in [g.get("source")] if s}),
        }
        player_summaries.append(summary)
        all_packages += summary["packageCount"]
        all_bytes += totals.get("bytes", 0)
        all_games += summary["gameCount"]
        sources.update(summary["sources"])
    player_summaries.sort(key=lambda item: item.get("fideID") or "")
    manifest = stamp({
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": now(),
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
        "sources": public_sources(sources),
    })
    write_json(OUTPUT_INDEX_ROOT / "manifest.json", manifest)
    write_json(OUTPUT_INDEX_ROOT / "players.json", player_summaries)
    return manifest


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
        # Static details are last build's output and therefore never an
        # identity source. Only registry members (including reviewed transfer
        # overrides) may get a by-player derivative.
        profile = profiles.get(fide_id)
        if profile is None:
            continue
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
                source_event_name = headers.get("Event") or clean(event.get("name"))
                broadcast_name = clean(headers.get("BroadcastName"))
                if TEST_NAME_RE.search(" ".join(filter(None, [source_event_name, broadcast_name]))):
                    continue
                event_name = broadcast_name if ROUND_ITEM_RE.match(source_event_name) and broadcast_name else source_event_name
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
                    broadcast_name=broadcast_name,
                    round=clean(headers.get("Round")),
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


def load_bulk_stage(stage: dict[str, Any]) -> tuple | None:
    """Load one bulk stage's entries plus repaired games (+ key index).

    Parsing/repairing tens of thousands of bulk games is by far the most
    expensive build step, so the repaired result is cached (pickle) keyed by
    the PGN file's size+mtime. Sharded runs then share one parse."""
    import pickle

    stage_id = clean(stage.get("id"))
    index_path = clean(stage.get("indexPath"))
    pgn_path = clean(stage.get("pgnPath"))
    if not stage_id or not index_path or not pgn_path:
        return None
    index_file = docs_path(index_path)
    pgn_file = docs_path(pgn_path)
    if not index_file.exists() or not pgn_file.exists():
        return None

    entries = read_json(index_file)
    cache_dir = pathlib.Path(os.environ.get("BSP_BULK_CACHE", "")) if os.environ.get("BSP_BULK_CACHE") else None
    stat = pgn_file.stat()
    cache_file = cache_dir / f"bulk-{stage_id}-{stat.st_size}-{int(stat.st_mtime)}.pickle" if cache_dir else None
    if cache_file and cache_file.exists():
        with cache_file.open("rb") as handle:
            cached = pickle.load(handle)
        if isinstance(cached, tuple) and len(cached) == 4:
            return entries, *cached

    games = [repair_pgn_text(game) for game in split_pgn_games(pgn_file.read_text(encoding="utf-8", errors="replace"))]
    games_by_key: dict[str, list[str]] = {}
    # Loose-match acceleration: headers are parsed once per game and games are
    # bucketed by (event, date) so a fallback match never rescans the stage.
    loose_index: dict[str, list[int]] = {}
    headers_list: list[dict[str, str]] = []
    for position, game in enumerate(games):
        headers = pgn_headers(game)
        headers_list.append(headers)
        games_by_key.setdefault(game_key_from_headers(headers), []).append(game)
        loose_key = "|".join([
            normalize_key(headers.get("Event")),
            date_key(headers.get("EventDate") or headers.get("Date")),
        ])
        loose_index.setdefault(loose_key, []).append(position)
    payload = (games, games_by_key, headers_list, loose_index)
    if cache_file:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        with cache_file.open("wb") as handle:
            pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)
    return entries, *payload


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
        loaded = load_bulk_stage(stage)
        if loaded is None:
            continue
        entries, games, games_by_key, headers_list, loose_index = loaded

        for entry in entries:
            fide_id = clean(entry.get("fideID"))
            if not fide_id:
                continue
            profile = profiles.get(fide_id)
            if profile is None:
                continue
            game = first_matching_game(games_by_key, games, entry, headers_list, loose_index)
            if not game:
                continue
            headers = pgn_headers(game)
            source_event_name = headers.get("Event") or clean(entry.get("event"))
            broadcast_name = clean(headers.get("BroadcastName"))
            if TEST_NAME_RE.search(" ".join(filter(None, [source_event_name, broadcast_name]))):
                continue
            event_name = broadcast_name if ROUND_ITEM_RE.match(source_event_name) and broadcast_name else source_event_name
            game_record = PlayerGame(
                pgn=game,
                event=event_name,
                date=normalize_pgn_date(headers.get("EventDate") or headers.get("Date") or clean(entry.get("date"))),
                white=headers.get("White") or clean(entry.get("white")),
                black=headers.get("Black") or clean(entry.get("black")),
                result=headers.get("Result") or clean(entry.get("result")),
                source=clean(entry.get("source")) or "Lichess Broadcasts",
                broadcast_name=broadcast_name,
                round=clean(headers.get("Round")),
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


def write_outputs(
    buckets: dict[str, PlayerBucket],
    dry_run: bool,
    existing_packages: dict[str, tuple[str, str]],
    write_aggregates: bool = True,
) -> dict[str, Any]:
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
            existing_packages=existing_packages,
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
                    existing_packages=existing_packages,
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
            "sources": public_sources({game.source for game in bucket.games}),
        }
        player_summaries.append(summary)

    manifest = stamp({
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
        "sources": public_sources(sources),
    })

    if not dry_run and write_aggregates:
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
    existing_packages: dict[str, tuple[str, str]],
) -> dict[str, Any]:
    body = "\n\n".join(game.pgn.strip() for game in games if game.pgn.strip()).strip()
    # Upstream PGN wraps movetext with trailing spaces; normalize so packs
    # stay `git diff --check` clean without altering game semantics.
    body = "\n".join(line.rstrip() for line in body.split("\n"))
    created_at = now()
    semantic_text = "\n".join(
        [
            "% Built by 中国棋手 PGN static by-player index",
            f"% FIDE: {fide_id}",
            f"% Package: {package_id}",
            f"% Games: {len(games)}",
            "% Created:",
            "",
            body,
            "",
        ]
    )
    relative = str(target.relative_to(OUTPUT_PGN_ROOT))
    previous = existing_packages.get(relative)
    semantic_hash = semantic_package_hash(semantic_text)
    if previous and previous[1] == semantic_hash:
        created_at = previous[0]
    text = semantic_text.replace("% Created:\n", f"% Created: {created_at}\n", 1)
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
        "sources": public_sources({game.source for game in games}),
    }


def public_sources(values: set[str]) -> list[str]:
    """Public source labels: Lichess attribution stays, Chess-Results never."""
    return sorted({v for v in values if v and not v.lower().startswith("chess-results")})


def semantic_package_hash(text: str) -> str:
    normalized = re.sub(r"^% Created:.*$", "% Created:", text, count=1, flags=re.MULTILINE)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def load_existing_package_metadata() -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    if not OUTPUT_PGN_ROOT.exists():
        return result
    for path in OUTPUT_PGN_ROOT.rglob("*.pgn"):
        # The "% Created:" preamble sits in the first few lines; reading the
        # whole multi-MB pack (× thousands of packs) made rebuilds minutes
        # slower for no benefit. The semantic hash below still requires the
        # full text, so it is only computed for packs whose header matched.
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            head = handle.read(512)
        match = re.search(r"^% Created:\s*(.+?)\s*$", head, flags=re.MULTILINE)
        text = path.read_text(encoding="utf-8", errors="replace") if match else ""
        if match:
            result[str(path.relative_to(OUTPUT_PGN_ROOT))] = (
                match.group(1),
                semantic_package_hash(text),
            )
    return result


_event_mappings: dict[str, dict[str, str]] | None = None


def event_mappings() -> dict[str, dict[str, str]]:
    """tournamentID -> reviewed canonical/chinese-name mapping (read-only)."""
    global _event_mappings
    if _event_mappings is None:
        _event_mappings = {}
        mapping_csv = REPO_ROOT / "data" / "community" / "tournament-name-mappings.csv"
        if mapping_csv.exists():
            import csv as _csv
            with mapping_csv.open("r", encoding="utf-8-sig", newline="") as handle:
                for row in _csv.DictReader(handle):
                    tid = clean(row.get("tournament_id"))
                    if tid:
                        _event_mappings[tid] = {
                            "canonicalEventID": clean(row.get("canonical_event_id")),
                            "chineseName": clean(row.get("chinese_name")),
                        }
    return _event_mappings


def event_summaries(games: list[PlayerGame]) -> list[dict[str, Any]]:
    """One row per canonical event: a tournament that reached us both through
    a TNR capture and a broadcast slug must not appear twice (plan §9.1)."""
    mappings = event_mappings()
    events: dict[str, dict[str, Any]] = {}
    for game in games:
        if TEST_NAME_RE.search(" ".join(filter(None, [game.event, game.broadcast_name]))):
            continue
        if ROUND_ITEM_RE.match(game.event) and not game.broadcast_name:
            # Keep the playable game in the package, but do not promote an
            # orphaned round/chapter title to a tournament summary.
            continue
        event_name = (
            game.broadcast_name
            if ROUND_ITEM_RE.match(game.event) and game.broadcast_name
            else game.event
        )
        mapping = mappings.get(game.tournament_id, {})
        canonical = mapping.get("canonicalEventID") or ""
        public_name = mapping.get("chineseName") or event_name
        if not has_chinese_text(public_name):
            # Untranslated source titles remain available on individual game
            # records, but must not leak back into the public event-summary UI.
            continue
        if canonical:
            key = f"canonical:{canonical}:{game.event_stage or game.stage}"
        elif game.tournament_id:
            key = f"tnr:{game.tournament_id}"
        elif game.broadcast_name:
            key = f"broadcast:{normalize_key(game.broadcast_name)}"
        else:
            key = f"name:{normalize_key(game.event)}|{game.date}"
        public_source = "" if game.source.lower().startswith("chess-results") else game.source
        event = events.setdefault(
            key,
            {
                "source": public_source,
                "name": public_name,
                "date": game.date,
                "tournamentID": game.tournament_id,
                "canonicalEventID": canonical or None,
                "stage": game.stage,
                "naturalStage": game.natural_stage,
                "eventStage": game.event_stage,
                "gameCount": 0,
                "results": {},
            },
        )
        if not event.get("date") and game.date:
            event["date"] = game.date
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
    if not REGISTRY_PLAYERS_JSON.exists():
        return profiles
    data = read_json(REGISTRY_PLAYERS_JSON)
    players = data.get("players", []) if isinstance(data, dict) else data
    for player in players:
        if not isinstance(player, dict):
            continue
        profile = profile_from_mapping(player)
        if profile.fide_id:
            profiles[profile.fide_id] = profile
    return profiles


def profile_from_mapping(player: dict[str, Any]) -> PlayerProfile:
    fide_id = clean(player.get("fideID"))
    return PlayerProfile(
        fide_id=fide_id,
        display_name=clean(player.get("displayName")) or clean(player.get("chineseName")) or clean(player.get("name")),
        chinese_name=clean(player.get("chineseName")),
        pinyin=clean(player.get("pinyin")),
        name=clean(player.get("name")),
        federation=clean(player.get("federation")),
        birth_year=parse_int(player.get("birthYear")),
        standard=parse_int(player.get("standard")),
        rapid=parse_int(player.get("rapid")),
        blitz=parse_int(player.get("blitz")),
        aliases=[clean(alias) for alias in player.get("aliases", []) if clean(alias)],
    )


def bucket_for(buckets: dict[str, PlayerBucket], profile: PlayerProfile) -> PlayerBucket:
    bucket = buckets.get(profile.fide_id)
    if bucket is None:
        bucket = PlayerBucket(profile=profile)
        buckets[profile.fide_id] = bucket
    return bucket


def first_matching_game(
    games_by_key: dict[str, list[str]],
    games: list[str],
    entry: dict[str, Any],
    headers_list: list[dict[str, str]] | None = None,
    loose_index: dict[str, list[int]] | None = None,
) -> str:
    key = game_key_from_entry(entry)
    exact = games_by_key.get(key)
    if exact:
        return exact[0]
    if headers_list is not None and loose_index is not None:
        loose_key = "|".join([normalize_key(entry.get("event")), date_key(entry.get("date"))])
        for position in loose_index.get(loose_key, []):
            if loose_match(headers_list[position], entry):
                return games[position]
        return ""
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


_MOVETEXT_NOISE_RE = re.compile(r"\{[^}]*\}|\$\d+|\d+\.(\.\.)?|[?!]+")


def game_fingerprint(game: str) -> str:
    """Provider-independent fingerprint: players, result and bare movetext.

    Header cosmetics (site, round labels, broadcast titles) differ between
    providers for the same physical game; the moves do not."""
    headers = pgn_headers(game)
    movetext = re.sub(r"^\[[^\]]*\]\s*$", "", game, flags=re.MULTILINE)
    movetext = _MOVETEXT_NOISE_RE.sub(" ", movetext)
    movetext = re.sub(r"\s+", " ", movetext).strip()
    seed = "|".join([
        normalize_key(headers.get("White")),
        normalize_key(headers.get("Black")),
        normalize_key(headers.get("Result")),
        movetext,
    ])
    return "fp:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()


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


def ensure_output_roots() -> None:
    for root in [OUTPUT_INDEX_ROOT, OUTPUT_PGN_ROOT]:
        root.mkdir(parents=True, exist_ok=True)


def prune_stale_outputs(buckets: dict[str, PlayerBucket]) -> None:
    active = {fide_id: bucket for fide_id, bucket in buckets.items() if bucket.games}
    expected_index = {OUTPUT_INDEX_ROOT / "manifest.json", OUTPUT_INDEX_ROOT / "players.json"}
    expected_pgn: set[pathlib.Path] = set()
    for fide_id, bucket in active.items():
        expected_index.add(OUTPUT_INDEX_ROOT / f"fide-{fide_id}.json")
        player_dir = OUTPUT_PGN_ROOT / f"fide-{fide_id}"
        expected_pgn.add(player_dir / "all.pgn")
        for stage_id in ["U8", "U10", "U12", "U14", "U16", "U18", "adult"]:
            if any(game.stage == stage_id for game in bucket.games):
                expected_pgn.add(player_dir / f"{stage_id}.pgn")
    for root, expected in ((OUTPUT_INDEX_ROOT, expected_index), (OUTPUT_PGN_ROOT, expected_pgn)):
        for path in root.rglob("*"):
            if path.is_file() and path not in expected:
                path.unlink()
        for directory in sorted((path for path in root.rglob("*") if path.is_dir()), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass


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
    write_stable_json(path, data, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    raise SystemExit(main())
