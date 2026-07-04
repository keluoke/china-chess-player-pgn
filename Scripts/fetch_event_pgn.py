#!/usr/bin/env python3
"""Download full-tournament PGN from Chess-Results and split it per player.

For each tournament ID this fetches the complete PGN in ONE request
(PartieSuche with empty FIDE ID + dbkey), then assigns each game to the
players on the board via WhiteFideId/BlackFideId headers, falling back to
alias name matching (player-aliases.csv + the committed registry). Output
follows the existing static layout so sync_static_pgn.py and
build_static_player_pgn.py pick everything up unchanged:

    docs/data/pgn/chess-results/tnr<ID>/fide-<FIDEID>-<ID>.pgn

  python3 fetch_event_pgn.py --category li-chengzhi        # from sources CSV
  python3 fetch_event_pgn.py --tournament-id 1356505
"""

from __future__ import annotations

import argparse
import csv
import json
import pathlib
import re
import sys
import time
from collections import defaultdict
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from build_static_player_pgn import clean  # mojibake-aware header cleaner  # noqa: E402
from sync_chess_results_starting_rank_aliases import (  # noqa: E402
    fetch_text,
    is_master_event,
    parse_html,
)
from sync_static_pgn import (  # noqa: E402
    REPO_ROOT,
    STATIC_PGN_ROOT,
    count_pgn_games,
    download_chess_results_pgn,
)


def event_title(tournament_id: str) -> str:
    url = f"https://chess-results.com/tnr{tournament_id}.aspx?lan=1&art=14"
    try:
        text, _final = fetch_text(url, timeout=25, retries=1)
    except Exception:  # noqa: BLE001
        return ""
    return parse_html(text).title

SOURCES_CSV = REPO_ROOT / "data" / "manual" / "chess-results-starting-rank-sources.csv"
ALIAS_CSV = REPO_ROOT / "data" / "manual" / "player-aliases.csv"
REGISTRY_PLAYERS = REPO_ROOT / "docs" / "data" / "registry" / "players.json"


def normalize_name(value: str) -> str:
    return re.sub(r"[\s,.\-_'’·()（）]+", "", clean(value)).casefold()


def split_games(pgn: str) -> list[str]:
    normalized = pgn.replace("\r\n", "\n")
    starts = [m.start() for m in re.finditer(r'^\[Event\s+"', normalized, flags=re.MULTILINE)]
    games = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(normalized)
        chunk = normalized[start:end].strip()
        if chunk:
            games.append(chunk)
    return games


