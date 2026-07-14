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

from apply_aliases_to_registry import sanitize_person_name
from stable_json import write_json


ROOT = pathlib.Path(__file__).resolve().parents[1]
GENERATED = ROOT / "data" / "generated" / "chess-results-event-details"
OUTPUT = ROOT / "docs" / "data" / "index" / "event-details"
BY_PLAYER = ROOT / "docs" / "data" / "index" / "by-player"
REGISTRY = ROOT / "docs" / "data" / "registry" / "players.json"
MAPPINGS = ROOT / "data" / "community" / "tournament-name-mappings.csv"
PUBLIC_REGIONS = (
    "北京", "上海", "天津", "重庆", "河北", "山西", "辽宁", "吉林", "黑龙江",
    "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南",
    "广东", "海南", "四川", "贵州", "云南", "陕西", "甘肃", "青海", "内蒙古",
    "广西", "西藏", "宁夏", "新疆", "香港", "澳门",
)


def clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def normalize_name(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", clean(value).casefold())


def round_number(value: Any) -> str:
    match = re.match(r"(\d+)", clean(value))
    return match.group(1) if match else clean(value)


# Legitimate Chess-Results results are built from 0/1/½/+/- plus an optional
# forfeit marker "K". Letters otherwise (e.g. a federation code "CHN" landing
# in the result column) or a digits-only player name (e.g. "0") indicate a
# column-shift misparse of the source table.
RESULT_LETTER_RE = re.compile(r"[A-JL-Za-jl-z]")
RESULT_CORE_RE = re.compile(r"[01½+\-]")


def round_anomalies(rounds: list[dict[str, Any]]) -> int:
    """Count structurally impossible pairings in per-round data."""
    count = 0
    for round_row in rounds or []:
        for pairing in round_row.get("pairings") or []:
            result = clean(pairing.get("result"))
            white = clean((pairing.get("white") or {}).get("name"))
            black = clean((pairing.get("black") or {}).get("name"))
            if result and (RESULT_LETTER_RE.search(result) or not RESULT_CORE_RE.search(result)):
                count += 1
            if (white and white.isdigit()) or (black and black.isdigit()):
                count += 1
    return count


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


def registry_index() -> dict[str, dict[str, Any]]:
    return {
        clean(player.get("fideID")): player
        for player in read_json(REGISTRY, [])
        if clean(player.get("fideID"))
    }


def apply_registry_identity(person: dict[str, Any], registry: dict[str, dict[str, Any]]) -> None:
    fide_id = clean(person.get("fideID"))
    authority = registry.get(fide_id)
    if not authority:
        # Unresolved source text is event evidence, not a player-authority row.
        if person.get("chineseName"):
            person["chineseName"] = sanitize_person_name(person.get("chineseName"))
        return
    mappings = (
        ("name", "name", "sourceName"),
        ("chineseName", "chineseName", "sourceChineseName"),
        ("federation", "federation", "sourceFederation"),
    )
    for target_key, registry_key, source_key in mappings:
        old = clean(person.get(target_key))
        new = clean(authority.get(registry_key))
        if old and old != new:
            person[source_key] = old
        if new:
            person[target_key] = new
        else:
            person.pop(target_key, None)
    person["displayName"] = clean(authority.get("displayName") or authority.get("name") or f"FIDE {fide_id}")
    pinyin = clean(authority.get("pinyin"))
    if pinyin:
        person["pinyin"] = pinyin
    else:
        person.pop("pinyin", None)


def minimize_public_location(person: dict[str, Any]) -> None:
    """Remove raw club/school affiliation from the public event projection."""
    raw_club = clean(person.pop("club", ""))
    explicit = clean(person.pop("province", "") or person.get("publicLocation"))
    if explicit:
        person["publicLocation"] = explicit
        return
    for region in PUBLIC_REGIONS:
        if region in raw_club:
            person["publicLocation"] = region
            return
    person.pop("publicLocation", None)


def prepare_public_person(
    person: dict[str, Any],
    names: dict[str, str],
    registry: dict[str, dict[str, Any]],
) -> None:
    attach_fide_id(person, names)
    apply_registry_identity(person, registry)
    minimize_public_location(person)


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
    registry = registry_index()
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
        for player in payload.get("players", []):
            prepare_public_person(player, names, registry)
        for standing in payload.get("standings", []):
            prepare_public_person(standing, names, registry)
        # Structural gate: misparsed rounds (result "CHN", digit-only names)
        # are never published. Final standings stay; rounds are withheld and
        # visibly marked pending re-verification.
        anomalies = round_anomalies(payload.get("rounds", []))
        if anomalies:
            payload["roundsPendingVerification"] = True
            payload["roundAnomalies"] = anomalies
            payload["withheldRounds"] = len(payload.get("rounds", []))
            payload["rounds"] = []
            totals["withheldRoundEvents"] = totals.get("withheldRoundEvents", 0) + 1
        for round_row in payload.get("rounds", []):
            round_id = round_number(round_row.get("round"))
            for pairing in round_row.get("pairings", []):
                prepare_public_person(pairing.get("white", {}), names, registry)
                prepare_public_person(pairing.get("black", {}), names, registry)
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
        write_json(output_path, payload, ensure_ascii=False, indent=2)
        manifest_events.append({
            "tournamentID": tid,
            "path": f"data/index/event-details/tnr{tid}.json",
            "displayName": payload["displayName"],
            "roundCount": payload.get("roundCount") or len(payload.get("rounds", [])),
            "standingCount": len(payload.get("standings", [])),
            **({"roundsPendingVerification": True} if payload.get("roundsPendingVerification") else {}),
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
    write_json(OUTPUT / "manifest.json", manifest, ensure_ascii=False, indent=2)
    print(json.dumps(totals, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
