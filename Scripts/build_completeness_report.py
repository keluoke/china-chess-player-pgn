#!/usr/bin/env python3
"""Build the per-event CompletenessReport (multi-dimensional gates).

This is the single place that decides what an event's capture actually
covers.  It replaces the old single-valued ``captureStatus == complete``
semantics as the publication gate:

- ``resultsStatus``   roster / standings / rounds / pairings / results gates
- ``pgnAvailability`` what the source actually advertised (bye-excluded)
- ``archiveStatus``   what we archived + matched against pairings
- ``publishable``     results-complete events may enter public projections,
                      clearly labelled; only ``archived-full-board`` events
                      may ever be called "棋谱完整" on any surface.

Inputs are maintainer-local machine artifacts (private capture details and
event PGN archives) plus the published by-player index.  The report itself
contains counts, statuses and reasons only — never source URLs — so it is
committed under ``data/generated/`` and consumed by derived builders and CI.

PGN denominator contract (plan §5.3): ``pgnExpected`` counts the non-bye
games the source actually advertised; ``allBoardCoverage`` is measured
against every non-bye pairing separately.  The two are never mixed into a
single percentage.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
from collections import defaultdict
from typing import Any

from stable_json import write_json

ROOT = pathlib.Path(__file__).resolve().parents[1]
DETAILS = ROOT / "data" / "generated" / "chess-results-event-details"
EVENT_PGN = ROOT / "data" / "generated" / "chess-results-event-pgn"
BY_PLAYER = ROOT / "docs" / "data" / "index" / "by-player"
OUTPUT = ROOT / "data" / "generated" / "event-completeness-report.json"
QUEUE_OUTPUT = ROOT / "data" / "generated" / "pgn-supplement-queue.json"

# Team-format sections report match points ("2", "2½"), so any digit counts
# as a result core; letters (e.g. a federation code shifted into the result
# column) still mark the row invalid.
RESULT_CORE_RE = re.compile(r"[0-9½+\-]")
RESULT_LETTER_RE = re.compile(r"[A-JL-Za-jl-z]")


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


def is_bye(pairing: dict[str, Any]) -> bool:
    white = pairing.get("white") or {}
    black = pairing.get("black") or {}
    return not clean(white.get("playerNo")) or not clean(black.get("playerNo"))


def advertised_pgn(pairing: dict[str, Any]) -> bool:
    return bool(pairing.get("hasPGN")) or bool(clean(pairing.get("pgnURL")))


def valid_result(pairing: dict[str, Any]) -> bool:
    result = clean(pairing.get("result"))
    return bool(result) and bool(RESULT_CORE_RE.search(result)) and not RESULT_LETTER_RE.search(result)


def archived_game_count(tid: str) -> int:
    path = EVENT_PGN / f"tnr{tid}.pgn"
    if not path.is_file():
        return 0
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    return text.count("[Event ")


def by_player_game_index() -> dict[str, dict[tuple[str, tuple[str, str]], set[str]]]:
    """tid -> {(round, sorted-normalized-names): {game fingerprints}} plus totals."""
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


def event_report(payload: dict[str, Any], games: dict[tuple[str, tuple[str, str]], set[str]]) -> dict[str, Any]:
    tid = clean(payload.get("tournamentID"))
    players = payload.get("players") or []
    standings = payload.get("standings") or []
    rounds = payload.get("rounds") or []
    round_count = int(payload.get("roundCount") or 0)
    capture_status = clean(payload.get("captureStatus")) or "complete"

    pairings = [p for r in rounds for p in (r.get("pairings") or [])]
    non_bye = [p for p in pairings if not is_bye(p)]
    advertised = [p for p in non_bye if advertised_pgn(p)]
    valid_results = sum(1 for p in non_bye if valid_result(p))

    matched_keys: set[tuple[str, tuple[str, str]]] = set()
    fingerprints: set[str] = set()
    for round_row in rounds:
        rid = round_number(round_row.get("round"))
        for pairing in round_row.get("pairings") or []:
            if is_bye(pairing):
                continue
            key = (rid, tuple(sorted([
                normalize_name((pairing.get("white") or {}).get("name")),
                normalize_name((pairing.get("black") or {}).get("name")),
            ])))
            if key in games:
                matched_keys.add(key)
    for prints in games.values():
        fingerprints.update(prints)

    matched = len(matched_keys)
    archived = archived_game_count(tid)
    local_games = len(fingerprints)

    gates = {
        "roster": {"expected": len(players), "actual": len(players)},
        "standings": {"expected": len(players) if payload.get("format") != "team" else len(standings),
                      "actual": len(standings)},
        "rounds": {"expected": round_count or len(rounds), "actual": len(rounds)},
        "pairings": {"expected": len(pairings), "actual": len(pairings)},
        "results": {"expected": len(non_bye), "valid": valid_results},
        "pgnExpected": {"expected": len(advertised), "archived": min(archived, len(advertised)) if advertised else 0},
        "pgnMatched": {"expected": len(advertised), "matched": matched},
    }

    results_complete = (
        capture_status == "complete"
        and len(standings) > 0
        and len(rounds) > 0
        and len(rounds) >= (round_count or len(rounds))
        and len(non_bye) > 0
        and valid_results == len(non_bye)
    )
    results_status = "results-complete" if results_complete else "partial"

    # --- source-advertised availability (bye-excluded denominator) ----------
    if not non_bye:
        availability = "no-pairings"
        scope = "none-published"
    elif not advertised:
        availability = "not-published"
        scope = "none-published"
    elif len(advertised) < len(non_bye):
        availability = "advertised-partial"
        scope = "selected-live-boards"
    else:
        availability = "advertised-full"
        scope = "all-non-bye-boards"

    # --- archive/matching state against what was advertised -----------------
    if availability in ("not-published", "no-pairings"):
        if local_games:
            archive_status = "external-or-legacy-supplement"
        else:
            archive_status = "none-expected"
    elif matched >= len(non_bye) and len(non_bye) > 0:
        archive_status = "archived-full-board"
    elif advertised and matched >= len(advertised):
        archive_status = "archived-advertised-complete"
    elif archived == 0 and local_games:
        archive_status = "locally-recoverable"
    elif archived == 0:
        archive_status = "missing"
    else:
        archive_status = "incomplete"

    advertised_coverage = round(matched / len(advertised), 4) if advertised else None
    all_board_coverage = round(matched / len(non_bye), 4) if non_bye else None

    publishable = results_complete
    event_complete = results_complete and archive_status == "archived-full-board"

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
        "counts": {
            "players": len(players),
            "standings": len(standings),
            "roundsExpected": round_count,
            "roundsCaptured": len(rounds),
            "pairings": len(pairings),
            "nonByePairings": len(non_bye),
            "advertisedPGN": len(advertised),
            "archivedGames": archived,
            "matchedPairings": matched,
            "localGameFingerprints": local_games,
        },
        "advertisedCoverage": advertised_coverage,
        "allBoardCoverage": all_board_coverage,
        "publishable": publishable,
        "eventComplete": event_complete,
    }


def supplement_queue(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Maintainer PGN backlog with explicit priorities (plan §5.3)."""
    queue: list[dict[str, Any]] = []
    for report in reports:
        availability = report["pgnAvailability"]
        archive = report["archiveStatus"]
        counts = report["counts"]
        action = priority = None
        if availability in ("advertised-full", "advertised-partial") and archive in ("missing", "incomplete"):
            priority, action = "P0", "re-fetch-or-rematch-advertised-boards"
        elif archive == "locally-recoverable":
            priority, action = "P1", "offline-restore-from-by-player-packs"
        elif availability == "advertised-partial" and archive == "archived-advertised-complete":
            continue  # promised boards done; nothing to supplement
        elif availability == "not-published" and archive == "external-or-legacy-supplement":
            priority, action = "P1", "verify-external-supplement-coverage"
        elif availability == "not-published":
            priority, action = "P2", "await-external-lead-no-refetch"
        else:
            continue
        queue.append({
            "tournamentID": report["tournamentID"],
            "priority": priority,
            "nextAction": action,
            "resultsStatus": report["resultsStatus"],
            "pgnAvailability": availability,
            "archiveStatus": archive,
            "nonByePairings": counts["nonByePairings"],
            "advertisedPGN": counts["advertisedPGN"],
            "archivedGames": counts["archivedGames"],
            "matchedPairings": counts["matchedPairings"],
            "localGameFingerprints": counts["localGameFingerprints"],
        })
    order = {"P0": 0, "P1": 1, "P2": 2}
    queue.sort(key=lambda row: (order.get(row["priority"], 9), row["tournamentID"]))
    return queue