def parse_headers(game: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for match in re.finditer(r'^\[([A-Za-z0-9_]+)\s+"(.*)"\]', game, flags=re.MULTILINE):
        headers[match.group(1)] = clean(match.group(2))
    return headers


def load_name_index() -> dict[str, str]:
    """normalized alias -> fide_id, from aliases CSV plus registry."""
    index: dict[str, str] = {}
    ambiguous: set[str] = set()

    def add(alias: str, fide_id: str) -> None:
        key = normalize_name(alias)
        if not key or not fide_id:
            return
        current = index.get(key)
        if current is None:
            index[key] = fide_id
        elif current != fide_id:
            ambiguous.add(key)

    if REGISTRY_PLAYERS.exists():
        for player in json.loads(REGISTRY_PLAYERS.read_text(encoding="utf-8")):
            fide_id = str(player.get("fideID") or "")
            for alias in player.get("aliases") or []:
                add(str(alias), fide_id)
    if ALIAS_CSV.exists():
        with ALIAS_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                fide_id = clean(row.get("fide_id"))
                for alias in (row.get("aliases") or "").split("|"):
                    add(alias, fide_id)
                add(row.get("chinese_name") or "", fide_id)
    for key in ambiguous:
        index.pop(key, None)
    return index


def fide_id_for(headers: dict[str, str], side: str, names: dict[str, str]) -> str:
    for tag in (f"{side}FideId", f"{side}FideID", f"{side}FIDEID"):
        digits = re.sub(r"\D", "", headers.get(tag, ""))
        if digits and digits != "0":
            return digits
    name = headers.get(side, "")
    if name:
        hit = names.get(normalize_name(name))
        if hit:
            return hit
        parts = [p for p in re.split(r"[,\s]+", name) if p]
        if len(parts) >= 2:
            return names.get(normalize_name(" ".join(reversed(parts)))) or ""
    return ""


def tournament_ids_from_sources(path: pathlib.Path, category: str) -> list[str]:
    ids: list[str] = []
    if not path.exists():
        return ids
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if category and clean(row.get("category")) != category:
                continue
            tid = re.sub(r"\D", "", clean(row.get("tournament_id")))
            if tid:
                ids.append(tid)
    return list(dict.fromkeys(ids))


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch full-event PGN from Chess-Results and split per player.")
    parser.add_argument("--sources", type=pathlib.Path, default=SOURCES_CSV)
    parser.add_argument("--category", default="", help="only sources rows with this category")
    parser.add_argument("--tournament-id", action="append", default=[], help="extra tournament ID; repeatable")
    parser.add_argument("--neighbor-window", type=int, default=0, help="also try IDs within +/-N of every source ID")
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--max-events", type=int, default=0, help="0 means no limit")
    parser.add_argument("--overwrite", action="store_true", help="refetch tournaments that already have files")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    ids = tournament_ids_from_sources(args.sources, args.category)
    ids.extend(re.sub(r"\D", "", t) for t in args.tournament_id)
    ids = [t for t in dict.fromkeys(ids) if t]
    if args.neighbor_window > 0:
        seeds = list(ids)
        for seed in seeds:
            base = int(seed)
            for tid in range(base - args.neighbor_window, base + args.neighbor_window + 1):
                if str(tid) not in ids:
                    ids.append(str(tid))

    names = load_name_index()
    stats = {
        "events": 0, "eventsWithGames": 0, "eventsSkippedExisting": 0, "games": 0,
        "gamesAssigned": 0, "gamesUnassigned": 0, "playersWritten": 0, "errors": [],
    }

    for tid in ids:
        if args.max_events and stats["events"] >= args.max_events:
            break
        out_dir = STATIC_PGN_ROOT / "chess-results" / f"tnr{tid}"
        if not args.overwrite and out_dir.exists() and any(out_dir.glob("*.pgn")):
            stats["eventsSkippedExisting"] += 1
            continue
        # Neighbor-probed IDs may belong to unrelated events; only ingest
        # tournaments whose title matches the accepted event families.
        title = event_title(tid)
        if not title or not is_master_event(title):
            stats.setdefault("skippedTitleMismatch", 0)
            stats["skippedTitleMismatch"] += 1
            time.sleep(args.delay / 2)
            continue
        stats["events"] += 1
        try:
            pgn = download_chess_results_pgn("", tid)
        except Exception as error:  # noqa: BLE001 - keep batch going
            stats["errors"].append(f"tnr{tid}: {error}")
            continue
        games = split_games(pgn)
        if not games or count_pgn_games(pgn) == 0:
            time.sleep(args.delay)
            continue
        stats["eventsWithGames"] += 1
        stats["games"] += len(games)

        per_player: dict[str, list[str]] = defaultdict(list)
        for game in games:
            headers = parse_headers(game)
            assigned = False
            for side in ("White", "Black"):
                fide_id = fide_id_for(headers, side, names)
                if fide_id:
                    per_player[fide_id].append(game)
                    assigned = True
            if assigned:
                stats["gamesAssigned"] += 1
            else:
                stats["gamesUnassigned"] += 1

        if not args.dry_run and per_player:
            out_dir.mkdir(parents=True, exist_ok=True)
            for fide_id, player_games in per_player.items():
                path = out_dir / f"fide-{fide_id}-{tid}.pgn"
                path.write_text("\n\n".join(player_games) + "\n", encoding="utf-8")
                stats["playersWritten"] += 1
        print(f"tnr{tid}: {len(games)} games, {len(per_player)} players", file=sys.stderr)
        time.sleep(args.delay)

    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
