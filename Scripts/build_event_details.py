#!/usr/bin/env python3
"""Build public domestic-event standings/round payloads and PGN cross-links."""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import pathlib
import re
from collections import defaultdict
from typing import Any

from apply_aliases_to_registry import sanitize_person_name
from snapshot_context import stamp
from stable_json import write_json
from fetch_event_pgn import parse_headers, split_games


ROOT = pathlib.Path(__file__).resolve().parents[1]
GENERATED = ROOT / "data" / "generated" / "chess-results-event-details"
COMPLETENESS = ROOT / "data" / "generated" / "event-completeness-report.json"
OUTPUT = ROOT / "docs" / "data" / "index" / "event-details"
BY_PLAYER = ROOT / "docs" / "data" / "index" / "by-player"
EVENT_PGN = ROOT / "data" / "generated" / "chess-results-event-pgn"
EVENT_PGN_RECEIPT = ROOT / "data" / "generated" / "r2-object-receipts" / "events--chess-results.json"
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
    # Raw source text never survives into the public projection: the registry
    # value replaces it outright, and the raw observation stays in the private
    # capture layer for maintainer audit (de-sourcing contract, AGENTS.md).
    for target_key, registry_key in (("name", "name"), ("chineseName", "chineseName"), ("federation", "federation")):
        new = clean(authority.get(registry_key))
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


def event_archive_game_lookup() -> dict[tuple[str, str, str], dict[str, Any]]:
    """Verified public event archive games keyed by tournament, round, board.

    The complete event archive is independent of FIDE identity, so games
    involving two no-FIDE players remain directly replayable.  An R2 HEAD
    receipt with a matching local SHA is required before a public URL leaves
    this projection.
    """
    receipt = read_json(EVENT_PGN_RECEIPT, {})
    receipts = {
        clean(item.get("key")): item
        for item in receipt.get("objects", []) or []
        if clean(item.get("key")) and clean(item.get("sha256"))
    }
    lookup: dict[tuple[str, str, str], dict[str, Any]] = {}
    for path in sorted(EVENT_PGN.glob("tnr*.pgn")):
        tid = path.stem.removeprefix("tnr")
        key = f"events/chess-results/{path.name}"
        item = receipts.get(key)
        if not item or hashlib.sha256(path.read_bytes()).hexdigest() != clean(item.get("sha256")):
            continue
        if not clean(item.get("publicURL")):
            continue
        public_url = f"./api/event-pgn?tnr={tid}&sha={clean(item.get('sha256'))[:16]}"
        text = path.read_text(encoding="utf-8", errors="replace")
        for index, game in enumerate(split_games(text)):
            headers = parse_headers(game)
            round_id = round_number(headers.get("Round"))
            board = clean(headers.get("Board"))
            if not round_id or not board:
                continue
            digest = hashlib.sha256(game.strip().encode("utf-8")).hexdigest()
            lookup[(tid, round_id, board)] = {
                "id": f"game-{digest[:20]}",
                "tournamentID": tid,
                "round": round_id,
                "board": board,
                "white": clean(headers.get("White")),
                "black": clean(headers.get("Black")),
                "result": clean(headers.get("Result")),
                "pgnPath": public_url,
                "gameIndex": index,
                "sha256": digest,
                "playerFideIDs": [],
            }
    return lookup


def attach_fide_id(side: dict[str, Any], names: dict[str, str]) -> None:
    if side.get("fideID"):
        return
    for value in (side.get("chineseName"), side.get("name")):
        hit = names.get(normalize_name(value))
        if hit:
            side["fideID"] = hit
            return


# De-sourcing contract: public event details never carry source identity,
# external links or capture evidence. The private capture layer keeps them.
_PRIVATE_TOP_KEYS = (
    "source", "sourceName", "sourceRefs", "evidence", "sourceSnapshots",
    "releasePolicy", "captureStatus", "captureErrorCode", "failedPage",
    "roundCandidates", "parserVersion", "coverageScope",
)
_PRIVATE_PERSON_KEYS = ("sourceName", "sourceChineseName", "sourceFederation", "club", "school")


