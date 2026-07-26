#!/usr/bin/env python3
"""Measure domestic identity projection quality against embedded FIDE truth.

The validator never creates identity links. It evaluates the display-only
projection produced by ``sync_domestic_players.py`` using event roster rows
that already carry a FIDE ID, writes aggregate-only metrics, and fails the
snapshot when precision or hard-conflict invariants regress.
"""

from __future__ import annotations

import argparse
import datetime as dt
import itertools
import json
import pathlib
import re
from collections import Counter, defaultdict
from typing import Any

from stable_json import write_json
from sync_domestic_players import (
    IDENTITY_CLUSTER_RULE_VERSION,
)


ROOT = pathlib.Path(__file__).resolve().parents[1]
EVENT_DETAILS = ROOT / "docs" / "data" / "index" / "event-details"
DOMESTIC_PLAYERS = ROOT / "data" / "generated" / "domestic-players-full.json"
PRESENTATION_GROUPS = (
    ROOT / "docs" / "data" / "registry" / "domestic" / "presentation-groups.json"
)
OUTPUT = ROOT / "docs" / "data" / "registry" / "domestic" / "identity-quality.json"
CHINESE_NAME_RE = re.compile(r"[\u4e00-\u9fff]{2,6}")

THRESHOLDS = {
    "chineseNamePrecisionMin": 0.997,
    "chineseNameRecallMin": 0.98,
    "projectionPrecisionMin": 0.995,
    "projectionEvaluatedEdgesMin": 100,
    "projectionHardConflictViolationsMax": 0,
    "hardConflictFalseSplitRateMax": 0.005,
}


def choose2(value: int) -> int:
    return value * (value - 1) // 2


def normalized_name(value: Any) -> str:
    return re.sub(
        r"[^0-9a-z\u4e00-\u9fff]",
        "",
        str(value or "").strip().casefold(),
    )


def event_key(value: Any) -> str:
    match = re.search(r"(\d+)$", str(value or "").strip())
    return match.group(1) if match else str(value or "").strip().casefold()


def chinese_name(row: dict[str, Any]) -> str:
    for key in ("chineseName", "displayName", "name"):
        value = str(row.get(key) or "").strip()
        if CHINESE_NAME_RE.fullmatch(value):
            return value
    return ""


def merged_event_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for collection in ("players", "standings"):
        for index, row in enumerate(payload.get(collection) or []):
            player_no = str(row.get("playerNo") or "").strip()
            key = player_no or f"{collection}-{index}"
            target = merged.setdefault(key, {"playerNo": player_no})
            for field, value in row.items():
                if value not in (None, "", [], {}):
                    if (
                        field == "fideID"
                        and target.get(field) not in (None, "")
                        and str(target[field]).strip() != str(value).strip()
                    ):
                        target["_ambiguousFideID"] = True
                    target[field] = value
    return list(merged.values())