def build() -> dict[str, Any]:
    games_index = by_player_game_index()
    reports = []
    for path in sorted(DETAILS.glob("tnr*.json")):
        payload = read_json(path, {})
        tid = clean(payload.get("tournamentID"))
        if not tid:
            continue
        reports.append(event_report(payload, games_index.get(tid, {})))
    summary = {
        "events": len(reports),
        "resultsComplete": sum(1 for r in reports if r["resultsStatus"] == "results-complete"),
        "publishable": sum(1 for r in reports if r["publishable"]),
        "eventComplete": sum(1 for r in reports if r["eventComplete"]),
        "pgnAvailability": {},
        "archiveStatus": {},
    }
    for report in reports:
        summary["pgnAvailability"][report["pgnAvailability"]] = summary["pgnAvailability"].get(report["pgnAvailability"], 0) + 1
        summary["archiveStatus"][report["archiveStatus"]] = summary["archiveStatus"].get(report["archiveStatus"], 0) + 1
    return {
        "schemaVersion": 1,
        "generatedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "summary": summary,
        "events": reports,
    }


def main() -> int:
    import os

    # Shrink guard: the private capture layer lives on the maintainer machine
    # and is only partially tracked in Git. A rebuild environment that sees
    # fewer capture files than the committed report (e.g. GitHub Actions)
    # must keep the committed report instead of silently shrinking the
    # publishable set. Maintainers can force a shrink deliberately.
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
    queue = supplement_queue(report["events"])
    write_json(QUEUE_OUTPUT, {
        "schemaVersion": 1,
        "generatedAt": report["generatedAt"],
        "totals": {"tasks": len(queue)},
        "tasks": queue,
    }, ensure_ascii=False, indent=2)
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