def strip_private_fields(payload: dict[str, Any]) -> None:
    for key in _PRIVATE_TOP_KEYS:
        payload.pop(key, None)
    for round_row in payload.get("rounds", []) or []:
        round_row.pop("sourceURL", None)
        for pairing in round_row.get("pairings", []) or []:
            pairing.pop("pgnURL", None)
            for side_key in ("white", "black"):
                side = pairing.get(side_key)
                if isinstance(side, dict):
                    for key in _PRIVATE_PERSON_KEYS:
                        side.pop(key, None)
    for collection in ("players", "standings"):
        for person in payload.get(collection, []) or []:
            for key in _PRIVATE_PERSON_KEYS:
                person.pop(key, None)


def completeness_index() -> dict[str, dict[str, Any]]:
    report = read_json(COMPLETENESS, {})
    return {
        clean(item.get("tournamentID")): item
        for item in report.get("events", []) or []
        if clean(item.get("tournamentID"))
    }


def public_completeness(report: dict[str, Any]) -> dict[str, Any]:
    """User-facing completeness: understandable statuses, no internal codes."""
    counts = report.get("counts") or {}
    return {
        "resultsStatus": report.get("resultsStatus"),
        "pgnAvailability": report.get("pgnAvailability"),
        "pgnSourceStatus": report.get("pgnSourceStatus"),
        "pgnLastAttemptedAt": report.get("pgnLastAttemptedAt"),
        "pgnCoverageScope": report.get("pgnCoverageScope"),
        "archiveStatus": report.get("archiveStatus"),
        "pgnIngestStatus": report.get("pgnIngestStatus"),
        "pgnArchiveSources": report.get("pgnArchiveSources") or [],
        "nonByePairings": counts.get("nonByePairings"),
        "advertisedPGN": counts.get("advertisedPGN"),
        "lichessBroadcastGames": counts.get("lichessBroadcastGames"),
        "matchedPairings": counts.get("matchedPairings"),
        "advertisedCoverage": report.get("advertisedCoverage"),
        "allBoardCoverage": report.get("allBoardCoverage"),
        "eventComplete": bool(report.get("eventComplete")),
        "playableComplete": bool(report.get("playableComplete")),
    }


