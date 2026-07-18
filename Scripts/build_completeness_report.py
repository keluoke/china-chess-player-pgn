#!/usr/bin/env python3
"""Build the per-event CompletenessReport (multi-dimensional gates).

Second-review (2026-07-18) contract:

- Event PGN archives (``data/generated/chess-results-event-pgn``) are a
  first-class input: an event whose archive already contains every played
  game must be recognized offline and never re-queued for source access.
- Matching uses the pairing natural key ``round + board`` (playerNo-anchored
  through the roster); names are fallback evidence only and fallback matches
  carry an explicit confidence marker.
- PGN denominators exclude byes AND forfeits/cancellations
  (``playedGameExpected``); results completeness still accepts legitimate
  forfeit results.
- Gates carry independent expected values; when an expected value cannot be
  established the dimension is ``unknown`` — never silently ``complete``.
- The supplement queue holds actionable tasks only; bare ``not-published``
  events stay in coverage statistics. Maintainer leads live in a private
  ``pgn-leads.csv`` outside the repository.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
from collections import defaultdict
from typing import Any

from stable_json import write_json

try:
    from source_policy import local_state_root
except Exception:  # pragma: no cover - CI without local policy module extras
    def local_state_root() -> pathlib.Path:
        return pathlib.Path.home() / ".china-chess-player-pgn"

ROOT = pathlib.Path(__file__).resolve().parents[1]
DETAILS = ROOT / "data" / "generated" / "chess-results-event-details"
EVENT_PGN = ROOT / "data" / "generated" / "chess-results-event-pgn"
BY_PLAYER = ROOT / "docs" / "data" / "index" / "by-player"
OUTPUT = ROOT / "data" / "generated" / "event-completeness-report.json"
QUEUE_OUTPUT = ROOT / "data" / "generated" / "pgn-supplement-queue.json"
COLLECTION_STATUS = ROOT / "data" / "generated" / "pgn-collection-status.json"
EVENT_PGN_RECEIPT = ROOT / "data" / "generated" / "r2-object-receipts" / "events--chess-results.json"
# Two lead registries: the repo copy holds sanitized, reviewable leads
# (event id + coverage claim only); URLs/private hints stay in the
# maintainer-local file outside the repository.
PGN_LEADS_PRIVATE = local_state_root() / "pgn-leads.csv"
PGN_LEADS_PUBLIC = ROOT / "data" / "manual" / "pgn-leads.csv"

# Team-format sections report match points ("2", "2½"); letters (e.g. a
# federation code shifted into the result column) still mark a row invalid.
RESULT_CORE_RE = re.compile(r"[0-9½+\-]")
RESULT_LETTER_RE = re.compile(r"[A-JL-Za-jl-z]")
# Chess-Results forfeit renderings: "+ - -", "- - +", "- - -" (double default).
FORFEIT_RE = re.compile(r"^[+\-]\s*-\s*[+\-]$")


def clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def normalize_name(value: Any) -> str:
    return re.sub(r"[^0-9a-z一-鿿]", "", clean(value).casefold())


def round_number(value: Any) -> str:
    match = re.match(r"(\d+)", clean(value))
    return match.group(1) if match else clean(value)


def read_json(path: pathlib.Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def verified_event_archives() -> set[str]:
    receipt = read_json(EVENT_PGN_RECEIPT, {})
    verified: set[str] = set()
    for item in receipt.get("objects", []) or []:
        key = clean(item.get("key"))
        match = re.fullmatch(r"events/chess-results/tnr(\d+)\.pgn", key)
        if not match:
            continue
        path = EVENT_PGN / f"tnr{match.group(1)}.pgn"
        if not path.is_file():
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest == clean(item.get("sha256")):
            verified.add(match.group(1))
    return verified


def is_bye(pairing: dict[str, Any]) -> bool:
    white = pairing.get("white") or {}
    black = pairing.get("black") or {}
    return not clean(white.get("playerNo")) or not clean(black.get("playerNo"))


def is_forfeit(result: Any) -> bool:
    return bool(FORFEIT_RE.match(clean(result)))


def played_game_expected(pairing: dict[str, Any]) -> bool | None:
    """Did this pairing produce an over-the-board game?

    False for byes and forfeits/cancellations; None (unknown) when the result
    is empty — unknown rows never enter the PGN-expected denominator."""
    if is_bye(pairing):
        return False
    result = clean(pairing.get("result"))
    if not result:
        return None
    if is_forfeit(result):
        return False
    return True


def advertised_pgn(pairing: dict[str, Any]) -> bool:
    return bool(pairing.get("hasPGN")) or bool(clean(pairing.get("pgnURL")))


def valid_result(pairing: dict[str, Any]) -> bool:
    """Results gate: legitimate forfeits count as valid results (review §2.3)."""
    result = clean(pairing.get("result"))
    if not result:
        return False
    if is_forfeit(result):
        return True
    return bool(RESULT_CORE_RE.search(result)) and not RESULT_LETTER_RE.search(result)


# --- event archive parsing ---------------------------------------------------

_HEADER_RE = re.compile(r'^\[(\w+)\s+"([^"]*)"\]', re.MULTILINE)


def parse_event_archive(tid: str) -> list[dict[str, str]]:
    path = EVENT_PGN / f"tnr{tid}.pgn"
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    games: list[dict[str, str]] = []
    starts = [m.start() for m in re.finditer(r'^\[Event\s+"', text, flags=re.MULTILINE)]
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(text)
        chunk = text[start:end]
        headers = dict(_HEADER_RE.findall(chunk))
        games.append({
            "round": round_number(headers.get("Round")),
            "board": clean(headers.get("Board")),
            "white": clean(headers.get("White")),
            "black": clean(headers.get("Black")),
            "result": clean(headers.get("Result")),
        })
    return games


def match_archive_games(
    payload: dict[str, Any],
    archive_games: list[dict[str, str]],
    played_keys: set[tuple[str, str]] | None = None,
    advertised_keys: set[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """Match archived games to pairings by natural key round+board.

    Name equality (either orientation) verifies the key; name-only matching is
    the explicit fallback and is reported separately (review §2.2)."""
    if played_keys is None or advertised_keys is None:
        inferred_played: set[tuple[str, str]] = set()
        inferred_advertised: set[tuple[str, str]] = set()
        for round_row in payload.get("rounds") or []:
            rid = round_number(round_row.get("round"))
            for pairing in round_row.get("pairings") or []:
                board = clean(pairing.get("board"))
                if not board or is_bye(pairing) or is_forfeit(pairing.get("result")):
                    continue
                inferred_played.add((rid, board))
                if pairing.get("hasPGN"):
                    inferred_advertised.add((rid, board))
        played_keys = inferred_played if played_keys is None else played_keys
        advertised_keys = inferred_advertised if advertised_keys is None else advertised_keys

    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    by_names: dict[tuple[str, tuple[str, str]], dict[str, Any]] = {}
    for round_row in payload.get("rounds") or []:
        rid = round_number(round_row.get("round"))
        for pairing in round_row.get("pairings") or []:
            if is_bye(pairing):
                continue
            board = clean(pairing.get("board"))
            if board:
                by_key[(rid, board)] = pairing
            names = tuple(sorted([
                normalize_name((pairing.get("white") or {}).get("name")),
                normalize_name((pairing.get("black") or {}).get("name")),
            ]))
            by_names.setdefault((rid, names), pairing)

    matched_exact: set[tuple[str, str]] = set()
    matched_fallback: set[tuple[str, str]] = set()
    mismatched_names = 0
    unmatched_games = 0

    # 提取 forfeits 自然键
    forfeits_keys = set()
    for round_row in payload.get("rounds") or []:
        rid = round_number(round_row.get("round"))
        for pairing in round_row.get("pairings") or []:
            if not is_bye(pairing) and is_forfeit(pairing.get("result")):
                board = clean(pairing.get("board"))
                if board:
                    forfeits_keys.add((rid, board))

    for game in archive_games:
        game_names = tuple(sorted([normalize_name(game["white"]), normalize_name(game["black"])]))
        pairing = by_key.get((game["round"], game["board"])) if game["board"] else None
        if pairing is not None:
            pairing_names = tuple(sorted([
                normalize_name((pairing.get("white") or {}).get("name")),
                normalize_name((pairing.get("black") or {}).get("name")),
            ]))
            if not any(game_names) or not any(pairing_names) or game_names == pairing_names:
                matched_exact.add((game["round"], game["board"]))
                continue
            # Natural key hit but names disagree: never silently count it.
            mismatched_names += 1
        pairing = by_names.get((game["round"], game_names))
        if pairing is not None:
            p_board = clean(pairing.get("board"))
            if p_board:
                matched_fallback.add((game["round"], p_board))
        else:
            unmatched_games += 1

    matched_fallback -= matched_exact
    matched_played = matched_exact & played_keys
    matched_advertised = matched_exact & advertised_keys
    matched_forfeits = matched_exact & forfeits_keys

    return {
        "matchedPlayedKeys": matched_played,
        "matchedAdvertisedKeys": matched_advertised,
        "matchedForfeitKeys": matched_forfeits,
        "fallbackMatchedKeys": matched_fallback,
        "unmatchedPlayedKeys": played_keys - matched_exact,
        "unmatchedAdvertisedKeys": advertised_keys - matched_exact,
        "keyNameMismatches": mismatched_names,
        "unmatchedArchiveGames": unmatched_games,
        "matchedExact": len(matched_exact),
        "matchedNameFallback": len(matched_fallback),
        "matched": len(matched_exact | matched_fallback),
    }


def by_player_game_index() -> dict[str, dict[tuple[str, tuple[str, str]], set[str]]]:
    index: dict[str, dict[tuple[str, tuple[str, str]], set[str]]] = defaultdict(dict)
    for path in sorted(BY_PLAYER.glob("fide-*.json")):
        detail = read_json(path, {})
        for game in detail.get("games", []) or []:
            tid = clean(game.get("tournamentID"))
            if not tid:
                continue
            fingerprint = clean(game.get("sha256") or game.get("id"))
            key = (
                round_number(game.get("round")),
                tuple(sorted([normalize_name(game.get("white")), normalize_name(game.get("black"))])),
            )
            index[tid].setdefault(key, set()).add(fingerprint or f"{path.name}:{key}")
    return index


def load_pgn_leads() -> dict[str, dict[str, str]]:
    """Merged lead registry: tid -> {leadType, knownCoverage, status}."""
    leads: dict[str, dict[str, str]] = {}
    for source in (PGN_LEADS_PUBLIC, PGN_LEADS_PRIVATE):
        if not source.exists():
            continue
        with source.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                tid = re.sub(r"^tnr", "", clean(row.get("tournament_id")), flags=re.IGNORECASE)
                if tid and clean(row.get("status")) not in ("resolved", "rejected"):
                    leads[tid] = {
                        "leadType": clean(row.get("lead_type")) or "external-lead",
                        "knownCoverage": clean(row.get("known_coverage")),
                        "status": clean(row.get("status")) or "open",
                    }
    return leads


def event_report(
    payload: dict[str, Any],
    by_player_games: dict[tuple[str, tuple[str, str]], set[str]],
    collection_status: dict[str, Any] | None = None,
    public_archive_verified: bool = False,
) -> dict[str, Any]:
    tid = clean(payload.get("tournamentID"))
    players = payload.get("players") or []
    standings = payload.get("standings") or []
    rounds = payload.get("rounds") or []
    round_count = int(payload.get("roundCount") or 0)
    capture_status = clean(payload.get("captureStatus")) or "complete"
    is_team = payload.get("format") == "team"
    collection_status = collection_status or {}

    roster_nos = {clean(p.get("playerNo")) for p in players if clean(p.get("playerNo"))}
    pairings = [p for r in rounds for p in (r.get("pairings") or [])]
    non_bye = [p for p in pairings if not is_bye(p)]
    played = [p for p in non_bye if played_game_expected(p) is True]
    played_unknown = sum(1 for p in non_bye if played_game_expected(p) is None)
    forfeits = sum(1 for p in non_bye if is_forfeit(p.get("result")))
    advertised = [p for p in played if advertised_pgn(p)]
    valid_results = sum(1 for p in non_bye if valid_result(p))

    # --- played and advertised expected keys ---
    played_keys = set()
    advertised_keys = set()
    for round_row in rounds:
        rid = round_number(round_row.get("round"))
        for p in round_row.get("pairings") or []:
            if is_bye(p):
                continue
            board = clean(p.get("board"))
            if not board:
                continue
            if played_game_expected(p) is True:
                played_keys.add((rid, board))
                if advertised_pgn(p):
                    advertised_keys.add((rid, board))

    # --- referential integrity (independent expected values, review §2.4) ---
    standing_nos = {clean(s.get("playerNo")) for s in standings if clean(s.get("playerNo"))}
    standing_ref_violations = sum(
        1 for s in standings
        if clean(s.get("playerNo")) and roster_nos and clean(s.get("playerNo")) not in roster_nos
    )
    pairing_ref_violations = 0
    per_round_coverage: list[float] = []
    for round_row in rounds:
        covered: set[str] = set()
        for pairing in round_row.get("pairings") or []:
            for side_key in ("white", "black"):
                no = clean((pairing.get(side_key) or {}).get("playerNo"))
                if no:
                    covered.add(no)
                    if roster_nos and no not in roster_nos and not is_team:
                        pairing_ref_violations += 1
        if roster_nos:
            per_round_coverage.append(len(covered & roster_nos) / len(roster_nos))
    min_round_coverage = round(min(per_round_coverage), 4) if per_round_coverage else None

    # --- archive matching (archives are first-class input, review §2.1) -----
    archive_games = parse_event_archive(tid)
    if archive_games:
        match = match_archive_games(payload, archive_games, played_keys, advertised_keys)
    else:
        matched_keys: set[tuple[str, tuple[str, str]]] = set()
        for round_row in rounds:
            rid = round_number(round_row.get("round"))
            for pairing in round_row.get("pairings") or []:
                if is_bye(pairing):
                    continue
                key = (rid, tuple(sorted([
                    normalize_name((pairing.get("white") or {}).get("name")),
                    normalize_name((pairing.get("black") or {}).get("name")),
                ])))
                if key in by_player_games:
                    matched_keys.add(key)
        match = {
            "matchedPlayedKeys": set(),
            "matchedAdvertisedKeys": set(),
            "matchedForfeitKeys": set(),
            "fallbackMatchedKeys": matched_keys,
            "unmatchedPlayedKeys": played_keys,
            "unmatchedAdvertisedKeys": advertised_keys,
            "keyNameMismatches": 0,
            "unmatchedArchiveGames": 0,
            "matchedExact": 0,
            "matchedNameFallback": len(matched_keys),
            "matched": len(matched_keys),
        }
    matched = match["matched"]
    local_fingerprints: set[str] = set()
    for prints in by_player_games.values():
        local_fingerprints.update(prints)

    gates = {
        "roster": {"expected": len(players) or None, "actual": len(players),
                   "status": "complete" if players else "unknown"},
        "standings": {
            "expected": (len(players) if not is_team else None) or None,
            "actual": len(standings),
            "refViolations": standing_ref_violations,
            "status": (
                "unknown" if is_team or not players else
                "complete" if standings and standing_ref_violations == 0 and standing_nos == roster_nos
                else "partial"
            ),
        },
        "rounds": {
            "expected": round_count or None,
            "actual": len(rounds),
            "status": (
                "unknown" if not round_count else
                "complete" if len(rounds) >= round_count else "partial"
            ),
        },
        "pairings": {
            "expected": None,  # no independent per-board expectation yet
            "actual": len(pairings),
            "refViolations": pairing_ref_violations,
            "minRoundRosterCoverage": min_round_coverage,
            "status": (
                "unknown" if not pairings else
                "complete" if pairing_ref_violations == 0 else "partial"
            ),
        },
        "results": {"expected": len(non_bye), "valid": valid_results,
                    "status": "complete" if non_bye and valid_results == len(non_bye) else ("unknown" if not non_bye else "partial")},
        "pgnExpected": {"expected": len(advertised), "playedGames": len(played),
                        "playedUnknown": played_unknown, "forfeits": forfeits},
        "pgnMatched": {"expected": len(advertised), "matched": matched,
                       "matchedExact": match["matchedExact"],
                       "matchedNameFallback": match["matchedNameFallback"],
                       "keyNameMismatches": match["keyNameMismatches"],
                       "unmatchedArchiveGames": match["unmatchedArchiveGames"]},
    }

    hard_gates = [gates["standings"]["status"], gates["rounds"]["status"],
                  gates["pairings"]["status"], gates["results"]["status"]]
    if capture_status != "complete" or "partial" in hard_gates:
        results_status = "partial"
    elif "unknown" in hard_gates:
        results_status = "unknown"
    else:
        results_status = "results-complete"

    # --- source-advertised availability (played-game denominator) -----------
    if not played:
        availability = "no-played-games" if non_bye else "no-pairings"
        scope = "none-published"
    elif not advertised:
        availability = "not-published"
        scope = "none-published"
    elif len(advertised) < len(played):
        availability = "advertised-partial"
        scope = "selected-live-boards"
    else:
        availability = "advertised-full"
        scope = "all-played-boards"

    # 严格根据包含性判定
    matched_exact_keys = match["matchedPlayedKeys"] | match["matchedAdvertisedKeys"] | match["matchedForfeitKeys"]
    is_matched_full = played_keys.issubset(matched_exact_keys) if played_keys else False
    is_matched_advertised = advertised_keys.issubset(matched_exact_keys) if advertised_keys else False

    # --- archive/matching state (review §2.1 status vocabulary) -------------
    if availability in ("not-published", "no-pairings", "no-played-games"):
        if archive_games or local_fingerprints:
            archive_status = "external-or-legacy-supplement"
        else:
            archive_status = "none-expected"
    elif archive_games:
        if matched == 0:
            archive_status = "archived-unmatched"
        elif is_matched_full:
            archive_status = "matched-full"
        elif is_matched_advertised and len(advertised) < len(played):
            archive_status = "matched-advertised-complete"
        else:
            archive_status = "matched-partial"
    elif local_fingerprints:
        archive_status = "locally-recoverable"
    else:
        archive_status = "missing"

    # 分子限制在对应的精确匹配子集中，防范 coverage > 1
    advertised_coverage = round(len(match["matchedAdvertisedKeys"]) / len(advertised), 4) if advertised else None
    played_coverage = round(len(match["matchedPlayedKeys"]) / len(played), 4) if played else None

    publishable = results_status == "results-complete"

    # 判定 playableComplete：所有 played expected 都能被 FIDE by-player 索引覆盖
    playable_pairings_count = 0
    for round_row in rounds:
        rid = round_number(round_row.get("round"))
        for pairing in round_row.get("pairings") or []:
            if is_bye(pairing):
                continue
            if played_game_expected(pairing) is True:
                key = (rid, tuple(sorted([
                    normalize_name((pairing.get("white") or {}).get("name")),
                    normalize_name((pairing.get("black") or {}).get("name")),
                ])))
                if key in by_player_games:
                    playable_pairings_count += 1

    playable_complete = publishable and len(played) > 0 and (
        (archive_status == "matched-full" and public_archive_verified)
        or playable_pairings_count >= len(played)
    )
    event_complete = publishable and archive_status == "matched-full"

    return {
        "tournamentID": tid,
        "format": payload.get("format"),
        "captureStatus": capture_status,
        "captureErrorCode": payload.get("captureErrorCode") or None,
        "gates": gates,
        "resultsStatus": results_status,
        "pgnAvailability": availability,
        "pgnCoverageScope": scope,
        "archiveStatus": archive_status,
        "pgnSourceStatus": collection_status.get("status") or "not-attempted",
        "pgnSourceErrorCode": collection_status.get("errorCode") or None,
        "pgnLastAttemptedAt": collection_status.get("attemptedAt") or None,
        "publicArchiveVerified": public_archive_verified,
        "playableComplete": playable_complete,
        "eventComplete": event_complete,
        "counts": {
            "players": len(players),
            "standings": len(standings),
            "roundsExpected": round_count or None,
            "roundsCaptured": len(rounds),
            "pairings": len(pairings),
            "nonByePairings": len(non_bye),
            "playedGames": len(played),
            "forfeits": forfeits,
            "advertisedPGN": len(advertised),
            "archivedGames": len(archive_games),
            "matchedPairings": matched,
            "localGameFingerprints": len(local_fingerprints),
        },
        "advertisedCoverage": advertised_coverage,
        "allBoardCoverage": played_coverage,
        "publishable": publishable,
    }


def supplement_queue(reports: list[dict[str, Any]], leads: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    """Actionable maintainer tasks only (review §2.5).

    Bare not-published events without any lead stay out of the queue; they
    are coverage statistics, not work items."""
    queue: list[dict[str, Any]] = []
    seen: set[str] = set()
    for report in reports:
        tid = report["tournamentID"]
        availability = report["pgnAvailability"]
        archive = report["archiveStatus"]
        counts = report["counts"]
        lead = leads.get(tid)
        source_status = report.get("pgnSourceStatus")
        priority = action = None
        if archive in ("archived-unmatched", "matched-partial"):
            priority, action = "P0", "offline-rematch-existing-archive"
        elif availability in ("advertised-full", "advertised-partial") and archive == "missing":
            priority, action = "P0", "re-fetch-or-import-advertised-boards"
        elif source_status in ("fetch-failed", "empty-response") and archive == "missing":
            priority, action = "P0", "retry-source-pgn-fetch"
        elif source_status == "not-published" and availability.startswith("advertised"):
            priority, action = "P0", "audit-contradictory-source-pgn-state"
        elif availability == "advertised-partial" and archive == "matched-advertised-complete":
            continue  # promised boards done
        elif archive == "locally-recoverable":
            priority, action = "P1", "offline-restore-from-by-player-packs"
        elif lead:
            priority, action = "P1", f"follow-lead:{lead['leadType']}"
        else:
            continue
        seen.add(tid)
        queue.append({
            "tournamentID": tid,
            "priority": priority,
            "nextAction": action,
            "resultsStatus": report["resultsStatus"],
            "pgnAvailability": availability,
            "archiveStatus": archive,
            "pgnSourceStatus": source_status,
            "pgnSourceErrorCode": report.get("pgnSourceErrorCode"),
            "pgnLastAttemptedAt": report.get("pgnLastAttemptedAt"),
            "playedGames": counts["playedGames"],
            "advertisedPGN": counts["advertisedPGN"],
            "archivedGames": counts["archivedGames"],
            "matchedPairings": counts["matchedPairings"],
            "localGameFingerprints": counts["localGameFingerprints"],
            **({"lead": lead} if lead else {}),
        })
    # Leads for events without a structured detail yet (e.g. 盐城快棋赛
    # tnr1210265–1210272) must still surface as tasks.
    for tid, lead in leads.items():
        if tid in seen:
            continue
        queue.append({
            "tournamentID": tid,
            "priority": "P1",
            "nextAction": f"follow-lead:{lead['leadType']}",
            "resultsStatus": "no-structured-detail",
            "pgnAvailability": "unknown",
            "archiveStatus": "missing",
            "lead": lead,
        })
    order = {"P0": 0, "P1": 1, "P2": 2}
    queue.sort(key=lambda row: (order.get(row["priority"], 9), row["tournamentID"]))
    return queue


def build() -> dict[str, Any]:
    games_index = by_player_game_index()
    public_archives = verified_event_archives()
    status_payload = read_json(COLLECTION_STATUS, {})
    collection_statuses = status_payload.get("events") if isinstance(status_payload.get("events"), dict) else {}
    reports = []
    for path in sorted(DETAILS.glob("tnr*.json")):
        payload = read_json(path, {})
        tid = clean(payload.get("tournamentID"))
        if not tid:
            continue
        reports.append(event_report(
            payload,
            games_index.get(tid, {}),
            collection_statuses.get(tid, {}),
            tid in public_archives,
        ))
    summary = {
        "events": len(reports),
        "resultsComplete": sum(1 for r in reports if r["resultsStatus"] == "results-complete"),
        "resultsUnknown": sum(1 for r in reports if r["resultsStatus"] == "unknown"),
        "publishable": sum(1 for r in reports if r["publishable"]),
        "eventComplete": sum(1 for r in reports if r["eventComplete"]),
        "pgnAvailability": {},
        "archiveStatus": {},
        "pgnSourceStatus": {},
    }
    for report in reports:
        summary["pgnAvailability"][report["pgnAvailability"]] = summary["pgnAvailability"].get(report["pgnAvailability"], 0) + 1
        summary["archiveStatus"][report["archiveStatus"]] = summary["archiveStatus"].get(report["archiveStatus"], 0) + 1
        summary["pgnSourceStatus"][report["pgnSourceStatus"]] = summary["pgnSourceStatus"].get(report["pgnSourceStatus"], 0) + 1
    return {
        "schemaVersion": 2,
        "generatedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "summary": summary,
        "events": reports,
    }


def main() -> int:
    # Shrink guard: environments without the full private capture layer keep
    # the committed report instead of silently shrinking the publishable set.
    previous = read_json(OUTPUT, {})
    visible = len(list(DETAILS.glob("tnr*.json")))
    committed = len(previous.get("events") or [])
    if committed and visible < committed and not os.environ.get("FORCE_COMPLETENESS_SHRINK"):
        print(json.dumps({
            "skipped": "private capture layer incomplete",
            "visibleDetails": visible,
            "committedReportEvents": committed,
            "hint": "run on the collector machine or set FORCE_COMPLETENESS_SHRINK=1",
        }, ensure_ascii=False))
        return 0

    report = build()
    write_json(OUTPUT, report, ensure_ascii=False, indent=2)
    queue = supplement_queue(report["events"], load_pgn_leads())
    write_json(QUEUE_OUTPUT, {
        "schemaVersion": 2,
        "generatedAt": report["generatedAt"],
        "totals": {"tasks": len(queue)},
        "tasks": queue,
    }, ensure_ascii=False, indent=2)
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
