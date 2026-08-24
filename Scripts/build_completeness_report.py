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
import pathlib
import re
from collections import defaultdict
from typing import Any

from canonical_player_facts import PLAYER_GAME_FACTS, load_fact_dataset, manifest_reference
from stable_json import write_json

try:
    from source_policy import local_state_root
except Exception:  # pragma: no cover - CI without local policy module extras
    def local_state_root() -> pathlib.Path:
        return pathlib.Path.home() / ".china-chess-player-pgn"

ROOT = pathlib.Path(__file__).resolve().parents[1]
DETAILS = ROOT / "data" / "generated" / "chess-results-event-details"
EVENT_PGN = ROOT / "data" / "generated" / "chess-results-event-pgn"
LICHESS_EVENT_ROOT = ROOT / "docs" / "data" / "bulk" / "lichess-events"
LICHESS_EVENT_PGN = LICHESS_EVENT_ROOT / "pgn"
LICHESS_EVENT_MANIFEST = LICHESS_EVENT_ROOT / "manifest.json"
OUTPUT = ROOT / "data" / "generated" / "event-completeness-report.json"
QUEUE_OUTPUT = ROOT / "data" / "generated" / "pgn-supplement-queue.json"
COLLECTION_STATUS = ROOT / "data" / "generated" / "pgn-collection-status.json"
EVENT_PGN_RECEIPT = ROOT / "data" / "generated" / "r2-object-receipts" / "events--chess-results.json"
# Two lead registries: the repo copy holds sanitized, reviewable leads
# (event id + coverage claim only); URLs/private hints stay in the
# maintainer-local file outside the repository.
PGN_LEADS_PRIVATE = local_state_root() / "pgn-leads.csv"
PGN_LEADS_PUBLIC = ROOT / "data" / "manual" / "pgn-leads.csv"
OFFLINE_REMATCH_EVIDENCE = ROOT / "data" / "manual" / "offline-pgn-rematch-evidence.csv"

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


def normalize_result(value: Any) -> str:
    result = clean(value).replace(" ", "")
    if result in {"1/2-1/2", "½-½", "0.5-0.5"}:
        return "draw"
    if result in {"1-0", "0-1"}:
        return result
    return result


def invert_result(value: Any) -> str:
    result = normalize_result(value)
    if result == "1-0":
        return "0-1"
    if result == "0-1":
        return "1-0"
    return result


def archive_name_compatible(archive_value: Any, pairing_value: Any) -> bool:
    """Accept exact names or a narrowly bounded omitted trailing initial.

    Historical live-board PGNs sometimes omit the final middle-name initial
    that is present in the independently captured starting list.  The match
    remains useful evidence only when the shared normalized name is long and
    the roster value adds at most two trailing characters.  An empty archive
    name is handled by ``archive_pairing_orientation`` where at least the
    other side must still identify the board.
    """
    archive_name = normalize_name(archive_value)
    pairing_name = normalize_name(pairing_value)
    if not archive_name:
        return True
    if not pairing_name:
        return False
    if archive_name == pairing_name:
        return True
    return (
        len(archive_name) >= 6
        and pairing_name.startswith(archive_name)
        and 1 <= len(pairing_name) - len(archive_name) <= 2
    )


def archive_pairing_orientation(
    game: dict[str, str], pairing: dict[str, Any]
) -> str | None:
    """Return the unique identity/result orientation, otherwise reject.

    Round+board is never accepted on its own.  At least one PGN player name
    must be present, all present names must be exact or narrowly abbreviated,
    and the result must agree after accounting for a possible color reversal.
    """
    game_white = clean(game.get("white"))
    game_black = clean(game.get("black"))
    if not game_white and not game_black:
        return None
    pairing_white = clean((pairing.get("white") or {}).get("name"))
    pairing_black = clean((pairing.get("black") or {}).get("name"))
    pairing_result = normalize_result(pairing.get("result"))
    game_result = normalize_result(game.get("result"))
    if not pairing_result or not game_result:
        return None

    orientations: list[str] = []
    if (
        archive_name_compatible(game_white, pairing_white)
        and archive_name_compatible(game_black, pairing_black)
        and game_result == pairing_result
    ):
        orientations.append("direct")
    if (
        archive_name_compatible(game_white, pairing_black)
        and archive_name_compatible(game_black, pairing_white)
        and invert_result(game_result) == pairing_result
    ):
        orientations.append("swapped")
    return orientations[0] if len(orientations) == 1 else None