def load_truth(
    event_root: pathlib.Path,
) -> tuple[list[dict[str, str]], dict[tuple[str, str], str]]:
    observations: list[dict[str, str]] = []
    roster_truth: dict[tuple[str, str], str] = {}
    ambiguous_roster_keys: set[tuple[str, str]] = set()
    for path in sorted(event_root.glob("tnr*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        tournament_id = event_key(payload.get("tournamentID") or path.stem)
        for row in merged_event_rows(payload):
            if row.get("_ambiguousFideID"):
                continue
            fide_id = str(row.get("fideID") or "").strip()
            if not fide_id:
                continue
            player_no = str(row.get("playerNo") or "").strip()
            raw_name = (
                chinese_name(row)
                or str(row.get("displayName") or row.get("name") or "").strip()
            )
            name_key = normalized_name(raw_name)
            if not name_key:
                continue
            observations.append({
                "eventID": tournament_id,
                "playerNo": player_no,
                "fideID": fide_id,
                "nameKey": name_key,
                "chineseName": chinese_name(row),
            })
            if player_no:
                lookup = (tournament_id, player_no)
                if lookup in ambiguous_roster_keys:
                    continue
                previous = roster_truth.get(lookup)
                if previous and previous != fide_id:
                    # Ambiguous event rows are unusable as truth.
                    roster_truth.pop(lookup, None)
                    ambiguous_roster_keys.add(lookup)
                elif lookup not in roster_truth:
                    roster_truth[lookup] = fide_id
    return observations, roster_truth


def pairwise_name_metrics(observations: list[dict[str, str]]) -> dict[str, Any]:
    by_name: dict[str, Counter[str]] = defaultdict(Counter)
    by_fide: Counter[str] = Counter()
    for row in observations:
        by_name[row["nameKey"]][row["fideID"]] += 1
        by_fide[row["fideID"]] += 1
    predicted_pairs = sum(choose2(sum(counts.values())) for counts in by_name.values())
    correct_pairs = sum(
        choose2(count)
        for counts in by_name.values()
        for count in counts.values()
    )
    truth_pairs = sum(choose2(count) for count in by_fide.values())
    ambiguous_names = sum(len(counts) > 1 for counts in by_name.values())
    affected_players = {
        fide_id
        for counts in by_name.values()
        if len(counts) > 1
        for fide_id in counts
    }
    return {
        "observations": len(observations),
        "players": len(by_fide),
        "names": len(by_name),
        "ambiguousNames": ambiguous_names,
        "affectedPlayers": len(affected_players),
        "predictedPairs": predicted_pairs,
        "correctPairs": correct_pairs,
        "truthPairs": truth_pairs,
        "precision": round(correct_pairs / predicted_pairs, 6)
        if predicted_pairs
        else 1.0,
        "recall": round(correct_pairs / truth_pairs, 6) if truth_pairs else 1.0,
    }


def truth_ids_for_players(
    players: list[dict[str, Any]],
    roster_truth: dict[tuple[str, str], str],
) -> tuple[dict[str, str], int]:
    result: dict[str, str] = {}
    ambiguous = 0
    for player in players:
        domestic_id = str(player.get("domesticID") or "").strip()
        ids = {
            roster_truth[(event_key(sighting.get("eventID")), str(sighting.get("playerNo") or "").strip())]
            for sighting in player.get("sightings") or []
            if (
                event_key(sighting.get("eventID")),
                str(sighting.get("playerNo") or "").strip(),
            )
            in roster_truth
        }
        if len(ids) == 1 and domestic_id:
            result[domestic_id] = next(iter(ids))
        elif len(ids) > 1:
            ambiguous += 1
    return result, ambiguous


def payload_age_continuity(player: dict[str, Any]) -> list[tuple[str, str]]:
    return [
        (
            str(sighting.get("eventDate") or ""),
            str(sighting.get("ageStage") or ""),
        )
        for sighting in player.get("sightings") or []
    ]


def age_continuity_from_payloads(
    left: dict[str, Any],
    right: dict[str, Any],
) -> str:
    observations: list[tuple[str, int]] = []
    for event_date, age_stage in [
        *payload_age_continuity(left),
        *payload_age_continuity(right),
    ]:
        stage_match = re.fullmatch(r"U\s*(\d{1,2})", age_stage, flags=re.IGNORECASE)
        year_match = re.match(r"(\d{4})", event_date)
        if stage_match and year_match:
            observations.append((event_date, int(stage_match.group(1))))
    if len(observations) < 2:
        return "unknown"
    stages = [stage for _date, stage in sorted(observations)]
    return (
        "consistent"
        if all(current >= previous for previous, current in zip(stages, stages[1:]))
        else "conflict"
    )


def payload_hard_conflicts(
    left: dict[str, Any],
    right: dict[str, Any],
) -> list[str]:
    left_sightings = left.get("sightings") or []
    right_sightings = right.get("sightings") or []
    conflicts: list[str] = []
    left_events = {event_key(row.get("eventID")) for row in left_sightings if row.get("eventID")}
    right_events = {event_key(row.get("eventID")) for row in right_sightings if row.get("eventID")}
    if left_events & right_events:
        conflicts.append("concurrent-event")
    left_birth = {row.get("birthYear") for row in left_sightings if row.get("birthYear")}
    right_birth = {row.get("birthYear") for row in right_sightings if row.get("birthYear")}
    if left_birth and right_birth and left_birth.isdisjoint(right_birth):
        conflicts.append("birth-year-conflict")
    if age_continuity_from_payloads(left, right) == "conflict":
        conflicts.append("age-stage-conflict")
    return conflicts


def projection_metrics(
    players: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    truth_by_domestic: dict[str, str],
) -> dict[str, Any]:
    by_id = {
        str(player.get("domesticID") or ""): player
        for player in players
        if player.get("domesticID")
    }
    membership: dict[str, str] = {}
    duplicate_memberships = 0
    evaluated = correct = hard_violations = incorrect_groups = 0
    for group in groups:
        members = [
            str(member)
            for member in group.get("members") or []
            if str(member) in by_id
        ]
        token = (
            f"fide:{group.get('canonicalFideID')}"
            if group.get("canonicalFideID")
            else f"group:{group.get('groupID')}"
        )
        for member in members:
            if member in membership and membership[member] != token:
                duplicate_memberships += 1
            membership[member] = token
        group_wrong = False
        for left_id, right_id in itertools.combinations(members, 2):
            if payload_hard_conflicts(by_id[left_id], by_id[right_id]):
                hard_violations += 1
        canonical_fide = str(group.get("canonicalFideID") or "").strip()
        if canonical_fide:
            for member in members:
                truth = truth_by_domestic.get(member)
                if not truth:
                    continue
                evaluated += 1
                if truth == canonical_fide:
                    correct += 1
                else:
                    group_wrong = True
        else:
            for left_id, right_id in itertools.combinations(members, 2):
                left_truth = truth_by_domestic.get(left_id)
                right_truth = truth_by_domestic.get(right_id)
                if not left_truth or not right_truth:
                    continue
                evaluated += 1
                if left_truth == right_truth:
                    correct += 1
                else:
                    group_wrong = True
        incorrect_groups += group_wrong

    by_truth: dict[str, list[str]] = defaultdict(list)
    for domestic_id, fide_id in truth_by_domestic.items():
        by_truth[fide_id].append(domestic_id)
    truth_pairs = recovered_pairs = 0
    for members in by_truth.values():
        for left_id, right_id in itertools.combinations(members, 2):
            truth_pairs += 1
            if membership.get(left_id) and membership.get(left_id) == membership.get(right_id):
                recovered_pairs += 1
    return {
        "groups": len(groups),
        "groupedEntities": sum(len(group.get("members") or []) for group in groups),
        "truthMappedEntities": len(truth_by_domestic),
        "evaluatedEdges": evaluated,
        "correctEdges": correct,
        "incorrectGroups": incorrect_groups,
        "precision": round(correct / evaluated, 6) if evaluated else 1.0,
        "truthPairs": truth_pairs,
        "recoveredTruthPairs": recovered_pairs,
        "recall": round(recovered_pairs / truth_pairs, 6) if truth_pairs else 1.0,
        "hardConflictViolations": hard_violations,
        "duplicateMemberships": duplicate_memberships,
    }


def hard_conflict_split_metrics(
    players: list[dict[str, Any]],
    truth_by_domestic: dict[str, str],
) -> dict[str, Any]:
    by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for player in players:
        key = normalized_name(
            player.get("chineseName")
            or player.get("pinyin")
            or player.get("displayName")
        )
        if key:
            by_name[key].append(player)
    evaluated = false_splits = 0
    reasons: Counter[str] = Counter()
    for members in by_name.values():
        for left, right in itertools.combinations(members, 2):
            conflicts = payload_hard_conflicts(left, right)
            if not conflicts:
                continue
            left_truth = truth_by_domestic.get(str(left.get("domesticID") or ""))
            right_truth = truth_by_domestic.get(str(right.get("domesticID") or ""))
            if not left_truth or not right_truth:
                continue
            evaluated += 1
            reasons.update(conflicts)
            false_splits += left_truth == right_truth
    return {
        "evaluatedPairs": evaluated,
        "falseSplitPairs": false_splits,
        "falseSplitRate": round(false_splits / evaluated, 6) if evaluated else 0.0,
        "reasonCounts": dict(sorted(reasons.items())),
    }


def validate_report(report: dict[str, Any]) -> list[str]:
    chinese = report["truthBenchmark"]["chineseNames"]
    projection = report["projection"]
    splits = report["hardConflictSplits"]
    failures: list[str] = []
    if chinese["precision"] < THRESHOLDS["chineseNamePrecisionMin"]:
        failures.append(f"Chinese-name precision {chinese['precision']:.4f} is below threshold")
    if chinese["recall"] < THRESHOLDS["chineseNameRecallMin"]:
        failures.append(f"Chinese-name recall {chinese['recall']:.4f} is below threshold")
    if projection["evaluatedEdges"] < THRESHOLDS["projectionEvaluatedEdgesMin"]:
        failures.append("too few projection edges carry embedded FIDE truth")
    if projection["precision"] < THRESHOLDS["projectionPrecisionMin"]:
        failures.append(f"projection precision {projection['precision']:.4f} is below threshold")
    if projection["hardConflictViolations"] > THRESHOLDS["projectionHardConflictViolationsMax"]:
        failures.append("a published identity group contains a hard-conflict pair")
    if projection["duplicateMemberships"]:
        failures.append("a domestic entity belongs to multiple identity groups")
    if splits["falseSplitRate"] >= THRESHOLDS["hardConflictFalseSplitRateMax"]:
        failures.append(f"hard-conflict false-split rate {splits['falseSplitRate']:.4f} is too high")
    return failures


def build_report(
    event_root: pathlib.Path,
    players_path: pathlib.Path,
    groups_path: pathlib.Path,
) -> dict[str, Any]:
    observations, roster_truth = load_truth(event_root)
    chinese_observations = [
        {**row, "nameKey": normalized_name(row["chineseName"])}
        for row in observations
        if row["chineseName"]
    ]
    players = json.loads(players_path.read_text(encoding="utf-8"))
    groups_payload = json.loads(groups_path.read_text(encoding="utf-8"))
    groups = groups_payload.get("groups") or []
    truth_by_domestic, ambiguous_entities = truth_ids_for_players(players, roster_truth)
    report = {
        "schemaVersion": 1,
        "snapshotId": groups_payload.get("snapshotId"),
        "generatedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "algorithmVersion": IDENTITY_CLUSTER_RULE_VERSION,
        "truthBenchmark": {
            "allNames": pairwise_name_metrics(observations),
            "chineseNames": pairwise_name_metrics(chinese_observations),
            "ambiguousDomesticEntitiesExcluded": ambiguous_entities,
        },
        "projection": projection_metrics(players, groups, truth_by_domestic),
        "hardConflictSplits": hard_conflict_split_metrics(players, truth_by_domestic),
        "thresholds": THRESHOLDS,
    }
    failures = validate_report(report)
    report["status"] = "failed" if failures else "passed"
    report["failures"] = failures
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-details", type=pathlib.Path, default=EVENT_DETAILS)
    parser.add_argument("--players", type=pathlib.Path, default=DOMESTIC_PLAYERS)
    parser.add_argument("--groups", type=pathlib.Path, default=PRESENTATION_GROUPS)
    parser.add_argument("--output", type=pathlib.Path, default=OUTPUT)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args()
    report = build_report(args.event_details, args.players, args.groups)
    if not args.check_only:
        write_json(args.output, report, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "passed" or args.report_only else 1


if __name__ == "__main__":
    raise SystemExit(main())
