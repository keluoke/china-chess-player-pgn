#!/usr/bin/env python3
"""Build public domestic-event standings/round payloads and PGN cross-links."""

from __future__ import annotations

import csv
import datetime as dt
import json
import pathlib
import re
from collections import defaultdict
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
GENERATED = ROOT / "data" / "generated" / "chess-results-event-details"
OUTPUT = ROOT / "docs" / "data" / "index" / "event-details"
BY_PLAYER = ROOT / "docs" / "data" / "index" / "by-player"
REGISTRY = ROOT / "docs" / "data" / "registry" / "players.json"
MAPPINGS = ROOT / "data" / "community" / "tournament-name-mappings.csv"


def clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def normalize_name(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", clean(value).casefold())


def round_number(value: Any) -> str:
    match = re.match(r"(\d+)", clean(value))
    return match.group(1) if match else clean(value)


def read_json(path: pathlib.Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def mapping_index() -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    if not MAPPINGS.exists():
        return result
    with MAPPINGS.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if clean(row.get("source")).casefold() != "chess-results":
                continue
            tid = clean(row.get("tournament_id"))
            if tid:
                result[tid] = {key: clean(value) for key, value in row.items()}
    return result


def name_index() -> dict[str, str]:
    hits: dict[str, set[str]] = defaultdict(set)
    for player in read_json(REGISTRY, []):
        fide_id = clean(player.get("fideID"))
        if not fide_id:
            continue
        for value in [player.get("displayName"), player.get("name"), player.get("chineseName"), player.get("pinyin"), *(player.get("aliases") or [])]:
            key = normalize_name(value)
            if key:
                hits[key].add(fide_id)
    return {key: next(iter(values)) for key, values in hits.items() if len(values) == 1}


def event_game_lookup() -> dict[tuple[str, str, tuple[str, str]], dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    players = read_json(BY_PLAYER / "players.json", [])
    for player in players:
        fide_id = clean(player.get("fideID"))
        detail_path = player.get("playerIndexPath")
        if not fide_id or not detail_path:
            continue
        detail = read_json(ROOT / "docs" / clean(detail_path), None)
        if detail is None:
            detail = read_json(BY_PLAYER / f"fide-{fide_id}.json", {})
        for game in detail.get("games", []):
            tid = clean(game.get("tournamentID"))
            path = clean(game.get("sourcePgnPath"))
            game_id = clean(game.get("id") or game.get("sha256"))
            if not tid or not path or not game_id:
                continue
            record = grouped.setdefault(game_id, {
                "id": game_id,
                "tournamentID": tid,
                "round": round_number(game.get("round")),
                "white": clean(game.get("white")),
                "black": clean(game.get("black")),
                "result": clean(game.get("result")),
                "pgnPath": path,
                "playerFideIDs": set(),
            })
            record["playerFideIDs"].add(fide_id)
    lookup: dict[tuple[str, str, tuple[str, str]], dict[str, Any]] = {}
    for record in grouped.values():
        record["playerFideIDs"] = sorted(record["playerFideIDs"])
        names = tuple(sorted([normalize_name(record["white"]), normalize_name(record["black"])]))
        lookup[(record["tournamentID"], record["round"], names)] = record
    return lookup


def attach_fide_id(side: dict[str, Any], names: dict[str, str]) -> None:
    if side.get("fideID"):
        return
    for value in (side.get("chineseName"), side.get("name")):
        hit = names.get(normalize_name(value))
        if hit:
            side["fideID"] = hit
            return


def build() -> tuple[list[dict[str, Any]], dict[str, int]]:
    mappings = mapping_index()
    names = name_index()
    games = event_game_lookup()
    manifest_events: list[dict[str, Any]] = []
    totals = {"events": 0, "standings": 0, "rounds": 0, "pairings": 0, "pairingsWithLocalPGN": 0}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for source_path in sorted(GENERATED.glob("tnr*.json")):
        payload = read_json(source_path, {})
        tid = clean(payload.get("tournamentID"))
        if not tid:
            continue
        mapping = mappings.get(tid, {})
        payload["canonicalEventID"] = mapping.get("canonical_event_id") or None
        payload["chineseName"] = mapping.get("chinese_name") or None
        payload["displayName"] = mapping.get("chinese_name") or payload.get("sourceName") or f"tnr{tid}"
        for standing in payload.get("standings", []):
            attach_fide_id(standing, names)
        for round_row in payload.get("rounds", []):
            round_id = round_number(round_row.get("round"))
            for pairing in round_row.get("pairings", []):
                attach_fide_id(pairing.get("white", {}), names)
                attach_fide_id(pairing.get("black", {}), names)
                key = (tid, round_id, tuple(sorted([
                    normalize_name(pairing.get("white", {}).get("name")),
                    normalize_name(pairing.get("black", {}).get("name")),
                ])))
                local_game = games.get(key)
                if local_game:
                    pairing["localGame"] = local_game
                    totals["pairingsWithLocalPGN"] += 1
                totals["pairings"] += 1
        output_path = OUTPUT / f"tnr{tid}.json"
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        manifest_events.append({
            "tournamentID": tid,
            "path": f"data/index/event-details/tnr{tid}.json",
            "displayName": payload["displayName"],
            "roundCount": payload.get("roundCount") or len(payload.get("rounds", [])),
            "standingCount": len(payload.get("standings", [])),
        })
        totals["events"] += 1
        totals["standings"] += len(payload.get("standings", []))
        totals["rounds"] += len(payload.get("rounds", []))
    return manifest_events, totals


def main() -> int:
    events, totals = build()
    manifest = {
        "schemaVersion": 1,
        "generatedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "totals": totals,
        "events": events,
    }
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(totals, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
