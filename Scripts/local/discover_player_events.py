#!/usr/bin/env python3
"""Discover recent tournament IDs by searching a bounded set of FIDE IDs.

Raw source responses and FIDE-to-event links remain private. Only tournament
IDs selected later by the normal event collector can enter a release.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
import pathlib
import re
import sys
import time
from typing import Any, Callable

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from crawl_player_events import load_china_fide_ids, search_player  # noqa: E402
from event_targeting import DISCOVERY_POOL, target_overrides  # noqa: E402
from run_manager import atomic_json  # noqa: E402

DISCOVERY_STATE = DISCOVERY_POOL.with_name("player-event-discovery-state.json")
FIDE_RE = re.compile(r"^\d{5,10}$")


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def load(path: pathlib.Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def select_players(explicit: list[str], limit: int, state: dict[str, Any]) -> list[str]:
    if explicit:
        values = explicit
    else:
        checked = state.get("players") or {}
        values = sorted(
            load_china_fide_ids(),
            key=lambda fide_id: ((checked.get(fide_id) or {}).get("checkedAt") or "", fide_id),
        )
    result: list[str] = []
    for value in values:
        fide_id = re.sub(r"\D", "", value)
        if FIDE_RE.fullmatch(fide_id) and fide_id not in result:
            result.append(fide_id)
    return result[:limit]


def discover(
    players: list[str],
    *,
    private_root: pathlib.Path,
    latest_per_player: int,
    delay: float,
    search: Callable[[str, list[str] | None], list[dict[str, Any]]] = search_player,
    pool_path: pathlib.Path = DISCOVERY_POOL,
    state_path: pathlib.Path = DISCOVERY_STATE,
) -> dict[str, Any]:
    pool = load(pool_path, {"schemaVersion": 1, "candidates": {}})
    state = load(state_path, {"schemaVersion": 1, "players": {}})
    candidates = pool.setdefault("candidates", {})
    player_state = state.setdefault("players", {})
    overrides = target_overrides()
    raw_root = private_root / "raw" / "chess-results" / "player-search"
    raw_root.mkdir(parents=True, exist_ok=True)
    discovered: set[str] = set()
    failures: list[dict[str, str]] = []

    for index, fide_id in enumerate(players):
        sink: list[str] = []
        try:
            rows = search(fide_id, sink)
            raw = (sink[0] if sink else "").encode("utf-8")
            raw_path = raw_root / f"fide{fide_id}.html.gz"
            with gzip.open(raw_path, "wb") as handle:
                handle.write(raw)
            os.chmod(raw_path, 0o600)
            chosen = rows[:latest_per_player]
            stamp = now_iso()
            player_state[fide_id] = {
                "checkedAt": stamp,
                "resultCount": len(rows),
                "selectedCount": len(chosen),
                "rawSha256": hashlib.sha256(raw).hexdigest(),
            }
            for rank, row in enumerate(chosen):
                tournament_id = re.sub(r"\D", "", str(row.get("tnrid") or ""))
                if not tournament_id:
                    continue
                override = overrides.get(tournament_id) or {}
                status = "suppressed" if override.get("action") in {"exclude", "aggregate", "duplicate"} else "pending"
                current = candidates.setdefault(tournament_id, {
                    "tournamentID": tournament_id,
                    "firstDiscoveredAt": stamp,
                    "fideIDs": [],
                })
                current.update({
                    "eventName": str(row.get("tournament") or current.get("eventName") or f"tnr{tournament_id}")[:180],
                    "date": row.get("end_date") or current.get("date"),
                    "rounds": int(row["rounds"]) if str(row.get("rounds") or "").isdigit() else current.get("rounds"),
                    "participants": int(row["participants"]) if str(row.get("participants") or "").isdigit() else current.get("participants"),
                    "lastDiscoveredAt": stamp,
                    "discoveredBy": ["fide-player-search"],
                    "priorityScore": max(int(current.get("priorityScore") or 0), 210 - rank * 10),
                    "nextAction": "capture-event",
                    "status": status,
                })
                if fide_id not in current["fideIDs"]:
                    current["fideIDs"].append(fide_id)
                if override.get("chinese_name"):
                    current["eventName"] = override["chinese_name"]
                if status == "suppressed":
                    current["suppressionReason"] = override.get("reason") or override.get("action")
                else:
                    discovered.add(tournament_id)
        except Exception as exc:  # noqa: BLE001 - isolate one player lookup
            failures.append({"fideID": fide_id, "error": str(exc)[:300]})
            player_state[fide_id] = {"checkedAt": now_iso(), "error": str(exc)[:300]}
        if delay and index + 1 < len(players):
            time.sleep(delay)

    stamp = now_iso()
    pool["updatedAt"] = stamp
    pool["privacy"] = "maintainer-local; FIDE IDs and discovery provenance are never published"
    state["updatedAt"] = stamp
    atomic_json(pool_path, pool)
    atomic_json(state_path, state)
    return {
        "playersChecked": len(players),
        "candidatesFound": len(discovered),
        "candidateTNRs": sorted(discovered),
        "failures": failures,
        "poolSize": sum(
            1 for item in candidates.values()
            if isinstance(item, dict) and item.get("status", "pending") == "pending"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("players", nargs="*")
    parser.add_argument("--private-root", required=True, type=pathlib.Path)
    parser.add_argument("--max-players", type=int, default=10)
    parser.add_argument("--latest-per-player", type=int, default=5)
    parser.add_argument("--delay", type=float, default=1.0)
    args = parser.parse_args()
    if not 1 <= args.max_players <= 50 or not 1 <= args.latest_per_player <= 20:
        parser.error("max-players must be 1..50 and latest-per-player must be 1..20")
    state = load(DISCOVERY_STATE, {"players": {}})
    players = select_players(args.players, args.max_players, state)
    if not players:
        parser.error("没有可查询的 FIDE ID")
    result = discover(
        players,
        private_root=args.private_root,
        latest_per_player=args.latest_per_player,
        delay=max(0.0, min(args.delay, 5.0)),
    )
    print(json.dumps(result, ensure_ascii=False))
    return 4 if result["failures"] and result["candidatesFound"] else 1 if result["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