def round_number(value: Any) -> str:
    match = re.match(r"(\d+)", clean(value))
    return match.group(1) if match else clean(value)


def read_json_optional(path: pathlib.Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def read_json_required(path: pathlib.Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise RuntimeError(f"required JSON is unreadable: {path}") from error
    except json.JSONDecodeError as error:
        raise RuntimeError(f"required JSON is invalid: {path}: {error}") from error


def verified_offline_rematch_ids() -> set[str]:
    """Return reviewed events only while both immutable inputs still match."""
    verified: set[str] = set()
    if not OFFLINE_REMATCH_EVIDENCE.is_file():
        return verified
    with OFFLINE_REMATCH_EVIDENCE.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            tid = re.sub(r"^tnr", "", clean(row.get("tournament_id")), flags=re.IGNORECASE)
            detail = DETAILS / f"tnr{tid}.json"
            archive = EVENT_PGN / f"tnr{tid}.pgn"
            if not tid or not detail.is_file() or not archive.is_file():
                continue
            if (
                hashlib.sha256(detail.read_bytes()).hexdigest() == clean(row.get("detail_sha256"))
                and hashlib.sha256(archive.read_bytes()).hexdigest() == clean(row.get("pgn_sha256"))
            ):
                verified.add(tid)
    return verified


def verified_event_archives() -> set[str]:
    receipt = read_json_optional(EVENT_PGN_RECEIPT, {})
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


NON_PLAYER_PAIRING_NAMES = {"bye", "notpaired"}


def side_player_number(pairing: dict[str, Any], side: str) -> str:
    return clean((pairing.get(side) or {}).get("playerNo"))


def side_name_key(pairing: dict[str, Any], side: str) -> str:
    return normalize_name((pairing.get(side) or {}).get("name"))


def is_bye(pairing: dict[str, Any]) -> bool:
    """A bye requires exactly one roster player and an explicit source marker."""
    white_no = side_player_number(pairing, "white")
    black_no = side_player_number(pairing, "black")
    if bool(white_no) == bool(black_no):
        return False
    missing_side = "black" if white_no else "white"
    return side_name_key(pairing, missing_side) in NON_PLAYER_PAIRING_NAMES


def is_unresolved_pairing(pairing: dict[str, Any]) -> bool:
    """A non-bye row with either roster reference missing is unresolved."""
    if is_bye(pairing):
        return False
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


def parse_archive_path(path: pathlib.Path, source: str) -> list[dict[str, str]]:
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
            "source": source,
        })
    return games


def parse_event_archive(tid: str, payload: dict[str, Any] | None = None) -> list[dict[str, str]]:
    payload = payload or {}
    pairings = [p for row in payload.get("rounds") or [] for p in row.get("pairings") or []]
    source = (
        "FIDE Event Report"
        if clean(payload.get("fideEventID")) and not any(advertised_pgn(p) for p in pairings)
        else "Chess-Results"
    )
    return [
        *parse_archive_path(EVENT_PGN / f"tnr{tid}.pgn", source),
        *parse_archive_path(LICHESS_EVENT_PGN / f"tnr{tid}.pgn", "Lichess Broadcasts"),
    ]