def build() -> tuple[list[dict[str, Any]], dict[str, int]]:
    mappings = mapping_index()
    names = name_index()
    registry = registry_index()
    games = event_game_lookup()
    archive_games = event_archive_game_lookup()
    completeness = completeness_index()
    manifest_events: list[dict[str, Any]] = []
    totals = {
        "events": 0, "standings": 0, "rounds": 0, "pairings": 0,
        "pairingsWithLocalPGN": 0, "resultsComplete": 0, "eventComplete": 0,
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for source_path in sorted(GENERATED.glob("tnr*.json")):
        payload = read_json(source_path, {})
        tid = clean(payload.get("tournamentID"))
        if not tid:
            continue
        report = completeness.get(tid, {})
        if not report.get("publishable"):
            # Only results-complete events enter the public projection; partial
            # captures stay quarantined in the private layer (plan §5.2).
            continue
        mapping = mappings.get(tid, {})
        payload["canonicalEventID"] = mapping.get("canonical_event_id") or None
        payload["chineseName"] = mapping.get("chinese_name") or None
        payload["title"] = clean(payload.get("sourceName")) or None
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
                archive_game = archive_games.get((tid, round_id, clean(pairing.get("board"))))
                if archive_game:
                    pairing_names = tuple(sorted([
                        normalize_name(pairing.get("white", {}).get("name")),
                        normalize_name(pairing.get("black", {}).get("name")),
                    ]))
                    archive_names = tuple(sorted([
                        normalize_name(archive_game.get("white")),
                        normalize_name(archive_game.get("black")),
                    ]))
                    if not any(archive_names) or archive_names == pairing_names:
                        archive_game = dict(archive_game)
                        archive_game["playerFideIDs"] = sorted({
                            clean(pairing.get(side, {}).get("fideID"))
                            for side in ("white", "black")
                            if clean(pairing.get(side, {}).get("fideID"))
                        })
                        local_game = archive_game
                if local_game:
                    pairing["localGame"] = local_game
                    totals["pairingsWithLocalPGN"] += 1
                totals["pairings"] += 1
        if report:
            payload["completeness"] = public_completeness(report)
            if report.get("resultsStatus") == "results-complete":
                totals["resultsComplete"] += 1
            if report.get("eventComplete"):
                totals["eventComplete"] += 1
        strip_private_fields(payload)
        output_path = OUTPUT / f"tnr{tid}.json"
        write_json(output_path, payload, ensure_ascii=False, indent=2)
        manifest_events.append({
            "tournamentID": tid,
            "path": f"data/index/event-details/tnr{tid}.json",
            "displayName": payload["displayName"],
            "dateBegin": payload.get("dateBegin"),
            "dateEnd": payload.get("dateEnd"),
            "roundCount": payload.get("roundCount") or len(payload.get("rounds", [])),
            "standingCount": len(payload.get("standings", [])),
            **({"roundsPendingVerification": True} if payload.get("roundsPendingVerification") else {}),
            **({"pgnAvailability": report.get("pgnAvailability")} if report else {}),
            **({"pgnSourceStatus": report.get("pgnSourceStatus")} if report else {}),
            **({"eventComplete": True} if report.get("eventComplete") else {}),
            **({"playableComplete": True} if report.get("playableComplete") else {}),
        })
        totals["events"] += 1
        totals["standings"] += len(payload.get("standings", []))
        totals["rounds"] += len(payload.get("rounds", []))
    return manifest_events, totals


def main() -> int:
    if not COMPLETENESS.exists():
        raise SystemExit(
            "event-completeness-report.json missing — run "
            "Scripts/build_completeness_report.py first (publication gate)."
        )
    # Environments without the full private capture layer (e.g. CI) cannot
    # regenerate event projections — but they CAN legitimately re-derive the
    # manifest from the committed public event files, which are the input of
    # record there. This "reproject" keeps every manifest inside one snapshot
    # without ever mixing unknown-state artifacts (review §3.1).
    report_events = len((read_json(COMPLETENESS, {}) or {}).get("events") or [])
    visible = len(list(GENERATED.glob("tnr*.json")))
    if report_events and visible < report_events:
        manifest_events = []
        totals = {"events": 0, "standings": 0, "rounds": 0, "reprojected": True}
        for path in sorted(OUTPUT.glob("tnr*.json")):
            payload = read_json(path, {})
            tid = clean(payload.get("tournamentID"))
            if not tid:
                continue
            completeness_block = payload.get("completeness") or {}
            manifest_events.append({
                "tournamentID": tid,
                "path": f"data/index/event-details/tnr{tid}.json",
                "displayName": payload.get("displayName"),
                "dateBegin": payload.get("dateBegin"),
                "dateEnd": payload.get("dateEnd"),
                "roundCount": payload.get("roundCount") or len(payload.get("rounds", [])),
                "standingCount": len(payload.get("standings", [])),
                **({"roundsPendingVerification": True} if payload.get("roundsPendingVerification") else {}),
                **({"pgnAvailability": completeness_block.get("pgnAvailability")} if completeness_block.get("pgnAvailability") else {}),
                **({"eventComplete": True} if completeness_block.get("eventComplete") else {}),
            })
            totals["events"] += 1
            totals["standings"] += len(payload.get("standings", []))
            totals["rounds"] += len(payload.get("rounds", []))
        write_json(OUTPUT / "manifest.json", stamp({
            "schemaVersion": 2,
            "buildMode": "reprojected-from-committed",
            "totals": totals,
            "events": manifest_events,
        }), ensure_ascii=False, indent=2)
        print(json.dumps({"reprojected": totals["events"], "visibleDetails": visible,
                          "reportEvents": report_events}, ensure_ascii=False))
        return 0
    events, totals = build()
    # Prune projections for events that fell out of the publishable set so a
    # stale file can never keep serving a withdrawn event.
    published = {f"tnr{item['tournamentID']}.json" for item in events}
    for path in OUTPUT.glob("tnr*.json"):
        if path.name not in published:
            path.unlink()
    manifest = stamp({
        "schemaVersion": 2,
        "totals": totals,
        "events": events,
    })
    write_json(OUTPUT / "manifest.json", manifest, ensure_ascii=False, indent=2)
    print(json.dumps(totals, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
