#!/usr/bin/env python3
"""Audit PGN source coverage and stage Chess-Results discovery batches."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import html
import html.parser
import json
import pathlib
import re
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from source_http import open_response
from source_policy import require_chess_results_publication
from stable_json import write_json as write_stable_json


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS_DATA = REPO_ROOT / "docs" / "data"
AUDIT_ROOT = DOCS_DATA / "audit"
INDEX_ROOT = DOCS_DATA / "index"
STATIC_PLAYER_ROOT = INDEX_ROOT / "players"
BY_PLAYER_ROOT = INDEX_ROOT / "by-player"
REGISTRY_PLAYERS_JSON = DOCS_DATA / "registry" / "players.json"
YOUTH_JSON = DOCS_DATA / "youth-leaderboards.json"
STATIC_PGN_ROOT = DOCS_DATA / "pgn"
CHESS_RESULTS_FORM_URL = "https://chess-results.com/PartieSuche.aspx?lan=1"
CHESS_RESULTS_TOURNAMENT_URL = "https://chess-results.com/tnr{tournament_id}.aspx?lan=1"
USER_AGENT = "ChinaChessPlayerPGNReconciler/1.0"
SCHEMA_VERSION = 1
CURRENT_YEAR = dt.datetime.now(dt.timezone.utc).year


@dataclass
class PlayerProfile:
    fide_id: str
    display_name: str = ""
    chinese_name: str = ""
    name: str = ""
    pinyin: str = ""
    federation: str = "CHN"
    birth_year: int | None = None
    standard: int | None = None
    rapid: int | None = None
    blitz: int | None = None
    sources: set[str] = field(default_factory=set)

    @property
    def best_rating(self) -> int:
        return max([value or 0 for value in [self.standard, self.rapid, self.blitz]])

    @property
    def youth_stage(self) -> str:
        if not self.birth_year:
            return ""
        age = CURRENT_YEAR - self.birth_year
        for limit in [8, 10, 12, 14, 16, 18]:
            if age <= limit:
                return f"U{limit}"
        return ""


@dataclass
class StaticEvent:
    fide_id: str
    display_name: str
    source: str
    tournament_id: str
    name: str
    date: str
    pgn_path: str
    game_count: int
    source_url: str


class TitleParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_title = False
        self.title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "title":
            self.in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)

    @property
    def title(self) -> str:
        return normalize_space(" ".join(self.title_parts))


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
    parser = argparse.ArgumentParser(description="Audit source mix and stage Chess-Results discovery batches.")
    parser.add_argument("--write-audit", action="store_true", help="write docs/data/audit reports")
    parser.add_argument("--discover-chess-results", action="store_true", help="probe Chess-Results and write discovery reports")
    parser.add_argument("--player", action="append", default=[], help="FIDE ID to include in the next player batch; repeatable")
    parser.add_argument("--max-players", type=int, default=80, help="player IDs to stage for Chess-Results scanning")
    parser.add_argument("--max-known-missing", type=int, default=40, help="known missing Chess-Results pairs to probe")
    parser.add_argument("--max-neighbor-ids", type=int, default=40, help="neighbor TournamentID pages to probe")
    parser.add_argument("--neighbor-window", type=int, default=4, help="TournamentID +/- window around known TNR seeds")
    parser.add_argument("--delay", type=float, default=1.0, help="delay between Chess-Results requests")
    parser.add_argument("--batch-output", type=pathlib.Path, default=AUDIT_ROOT / "chess-results-player-batch.csv")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.discover_chess_results:
        require_chess_results_publication()

    if not args.write_audit and not args.discover_chess_results:
        args.write_audit = True

    AUDIT_ROOT.mkdir(parents=True, exist_ok=True)
    profiles = load_profiles()
    by_player = load_by_player_indexes()
    static_events = load_static_events()

    outputs: dict[str, Any] = {
        "generatedAt": now(),
        "profiles": len(profiles),
        "byPlayerIndexes": len(by_player),
        "staticEvents": len(static_events),
    }

    if args.write_audit:
        outputs["audit"] = write_audit_reports(profiles, by_player, static_events, dry_run=args.dry_run)

    if args.discover_chess_results:
        outputs["chessResultsDiscovery"] = discover_chess_results(
            args=args,
            profiles=profiles,
            by_player=by_player,
            static_events=static_events,
            dry_run=args.dry_run,
        )

    print(json.dumps(outputs, ensure_ascii=False, indent=2))
    return 0


def write_audit_reports(
    profiles: dict[str, PlayerProfile],
    by_player: dict[str, dict[str, Any]],
    static_events: list[StaticEvent],
    dry_run: bool,
) -> dict[str, Any]:
    source_coverage = build_source_coverage(by_player)
    missing_events = build_missing_pgn_events(static_events)
    player_coverage = build_player_coverage(profiles, by_player)
    known_targets = build_known_chess_results_targets(static_events)
    summary = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": now(),
        "reports": {
            "sourceCoverage": "data/audit/source-coverage.json",
            "missingPgnEvents": "data/audit/missing-pgn-events.json",
            "playerCoverage": "data/audit/player-coverage.json",
            "chessResultsTargets": "data/audit/chess-results-targets.json",
        },
        "headline": {
            "byPlayerGames": source_coverage["totals"]["games"],
            "sourceCount": len(source_coverage["sources"]),
            "registryPlayers": player_coverage["totals"]["registryPlayers"],
            "playersWithPgn": player_coverage["totals"]["playersWithPgn"],
            "knownChessResultsPairs": known_targets["totals"]["pairs"],
            "missingChessResultsPairs": missing_events["totals"]["missing"],
            "targetableMissingChessResultsPairs": missing_events["totals"]["targetable"],
        },
    }

    if not dry_run:
        write_json(AUDIT_ROOT / "source-coverage.json", source_coverage)
        write_json(AUDIT_ROOT / "missing-pgn-events.json", missing_events)
        write_json(AUDIT_ROOT / "player-coverage.json", player_coverage)
        write_json(AUDIT_ROOT / "chess-results-targets.json", known_targets)
        write_json(AUDIT_ROOT / "manifest.json", summary)
        write_audit_markdown(AUDIT_ROOT / "README.md", summary, source_coverage, player_coverage, missing_events)
    return summary["headline"]


def build_source_coverage(by_player: dict[str, dict[str, Any]]) -> dict[str, Any]:
    game_count_by_source: Counter[str] = Counter()
    player_ids_by_source: dict[str, set[str]] = defaultdict(set)
    events_by_source: dict[str, set[str]] = defaultdict(set)
    stage_counts: dict[str, Counter[str]] = defaultdict(Counter)
    player_mix: Counter[str] = Counter()
    player_rows: list[dict[str, Any]] = []
    birth_bucket_counts: dict[str, Counter[str]] = defaultdict(Counter)

    for fide_id, detail in by_player.items():
        games = detail.get("games", [])
        player_sources = Counter(clean(game.get("source")) or "Unknown" for game in games)
        if not player_sources:
            continue
        if len(player_sources) == 1:
            player_mix[next(iter(player_sources))] += 1
        else:
            player_mix["mixed"] += 1
        player = detail.get("player", {})
        birth_year = parse_int(player.get("birthYear"))
        birth_bucket = "unknown"
        if birth_year:
            birth_bucket = "youth" if CURRENT_YEAR - birth_year <= 18 else "adult_or_older"

        for source, count in player_sources.items():
            player_ids_by_source[source].add(fide_id)
            birth_bucket_counts[birth_bucket][source] += count
        for game in games:
            source = clean(game.get("source")) or "Unknown"
            stage = clean(game.get("stage")) or "none"
            event_key = "|".join([source, clean(game.get("event")), clean(game.get("date")), clean(game.get("tournamentID"))])
            game_count_by_source[source] += 1
            events_by_source[source].add(event_key)
            stage_counts[stage][source] += 1

        player_rows.append(
            {
                "fideID": fide_id,
                "displayName": player.get("displayName") or player.get("name") or f"FIDE {fide_id}",
                "games": len(games),
                "sources": dict(player_sources),
                "dominantSource": player_sources.most_common(1)[0][0],
                "dominantSourcePercent": percent(player_sources.most_common(1)[0][1], len(games)),
            }
        )

    total_games = sum(game_count_by_source.values())
    sources = [
        {
            "source": source,
            "games": games,
            "percent": percent(games, total_games),
            "players": len(player_ids_by_source[source]),
            "events": len(events_by_source[source]),
        }
        for source, games in game_count_by_source.most_common()
    ]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": now(),
        "totals": {
            "players": len(player_rows),
            "games": total_games,
            "events": len({event for events in events_by_source.values() for event in events}),
        },
        "sources": sources,
        "stageSourceCoverage": {
            stage: [
                {"source": source, "games": games, "percent": percent(games, sum(counter.values()))}
                for source, games in counter.most_common()
            ]
            for stage, counter in sorted(stage_counts.items(), key=lambda item: stage_sort_key(item[0]))
        },
        "birthBucketSourceCoverage": {
            bucket: [
                {"source": source, "games": games, "percent": percent(games, sum(counter.values()))}
                for source, games in counter.most_common()
            ]
            for bucket, counter in sorted(birth_bucket_counts.items())
        },
        "playerSourceClasses": dict(player_mix),
        "players": sorted(player_rows, key=lambda item: (item["games"], item["fideID"]), reverse=True),
    }


def build_missing_pgn_events(static_events: list[StaticEvent]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    source_counts: Counter[str] = Counter()
    targetable = 0
    for event in static_events:
        source_counts[event.source] += 1
        if slug(event.source) != "chess-results":
            continue
        path_exists = bool(event.pgn_path and docs_path(event.pgn_path).exists())
        if path_exists and event.game_count > 0:
            continue
        is_targetable = bool(event.fide_id and is_numeric_tnr(event.tournament_id))
        targetable += 1 if is_targetable else 0
        rows.append(
            {
                "fideID": event.fide_id,
                "displayName": event.display_name,
                "source": event.source,
                "tournamentID": event.tournament_id,
                "event": event.name,
                "date": event.date,
                "sourceURL": event.source_url or tournament_url(event.tournament_id),
                "pgnPath": event.pgn_path,
                "gameCount": event.game_count,
                "targetable": is_targetable,
                "reason": "missing_file" if event.pgn_path else "no_static_pgn_path",
            }
        )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": now(),
        "totals": {
            "staticEventRows": len(static_events),
            "chessResultsRows": source_counts.get("Chess-Results", 0),
            "missing": len(rows),
            "targetable": targetable,
        },
        "events": sorted(rows, key=lambda item: (item.get("date") or "", item.get("tournamentID") or ""), reverse=True),
    }


def build_player_coverage(profiles: dict[str, PlayerProfile], by_player: dict[str, dict[str, Any]]) -> dict[str, Any]:
    with_pgn = set(by_player)
    registry_ids = set(profiles)
    uncovered = [profile for fide_id, profile in profiles.items() if fide_id not in with_pgn]
    youth_uncovered = [profile for profile in uncovered if profile.youth_stage]
    rated_uncovered = [profile for profile in uncovered if profile.best_rating]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": now(),
        "totals": {
            "registryPlayers": len(registry_ids),
            "playersWithPgn": len(with_pgn),
            "coveragePercent": percent(len(with_pgn), len(registry_ids)),
            "uncoveredPlayers": len(registry_ids - with_pgn),
            "uncoveredYouthPlayers": len(youth_uncovered),
            "uncoveredRatedPlayers": len(rated_uncovered),
        },
        "stageCoverage": build_stage_coverage(profiles, with_pgn),
        "uncoveredPrioritySamples": [player_payload(profile) for profile in prioritized_players(uncovered, with_pgn)[:200]],
    }


def build_stage_coverage(profiles: dict[str, PlayerProfile], with_pgn: set[str]) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[PlayerProfile]] = defaultdict(list)
    for profile in profiles.values():
        if profile.youth_stage:
            buckets[profile.youth_stage].append(profile)
    output: dict[str, dict[str, Any]] = {}
    for stage in ["U8", "U10", "U12", "U14", "U16", "U18"]:
        players = buckets.get(stage, [])
        covered = [profile for profile in players if profile.fide_id in with_pgn]
        output[stage] = {
            "players": len(players),
            "playersWithPgn": len(covered),
            "coveragePercent": percent(len(covered), len(players)),
        }
    return output


def build_known_chess_results_targets(static_events: list[StaticEvent]) -> dict[str, Any]:
    targets: dict[tuple[str, str], dict[str, Any]] = {}
    for event in static_events:
        if slug(event.source) != "chess-results" or not event.fide_id or not event.tournament_id:
            continue
        key = (event.fide_id, event.tournament_id)
        targets[key] = {
            "fideID": event.fide_id,
            "displayName": event.display_name,
            "tournamentID": event.tournament_id,
            "event": event.name,
            "date": event.date,
            "sourceURL": event.source_url or tournament_url(event.tournament_id),
            "hasStaticPgn": bool(event.pgn_path and docs_path(event.pgn_path).exists() and event.game_count > 0),
            "targetable": is_numeric_tnr(event.tournament_id),
        }
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": now(),
        "totals": {
            "pairs": len(targets),
            "targetablePairs": sum(1 for target in targets.values() if target["targetable"]),
            "withStaticPgn": sum(1 for target in targets.values() if target["hasStaticPgn"]),
        },
        "targets": sorted(targets.values(), key=lambda item: (item.get("date") or "", item.get("tournamentID") or ""), reverse=True),
    }


def discover_chess_results(
    args: argparse.Namespace,
    profiles: dict[str, PlayerProfile],
    by_player: dict[str, dict[str, Any]],
    static_events: list[StaticEvent],
    dry_run: bool,
) -> dict[str, Any]:
    state = load_scan_state()
    missing = build_missing_pgn_events(static_events)["events"]
    targetable_missing = [event for event in missing if event.get("targetable")]
    known_probe_results = probe_known_missing(targetable_missing[: args.max_known_missing], args.delay, dry_run)
    neighbor_results = probe_neighbor_tournaments(static_events, args.max_neighbor_ids, args.neighbor_window, args.delay, dry_run)
    selected_players = select_player_batch(args, profiles, by_player, state)
    batch_rows = [player_payload(player) for player in selected_players]

    if not dry_run:
        write_player_batch(args.batch_output, selected_players)
        update_scan_state(state, selected_players)
        write_json(AUDIT_ROOT / "chess-results-scan-state.json", state)

    candidates = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": now(),
        "knownMissingPgnCandidates": known_probe_results["candidates"],
        "tournamentPageCandidates": neighbor_results["candidates"],
        "playerBatch": batch_rows,
    }
    discovery = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": now(),
        "limits": {
            "maxPlayers": args.max_players,
            "maxKnownMissing": args.max_known_missing,
            "maxNeighborIds": args.max_neighbor_ids,
            "neighborWindow": args.neighbor_window,
            "delay": args.delay,
        },
        "totals": {
            "playerBatch": len(selected_players),
            "knownMissingProbed": known_probe_results["requests"],
            "knownMissingWithPgn": len(known_probe_results["candidates"]),
            "neighborPagesProbed": neighbor_results["requests"],
            "neighborCandidatePages": len(neighbor_results["candidates"]),
            "errors": len(known_probe_results["errors"]) + len(neighbor_results["errors"]),
        },
        "knownMissingProbe": known_probe_results,
        "neighborProbe": neighbor_results,
        "playerBatchPath": public_data_path(args.batch_output),
    }
    if not dry_run:
        write_json(AUDIT_ROOT / "candidate-pgn-packages.json", candidates)
        write_json(AUDIT_ROOT / "chess-results-discovery.json", discovery)
    return discovery["totals"]


def probe_known_missing(events: list[dict[str, Any]], delay: float, dry_run: bool) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    requests = 0
    if dry_run:
        return {"requests": len(events), "candidates": [], "errors": [], "dryRun": True}

    for event in events:
        requests += 1
        try:
            pgn = download_chess_results_pgn(str(event["fideID"]), str(event["tournamentID"]))
            game_count = count_pgn_games(pgn)
            if game_count:
                digest = hashlib.sha256(pgn.encode("utf-8")).hexdigest()
                candidates.append(
                    {
                        **event,
                        "availableGames": game_count,
                        "sha256": digest,
                        "bytes": len(pgn.encode("utf-8")),
                        "checkedAt": now(),
                    }
                )
            else:
                failures.append({**event, "checkedAt": now(), "reason": "empty_pgn_response"})
        except Exception as error:  # noqa: BLE001 - discovery should keep moving.
            failures.append({**event, "checkedAt": now(), "reason": str(error)})
        if delay:
            time.sleep(delay)
    return {"requests": requests, "candidates": candidates, "errors": failures}


def probe_neighbor_tournaments(
    static_events: list[StaticEvent],
    max_neighbor_ids: int,
    neighbor_window: int,
    delay: float,
    dry_run: bool,
) -> dict[str, Any]:
    seeds = sorted(
        {
            int(event.tournament_id)
            for event in static_events
            if slug(event.source) == "chess-results" and is_numeric_tnr(event.tournament_id)
        },
        reverse=True,
    )
    targets: list[int] = []
    seen = set(seeds)
    for seed in seeds:
        for offset in range(-neighbor_window, neighbor_window + 1):
            if offset == 0:
                continue
            candidate = seed + offset
            if candidate <= 0 or candidate in seen:
                continue
            seen.add(candidate)
            targets.append(candidate)
            if len(targets) >= max_neighbor_ids:
                break
        if len(targets) >= max_neighbor_ids:
            break

    if dry_run:
        return {"requests": len(targets), "candidates": [], "errors": [], "dryRun": True}

    candidates: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for tournament_id in targets:
        try:
            page = fetch_tournament_page(str(tournament_id))
            if page["candidate"]:
                candidates.append(page)
        except Exception as error:  # noqa: BLE001 - discovery should keep moving.
            failures.append({"tournamentID": str(tournament_id), "checkedAt": now(), "reason": str(error)})
        if delay:
            time.sleep(delay)
    return {"requests": len(targets), "candidates": candidates, "errors": failures}


def fetch_tournament_page(tournament_id: str) -> dict[str, Any]:
    url = tournament_url(tournament_id)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with open_url(request) as response:
        final_url = response.geturl()
        body = decode_response(response.read())
    title_parser = TitleParser()
    title_parser.feed(body)
    title = clean_title(title_parser.title)
    lowered = normalize_text(html.unescape(body))
    title_terms = ["china", "chinese", "中国", "李成智", "全国", "甲级", "棋协"]
    body_terms = ["china ", "chinese", "中国", "李成智", "全国", "甲级", "棋协"]
    title_signal = any(term in normalize_text(title) for term in title_terms)
    body_signal = any(term in lowered for term in body_terms)
    candidate = title_signal or body_signal
    return {
        "tournamentID": tournament_id,
        "url": url,
        "finalURL": final_url,
        "title": title,
        "candidate": candidate,
        "hasPgnSignal": "pgn" in lowered or "partiesuche" in lowered,
        "hasChinaSignal": title_signal or body_signal,
        "checkedAt": now(),
    }


def select_player_batch(
    args: argparse.Namespace,
    profiles: dict[str, PlayerProfile],
    by_player: dict[str, dict[str, Any]],
    state: dict[str, Any],
) -> list[PlayerProfile]:
    if args.player:
        requested = []
        for fide_id in args.player:
            profile = profiles.get(str(fide_id))
            if profile:
                requested.append(profile)
        return requested[: args.max_players or None]

    scanned = state.get("players", {}) if isinstance(state.get("players"), dict) else {}
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=45)

    def recently_planned(profile: PlayerProfile) -> bool:
        row = scanned.get(profile.fide_id, {})
        planned_at = parse_datetime(row.get("lastPlannedAt"))
        return bool(planned_at and planned_at >= cutoff)

    candidates = [profile for profile in profiles.values() if not recently_planned(profile)]
    return prioritized_players(candidates, set(by_player))[: args.max_players or None]


def prioritized_players(players: list[PlayerProfile], with_pgn: set[str]) -> list[PlayerProfile]:
    def score(profile: PlayerProfile) -> tuple[int, int, int, int, str]:
        has_no_pgn = 1 if profile.fide_id not in with_pgn else 0
        is_youth = 1 if profile.youth_stage else 0
        has_rating = 1 if profile.best_rating else 0
        return (has_no_pgn, is_youth, has_rating, profile.best_rating, profile.fide_id)

    return sorted(players, key=score, reverse=True)


def load_profiles() -> dict[str, PlayerProfile]:
    profiles: dict[str, PlayerProfile] = {}
    for path, source in [(REGISTRY_PLAYERS_JSON, "registry"), (YOUTH_JSON, "youth-leaderboard")]:
        if not path.exists():
            continue
        data = read_json(path)
        players = data.get("players", []) if isinstance(data, dict) else data
        for player in players:
            if isinstance(player, dict):
                merge_profile_row(profiles, player, source)
    for path in sorted(STATIC_PLAYER_ROOT.glob("fide-*.json")):
        data = read_json(path)
        if isinstance(data, dict):
            merge_profile_row(profiles, data, "static-index")
    return profiles


def merge_profile_row(profiles: dict[str, PlayerProfile], row: dict[str, Any], source: str) -> None:
    fide_id = clean(row.get("fideID"))
    if not fide_id:
        return
    profile = profiles.setdefault(fide_id, PlayerProfile(fide_id=fide_id))
    profile.display_name = first_non_empty(clean(row.get("displayName")), profile.display_name)
    profile.chinese_name = first_non_empty(clean(row.get("chineseName")), profile.chinese_name)
    profile.name = first_non_empty(clean(row.get("name")), profile.name)
    profile.pinyin = first_non_empty(clean(row.get("pinyin")), clean(row.get("pinyinName")), profile.pinyin)
    profile.federation = first_non_empty(clean(row.get("federation")), profile.federation, "CHN")
    profile.birth_year = parse_int(row.get("birthYear")) or profile.birth_year
    profile.standard = parse_int(row.get("standard")) or parse_int(row.get("standardRating")) or profile.standard
    profile.rapid = parse_int(row.get("rapid")) or parse_int(row.get("rapidRating")) or profile.rapid
    profile.blitz = parse_int(row.get("blitz")) or parse_int(row.get("blitzRating")) or profile.blitz
    profile.sources.add(source)


def load_by_player_indexes() -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for path in sorted(BY_PLAYER_ROOT.glob("fide-*.json")):
        match = re.match(r"fide-(\d+)\.json$", path.name)
        if not match:
            continue
        rows[match.group(1)] = read_json(path)
    return rows


def load_static_events() -> list[StaticEvent]:
    rows: list[StaticEvent] = []
    for path in sorted(STATIC_PLAYER_ROOT.glob("fide-*.json")):
        data = read_json(path)
        fide_id = clean(data.get("fideID"))
        display_name = clean(data.get("displayName")) or clean(data.get("name")) or f"FIDE {fide_id}"
        for event in data.get("events", []):
            rows.append(
                StaticEvent(
                    fide_id=fide_id,
                    display_name=display_name,
                    source=clean(event.get("source")),
                    tournament_id=clean(event.get("tournamentID")),
                    name=clean(event.get("name")),
                    date=clean(event.get("date")),
                    pgn_path=clean(event.get("pgnPath")),
                    game_count=parse_int(event.get("gameCount")) or 0,
                    source_url=clean(event.get("sourceURL")),
                )
            )
    return rows


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
        body = decode_response(response.read())
    parser = FormParser(final_url)
    parser.feed(body)
    return {"base_url": final_url, "action_url": parser.action_url, "fields": parser.fields}


def open_url(request: urllib.request.Request):
    return open_response(request, timeout=90, retries=2)


def write_player_batch(path: pathlib.Path, players: list[PlayerProfile]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["fideID", "displayName", "chineseName", "name", "birthYear", "stage", "bestRating"],
        )
        writer.writeheader()
        for profile in players:
            writer.writerow(
                {
                    "fideID": profile.fide_id,
                    "displayName": profile.display_name,
                    "chineseName": profile.chinese_name,
                    "name": profile.name,
                    "birthYear": profile.birth_year or "",
                    "stage": profile.youth_stage,
                    "bestRating": profile.best_rating or "",
                }
            )


def load_scan_state() -> dict[str, Any]:
    path = AUDIT_ROOT / "chess-results-scan-state.json"
    if not path.exists():
        return {"schemaVersion": SCHEMA_VERSION, "players": {}}
    data = read_json(path)
    if not isinstance(data, dict):
        return {"schemaVersion": SCHEMA_VERSION, "players": {}}
    data.setdefault("schemaVersion", SCHEMA_VERSION)
    data.setdefault("players", {})
    return data


def update_scan_state(state: dict[str, Any], players: list[PlayerProfile]) -> None:
    state["updatedAt"] = now()
    rows = state.setdefault("players", {})
    for profile in players:
        row = rows.setdefault(profile.fide_id, {})
        row["lastPlannedAt"] = now()
        row["displayName"] = profile.display_name or profile.name or f"FIDE {profile.fide_id}"
        row["stage"] = profile.youth_stage


def write_audit_markdown(
    path: pathlib.Path,
    summary: dict[str, Any],
    source_coverage: dict[str, Any],
    player_coverage: dict[str, Any],
    missing_events: dict[str, Any],
) -> None:
    top_sources = source_coverage.get("sources", [])[:5]
    lines = [
        "# PGN Data Audit",
        "",
        f"Generated: {summary['generatedAt']}",
        "",
        "## Headline",
        "",
        f"- By-player games: {summary['headline']['byPlayerGames']}",
        f"- Registry players: {summary['headline']['registryPlayers']}",
        f"- Players with PGN: {summary['headline']['playersWithPgn']} ({player_coverage['totals']['coveragePercent']}%)",
        f"- Missing Chess-Results pairs: {summary['headline']['missingChessResultsPairs']}",
        f"- Targetable missing Chess-Results pairs: {summary['headline']['targetableMissingChessResultsPairs']}",
        "",
        "## Source Mix",
        "",
    ]
    for source in top_sources:
        lines.append(f"- {source['source']}: {source['games']} games ({source['percent']}%)")
    lines.extend(
        [
            "",
            "## Files",
            "",
            "- `source-coverage.json`: source, stage, and player source mix.",
            "- `missing-pgn-events.json`: known static events without usable PGN.",
            "- `player-coverage.json`: registry coverage and priority uncovered players.",
            "- `chess-results-targets.json`: known Chess-Results player/tournament pairs.",
            "- `chess-results-discovery.json`: written by `--discover-chess-results` runs.",
            "- `candidate-pgn-packages.json`: candidate Chess-Results PGN/event pages from discovery.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def player_payload(profile: PlayerProfile) -> dict[str, Any]:
    return without_empty(
        {
            "fideID": profile.fide_id,
            "displayName": profile.display_name or profile.name or f"FIDE {profile.fide_id}",
            "chineseName": profile.chinese_name,
            "name": profile.name,
            "pinyin": profile.pinyin,
            "birthYear": profile.birth_year,
            "stage": profile.youth_stage,
            "standard": profile.standard,
            "rapid": profile.rapid,
            "blitz": profile.blitz,
            "bestRating": profile.best_rating or None,
        }
    )


def read_json(path: pathlib.Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: pathlib.Path, data: Any) -> None:
    write_stable_json(path, data, ensure_ascii=False, indent=2, sort_keys=False)


def docs_path(public_path: str) -> pathlib.Path:
    cleaned = public_path.strip()
    if cleaned.startswith("data/"):
        return DOCS_DATA / cleaned[len("data/") :]
    return REPO_ROOT / cleaned


def public_data_path(path: pathlib.Path) -> str:
    try:
        relative = path.resolve().relative_to((REPO_ROOT / "docs").resolve())
        return str(relative).replace("\\", "/")
    except ValueError:
        return str(path)


def tournament_url(tournament_id: str) -> str:
    if is_numeric_tnr(tournament_id):
        return CHESS_RESULTS_TOURNAMENT_URL.format(tournament_id=tournament_id)
    return ""


def is_numeric_tnr(value: str) -> bool:
    return bool(re.fullmatch(r"\d+", clean(value)))


def count_pgn_games(text: str) -> int:
    return len(re.findall(r'^\[Event\s+"', text, flags=re.MULTILINE | re.IGNORECASE))


def decode_response(data: bytes) -> str:
    for encoding in ("utf-8", "gb18030", "iso-8859-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def clean(value: Any) -> str:
    return str(value or "").strip()


def clean_title(value: str) -> str:
    text = normalize_space(html.unescape(value))
    return re.sub(r"^Chess-Results Server Chess-results\.com\s*-\s*", "", text).strip()


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_text(value: str) -> str:
    return normalize_space(value).casefold()


def slug(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").casefold()).strip("-")


def first_non_empty(*values: str) -> str:
    for value in values:
        if value:
            return value
    return ""


def parse_int(value: Any) -> int | None:
    try:
        text = str(value or "").strip()
        return int(text) if text else None
    except ValueError:
        return None


def parse_datetime(value: Any) -> dt.datetime | None:
    text = clean(value)
    if not text:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed
    except ValueError:
        return None


def percent(value: int, total: int) -> float:
    return round((value / total) * 100, 2) if total else 0.0


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def stage_sort_key(stage: str) -> tuple[int, str]:
    if stage == "none":
        return (99, stage)
    match = re.match(r"U(\d+)", stage)
    return (int(match.group(1)) if match else 98, stage)


def without_empty(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if value not in ["", None, [], {}]}


if __name__ == "__main__":
    raise SystemExit(main())