def verified_lichess_event_archives() -> set[str]:
    manifest = read_json_optional(LICHESS_EVENT_MANIFEST, {})
    verified: set[str] = set()
    for event in manifest.get("events", []) or []:
        tid = clean(event.get("tournamentID"))
        path_value = clean(event.get("pgnPath"))
        expected = clean(event.get("sha256"))
        if not tid or path_value != f"data/bulk/lichess-events/pgn/tnr{tid}.pgn" or not expected:
            continue
        path = LICHESS_EVENT_PGN / f"tnr{tid}.pgn"
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == expected:
            verified.add(tid)
    return verified


def lichess_event_metadata() -> dict[str, dict[str, Any]]:
    manifest = read_json_optional(LICHESS_EVENT_MANIFEST, {})
    return {
        clean(event.get("tournamentID")): event
        for event in manifest.get("events", []) or []
        if clean(event.get("tournamentID"))
    }


def match_archive_games(
    payload: dict[str, Any],
    archive_games: list[dict[str, str]],
    played_keys: set[tuple[str, str]] | None = None,
    advertised_keys: set[tuple[str, str]] | None = None,
    *,
    reviewed_rematch: bool = False,
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
                if not board or is_bye(pairing) or is_unresolved_pairing(pairing) or is_forfeit(pairing.get("result")):
                    continue
                inferred_played.add((rid, board))
                if pairing.get("hasPGN"):
                    inferred_advertised.add((rid, board))
        played_keys = inferred_played if played_keys is None else played_keys
        advertised_keys = inferred_advertised if advertised_keys is None else advertised_keys

    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    by_round: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_names: dict[tuple[str, tuple[str, str]], dict[str, Any]] = {}
    for round_row in payload.get("rounds") or []:
        rid = round_number(round_row.get("round"))
        for pairing in round_row.get("pairings") or []:
            if is_bye(pairing) or is_unresolved_pairing(pairing):
                continue
            board = clean(pairing.get("board"))
            if board:
                by_key[(rid, board)] = pairing
            by_round[rid].append(pairing)
            names = tuple(sorted([
                normalize_name((pairing.get("white") or {}).get("name")),
                normalize_name((pairing.get("black") or {}).get("name")),
            ]))
            by_names.setdefault((rid, names), pairing)

    matched_exact: set[tuple[str, str]] = set()
    matched_fallback: set[tuple[str, str]] = set()
    remapped_advertised_from: set[tuple[str, str]] = set()
    remapped_advertised_to: set[tuple[str, str]] = set()
    mismatched_names = 0
    unmatched_games = 0

    # 提取 forfeits 自然键
    forfeits_keys = set()
    for round_row in payload.get("rounds") or []:
        rid = round_number(round_row.get("round"))
        for pairing in round_row.get("pairings") or []:
            if not is_bye(pairing) and not is_unresolved_pairing(pairing) and is_forfeit(pairing.get("result")):
                board = clean(pairing.get("board"))
                if board:
                    forfeits_keys.add((rid, board))

    for game in archive_games:
        game_names = tuple(sorted([
            normalize_name(game["white"]), normalize_name(game["black"])
        ]))
        pairing = by_key.get((game["round"], game["board"])) if game["board"] else None
        if pairing is not None:
            pairing_names = tuple(sorted([
                normalize_name((pairing.get("white") or {}).get("name")),
                normalize_name((pairing.get("black") or {}).get("name")),
            ]))
            accepted = (
                archive_pairing_orientation(game, pairing) is not None
                if reviewed_rematch
                else (
                    not any(game_names)
                    or not any(pairing_names)
                    or game_names == pairing_names
                )
            )
            if accepted:
                matched_exact.add((game["round"], game["board"]))
                continue
            # Natural key hit but identity/result evidence disagrees: never
            # silently count it. A unique same-round pairing may still prove
            # that the historical PGN Board header itself is wrong.
            mismatched_names += 1
        if reviewed_rematch:
            candidates = [
                candidate
                for candidate in by_round.get(game["round"], [])
                if archive_pairing_orientation(game, candidate) is not None
            ]
        else:
            legacy_candidate = by_names.get((game["round"], game_names))
            candidates = [legacy_candidate] if legacy_candidate is not None else []
        candidate_keys = {
            (game["round"], clean(candidate.get("board")))
            for candidate in candidates
            if clean(candidate.get("board"))
        }
        if len(candidate_keys) == 1:
            matched_fallback.update(candidate_keys)
            header_key = (game["round"], game["board"])
            if (
                reviewed_rematch
                and
                clean(game.get("source")) in {"", "Chess-Results"}
                and game["board"]
                and header_key in advertised_keys
            ):
                # Some historical PartieSuche links were attached to the
                # wrong board row. The downloaded game itself proves the
                # uniquely identified target board was published, so replace
                # (rather than expand) that one advertised natural key.
                remapped_advertised_from.add(header_key)
                remapped_advertised_to.update(candidate_keys)
        else:
            unmatched_games += 1

    matched_fallback -= matched_exact
    accepted_keys = matched_exact | matched_fallback
    coverage_keys = accepted_keys if reviewed_rematch else matched_exact
    effective_advertised_keys = (
        (advertised_keys - remapped_advertised_from) | remapped_advertised_to
        if reviewed_rematch else advertised_keys
    )
    matched_played = coverage_keys & played_keys
    matched_advertised = coverage_keys & effective_advertised_keys
    matched_forfeits = coverage_keys & forfeits_keys

    return {
        "matchedPlayedKeys": matched_played,
        "matchedAdvertisedKeys": matched_advertised,
        "matchedForfeitKeys": matched_forfeits,
        "fallbackMatchedKeys": matched_fallback,
        "unmatchedPlayedKeys": played_keys - coverage_keys,
        "unmatchedAdvertisedKeys": effective_advertised_keys - coverage_keys,
        "effectiveAdvertisedKeys": effective_advertised_keys,
        "remappedAdvertisedFromKeys": remapped_advertised_from,
        "remappedAdvertisedToKeys": remapped_advertised_to,
        "keyNameMismatches": mismatched_names,
        "unmatchedArchiveGames": unmatched_games,
        "matchedExact": len(matched_exact),
        "matchedNameFallback": len(matched_fallback),
        "matched": len(matched_exact | matched_fallback),
    }


def player_game_fact_index() -> tuple[dict[str, dict[tuple[str, tuple[str, str]], set[str]]], dict[str, Any]]:
    """Index canonical current-snapshot game facts for archive recovery.

    This is intentionally not a by-player fallback.  Missing or stale fact
    manifests fail before completeness can reuse a previous projection.
    """
    index: dict[str, dict[tuple[str, tuple[str, str]], set[str]]] = defaultdict(dict)
    facts, manifest = load_fact_dataset(PLAYER_GAME_FACTS, "player-game-facts")
    for game in facts:
        tid = clean(game.get("tournamentID"))
        if not tid:
            continue
        fingerprint = clean(game.get("fingerprint") or game.get("gameSha256") or game.get("id"))
        key = (
            round_number(game.get("round")),
            tuple(sorted([normalize_name(game.get("white")), normalize_name(game.get("black"))])),
        )
        index[tid].setdefault(key, set()).add(fingerprint or f"fact:{tid}:{key}")
    return index, manifest_reference(PLAYER_GAME_FACTS, manifest)


def independent_pairing_expectation(payload: dict[str, Any]) -> dict[str, Any]:
    """Expected non-bye boards from the independent roster page.

    The starting list and round tables are captured separately.  For each
    round the roster population, less players explicitly marked ``bye`` or
    ``not paired``, yields the lower-bound board expectation.  ``floor(N/2)``
    deliberately does not fail an odd roster merely because one player is
    absent from the pairing rows.  Unknown roster/round-count evidence stays
    unknown rather than being promoted to complete.
    """
    if payload.get("format") == "team":
        return {"expected": None, "status": "unknown", "basis": "team-format-unsupported", "rounds": []}
    roster = [clean(row.get("playerNo")) for row in payload.get("players") or [] if clean(row.get("playerNo"))]
    unique_roster = set(roster)
    round_count = int(payload.get("roundCount") or 0)
    if not unique_roster or not round_count or len(unique_roster) != len(roster):
        return {
            "expected": None,
            "status": "unknown",
            "basis": "independent-roster-or-round-count-unavailable",
            "rounds": [],
        }
    rounds = {
        round_number(row.get("round")): row
        for row in payload.get("rounds") or []
        if round_number(row.get("round"))
    }
    rows = []
    total_expected = total_actual = missing = excess = duplicate_refs = 0
    for number in range(1, round_count + 1):
        rid = str(number)
        pairings = (rounds.get(rid) or {}).get("pairings") or []
        explicitly_not_playing: set[str] = set()
        seen_refs: list[str] = []
        actual_non_bye = 0
        for pairing in pairings:
            if is_bye(pairing):
                for side in ("white", "black"):
                    player_no = side_player_number(pairing, side)
                    if player_no in unique_roster:
                        explicitly_not_playing.add(player_no)
                continue
            actual_non_bye += 1
            for side in ("white", "black"):
                player_no = side_player_number(pairing, side)
                if player_no:
                    seen_refs.append(player_no)
        expected = max(0, (len(unique_roster) - len(explicitly_not_playing)) // 2)
        round_missing = max(0, expected - actual_non_bye)
        round_excess = max(0, actual_non_bye - expected)
        duplicates = len(seen_refs) - len(set(seen_refs))
        rows.append({
            "round": rid,
            "expected": expected,
            "actual": actual_non_bye,
            "explicitNonPlaying": len(explicitly_not_playing),
            "unexplainedMissing": round_missing,
            "unexpectedExtra": round_excess,
            "duplicatePlayerRefs": duplicates,
        })
        total_expected += expected
        total_actual += actual_non_bye
        missing += round_missing
        excess += round_excess
        duplicate_refs += duplicates
    status = "partial" if missing or excess or duplicate_refs else "complete"
    return {
        "expected": total_expected,
        "actual": total_actual,
        "status": status,
        "basis": "starting-roster-minus-explicit-bye-or-not-paired",
        "unexplainedMissing": missing,
        "unexpectedExtra": excess,
        "duplicatePlayerRefs": duplicate_refs,
        "rounds": rows,
    }


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
    lichess_status: dict[str, Any] | None = None,
    offline_rematch_ids: set[str] | None = None,
) -> dict[str, Any]:
    tid = clean(payload.get("tournamentID"))
    reviewed_rematch = tid in (
        offline_rematch_ids
        if offline_rematch_ids is not None
        else verified_offline_rematch_ids()
    )
    players = payload.get("players") or []
    standings = payload.get("standings") or []
    rounds = payload.get("rounds") or []
    round_count = int(payload.get("roundCount") or 0)
    capture_status = clean(payload.get("captureStatus")) or "complete"
    is_team = payload.get("format") == "team"
    collection_status = collection_status or {}
    lichess_status = lichess_status or {}

    roster_nos = {clean(p.get("playerNo")) for p in players if clean(p.get("playerNo"))}
    pairings = [p for r in rounds for p in (r.get("pairings") or [])]
    unresolved_pairings = [p for p in pairings if is_unresolved_pairing(p)]
    non_bye = [p for p in pairings if not is_bye(p) and not is_unresolved_pairing(p)]
    played = [p for p in non_bye if played_game_expected(p) is True]
    played_unknown = sum(1 for p in non_bye if played_game_expected(p) is None)
    forfeits = sum(1 for p in non_bye if is_forfeit(p.get("result")))
    valid_results = sum(1 for p in non_bye if valid_result(p))

    # --- played and advertised expected keys ---
    played_keys = set()
    advertised_keys = set()
    for round_row in rounds:
        rid = round_number(round_row.get("round"))
        for p in round_row.get("pairings") or []:
            if is_bye(p) or is_unresolved_pairing(p):
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
    pairing_expectation = independent_pairing_expectation(payload)

    # --- archive matching (archives are first-class input, review §2.1) -----
    archive_games = parse_event_archive(tid, payload)
    archive_sources = sorted({clean(game.get("source")) for game in archive_games if clean(game.get("source"))})
    lichess_games = [game for game in archive_games if clean(game.get("source")) == "Lichess Broadcasts"]
    fide_event_games = [game for game in archive_games if clean(game.get("source")) == "FIDE Event Report"]
    lichess_residual = int(lichess_status.get("linkedContainerUnmatchedGames") or 0)
    lichess_incomplete = int(lichess_status.get("linkedContainerIncompleteGames") or 0)
    lichess_container_games = int(lichess_status.get("linkedContainerGames") or 0)
    lichess_scope_verified = (
        not lichess_games and lichess_container_games == 0
        or (
            bool(lichess_games)
            and lichess_status.get("broadcastComplete") is True
            and lichess_residual == 0
        )
    )
    if lichess_games:
        # A successfully cross-matched Lichess broadcast game is itself
        # evidence that the source published that board.  Expand the
        # source-published denominator before matching the combined archives.
        lichess_match = match_archive_games(payload, lichess_games, played_keys, set())
        advertised_keys.update(lichess_match["matchedPlayedKeys"])
    if fide_event_games:
        # A FIDE-Event-ID link is independent cross-source evidence. Only the
        # official PGN games that strictly match captured round+board facts
        # expand the published denominator.
        fide_match = match_archive_games(payload, fide_event_games, played_keys, set())
        advertised_keys.update(fide_match["matchedPlayedKeys"])
    if archive_games:
        match = match_archive_games(
            payload, archive_games, played_keys, advertised_keys,
            reviewed_rematch=reviewed_rematch,
        )
        advertised_keys = match["effectiveAdvertisedKeys"]
    else:
        matched_keys: set[tuple[str, tuple[str, str]]] = set()
        for round_row in rounds:
            rid = round_number(round_row.get("round"))
            for pairing in round_row.get("pairings") or []:
                if is_bye(pairing) or is_unresolved_pairing(pairing):
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
            "effectiveAdvertisedKeys": advertised_keys,
            "remappedAdvertisedFromKeys": set(),
            "remappedAdvertisedToKeys": set(),
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
            "expected": pairing_expectation.get("expected"),
            "actual": len(pairings),
            "actualNonBye": pairing_expectation.get("actual"),
            "expectedBasis": pairing_expectation.get("basis"),
            "refViolations": pairing_ref_violations,
            "unresolved": len(unresolved_pairings),
            "unexplainedMissing": pairing_expectation.get("unexplainedMissing"),
            "unexpectedExtra": pairing_expectation.get("unexpectedExtra"),
            "duplicatePlayerRefs": pairing_expectation.get("duplicatePlayerRefs"),
            "byRound": pairing_expectation.get("rounds"),
            "minRoundRosterCoverage": min_round_coverage,
            "status": (
                "unknown" if pairing_expectation.get("status") == "unknown" else
                "complete" if (
                    pairing_expectation.get("status") == "complete"
                    and pairing_ref_violations == 0
                    and not unresolved_pairings
                ) else "partial"
            ),
        },
        "results": {"expected": len(non_bye), "valid": valid_results,
                    "status": (
                        "partial" if unresolved_pairings else
                        "complete" if non_bye and valid_results == len(non_bye)
                        else ("unknown" if not non_bye else "partial")
                    )},
        "pgnExpected": {"expected": len(advertised_keys), "playedGames": len(played),
                        "playedUnknown": played_unknown, "forfeits": forfeits},
        "pgnMatched": {"expected": len(advertised_keys), "matched": matched,
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
    # An official FIDE event PGN link advertises event-wide coverage even when
    # strict validation rejects the downloaded bytes. Preserve that promise so
    # the gap is actionable instead of being mislabeled as none expected.
    if (
        collection_status.get("status") in {"fetch-failed", "empty-response"}
        and collection_status.get("via") == "fide-event-id"
    ):
        advertised_keys.update(played_keys)
    if unresolved_pairings and not played:
        availability = "unresolved-pairings"
        scope = "coverage-unresolved"
    elif not played:
        availability = "no-played-games" if non_bye else "no-pairings"
        scope = "none-published"
    elif not advertised_keys:
        availability = "not-published"
        scope = "none-published"
    elif len(advertised_keys) < len(played_keys):
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
    if availability == "unresolved-pairings":
        archive_status = "coverage-unresolved"
    elif availability in ("not-published", "no-pairings", "no-played-games"):
        if archive_games or local_fingerprints:
            archive_status = "external-or-legacy-supplement"
        else:
            archive_status = "none-expected"
    elif archive_games:
        if matched == 0:
            archive_status = "archived-unmatched"
        elif is_matched_full:
            archive_status = "matched-full"
        elif is_matched_advertised and len(advertised_keys) < len(played_keys):
            archive_status = "matched-advertised-complete"
        else:
            archive_status = "matched-partial"
    elif local_fingerprints:
        archive_status = "locally-recoverable"
    else:
        archive_status = "missing"
    if (
        (unresolved_pairings or not lichess_scope_verified)
        and archive_status in {"matched-full", "matched-advertised-complete"}
    ):
        archive_status = "matched-partial"

    # 分子限制在对应的精确匹配子集中，防范 coverage > 1
    advertised_coverage = round(len(match["matchedAdvertisedKeys"]) / len(advertised_keys), 4) if advertised_keys else None
    played_coverage = round(len(match["matchedPlayedKeys"]) / len(played), 4) if played else None

    if unresolved_pairings:
        pgn_ingest_status = "coverage-unresolved"
    elif lichess_games and not lichess_scope_verified:
        pgn_ingest_status = "source-published-coverage-unresolved"
    elif availability in ("no-pairings", "no-played-games"):
        pgn_ingest_status = "not-applicable"
    elif availability == "unresolved-pairings":
        pgn_ingest_status = "coverage-unresolved"
    elif availability == "not-published":
        pgn_ingest_status = "not-published"
    elif is_matched_advertised:
        pgn_ingest_status = "full-board-complete" if is_matched_full else "source-published-complete"
    elif match["matchedAdvertisedKeys"]:
        pgn_ingest_status = "source-published-partial"
    else:
        pgn_ingest_status = "source-published-missing"

    publishable = results_status == "results-complete"

    # 判定 playableComplete：所有 played expected 都能被 FIDE by-player 索引覆盖
    playable_pairings_count = 0
    for round_row in rounds:
        rid = round_number(round_row.get("round"))
        for pairing in round_row.get("pairings") or []:
            if is_bye(pairing) or is_unresolved_pairing(pairing):
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
        "pgnIngestStatus": pgn_ingest_status,
        "pgnArchiveSources": archive_sources,
        "pgnSourceStatus": collection_status.get("status") or "not-attempted",
        "pgnSourceErrorCode": collection_status.get("errorCode") or None,
        "pgnLastAttemptedAt": collection_status.get("attemptedAt") or None,
        "publicArchiveVerified": public_archive_verified,
        "offlineRematchEvidenceVerified": reviewed_rematch,
        "playableComplete": playable_complete,
        "eventComplete": event_complete,
        "counts": {
            "players": len(players),
            "standings": len(standings),
            "roundsExpected": round_count or None,
            "roundsCaptured": len(rounds),
            "pairings": len(pairings),
            "unresolvedPairings": len(unresolved_pairings),
            "nonByePairings": len(non_bye),
            "playedGames": len(played),
            "forfeits": forfeits,
            "advertisedPGN": len(advertised_keys),
            "lichessBroadcastGames": len(lichess_games),
            "lichessUnmatchedResidual": lichess_residual,
            "lichessIncompleteResidual": lichess_incomplete,
            "archivedGames": len(archive_games),
            "matchedPairings": matched,
            "localGameFingerprints": len(local_fingerprints),
        },
        "advertisedCoverage": advertised_coverage,
        "allBoardCoverage": played_coverage,
        "lichessScopeVerified": lichess_scope_verified,
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
        if counts.get("unresolvedPairings"):
            priority, action = "P0", "repair-pairing-player-numbers"
        elif counts.get("lichessUnmatchedResidual"):
            if counts.get("lichessIncompleteResidual") == counts.get("lichessUnmatchedResidual"):
                priority, action = "P0", "review-incomplete-lichess-records"
            else:
                priority, action = "P0", "offline-rematch-lichess-residual"
        elif counts.get("lichessBroadcastGames") and report.get("lichessScopeVerified") is False:
            priority, action = "P0", "audit-lichess-broadcast-scope"
        elif archive in ("archived-unmatched", "matched-partial"):
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
            priority, action = "P1", "offline-restore-from-canonical-game-facts"
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
    games_index, game_fact_input = player_game_fact_index()
    public_archives = verified_event_archives() | verified_lichess_event_archives()
    lichess_statuses = lichess_event_metadata()
    status_payload = read_json_optional(COLLECTION_STATUS, {})
    collection_statuses = status_payload.get("events") if isinstance(status_payload.get("events"), dict) else {}
    offline_rematch_ids = verified_offline_rematch_ids()
    reports = []
    for path in sorted(DETAILS.glob("tnr*.json")):
        payload = read_json_required(path)
        tid = clean(payload.get("tournamentID"))
        if not tid:
            continue
        reports.append(event_report(
            payload,
            games_index.get(tid, {}),
            collection_statuses.get(tid, {}),
            tid in public_archives,
            lichess_statuses.get(tid, {}),
            offline_rematch_ids,
        ))
    summary = {
        "events": len(reports),
        "resultsComplete": sum(1 for r in reports if r["resultsStatus"] == "results-complete"),
        "resultsUnknown": sum(1 for r in reports if r["resultsStatus"] == "unknown"),
        "publishable": sum(1 for r in reports if r["publishable"]),
        "eventComplete": sum(1 for r in reports if r["eventComplete"]),
        "pgnAvailability": {},
        "archiveStatus": {},
        "pgnIngestStatus": {},
        "pgnSourceStatus": {},
    }
    for report in reports:
        summary["pgnAvailability"][report["pgnAvailability"]] = summary["pgnAvailability"].get(report["pgnAvailability"], 0) + 1
        summary["archiveStatus"][report["archiveStatus"]] = summary["archiveStatus"].get(report["archiveStatus"], 0) + 1
        summary["pgnIngestStatus"][report["pgnIngestStatus"]] = summary["pgnIngestStatus"].get(report["pgnIngestStatus"], 0) + 1
        summary["pgnSourceStatus"][report["pgnSourceStatus"]] = summary["pgnSourceStatus"].get(report["pgnSourceStatus"], 0) + 1
    return {
        "schemaVersion": 2,
        "generatedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "factInputs": {"playerGames": game_fact_input},
        "summary": summary,
        "events": reports,
    }


def main() -> int:
    previous = read_json_optional(OUTPUT, {})
    visible = len(list(DETAILS.glob("tnr*.json")))
    committed = len(previous.get("events") or [])
    if committed and visible < committed:
        raise SystemExit(
            "COMPLETENESS_INPUT_INCOMPLETE: structured event inputs are fewer than "
            f"the committed report ({visible} < {committed}); refusing warm-output fallback"
        )

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
