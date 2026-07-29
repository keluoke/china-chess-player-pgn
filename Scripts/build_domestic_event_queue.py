#!/usr/bin/env python3
"""Build the demand-driven domestic event ingestion queue (offline only).

The queue joins the reviewed/manual source catalog with the existing
Chess-Results target tables. It never scrapes and never changes identity links.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import pathlib
import re
from typing import Any

from event_targeting import SUPPRESS_ACTIONS, target_overrides
from stable_json import write_json


ROOT = pathlib.Path(__file__).resolve().parents[1]
STARTING_RANK = ROOT / "data" / "manual" / "chess-results-starting-rank-sources.csv"
MASTER_GROUPS = ROOT / "data" / "community" / "master-tournament-groups.csv"
SOURCE_CATALOG = ROOT / "data" / "manual" / "domestic-source-catalog.csv"
DEMAND_GAPS = ROOT / "data" / "manual" / "data-demand-gaps.csv"
EVENT_DETAILS = ROOT / "data" / "generated" / "chess-results-event-details"
PUBLIC_EVENTS = ROOT / "docs" / "data" / "index" / "public-events.json"
OUTPUT = ROOT / "data" / "generated" / "audit" / "domestic-event-queue.json"
DEMAND_OUTPUT = ROOT / "docs" / "data" / "audit" / "demand-queue.json"
SUMMARY_OUTPUT = ROOT / "docs" / "data" / "audit" / "event-queue-summary.json"


def clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def rows(path: pathlib.Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [{key: clean(value) for key, value in row.items()} for row in csv.DictReader(handle)]


def tnr(value: Any) -> str:
    match = re.search(r"(?:tnr)?(\d{5,9})", clean(value), flags=re.IGNORECASE)
    return match.group(1) if match else ""


def category_priority(category: str, name: str) -> tuple[str, int]:
    text = f"{category} {name}".casefold()
    if any(term in text for term in ("li-chengzhi", "李成智", "全国少年儿童", "national youth")):
        return "li-chengzhi", 300
    if any(term in text for term in ("master", "棋协大师")):
        return "master-tournament", 200
    if any(term in text for term in ("provincial", "省", "市青少年")):
        return "provincial-youth", 100
    return category or "other-domestic", 50


def detail_state(tournament_id: str) -> dict[str, Any]:
    path = EVENT_DETAILS / f"tnr{tournament_id}.json"
    if not path.exists():
        return {"ingestionStatus": "registered", "snapshotAudited": False}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"ingestionStatus": "needs-review", "snapshotAudited": False}
    snapshots = payload.get("sourceSnapshots") or []
    return {
        "ingestionStatus": "captured" if payload.get("players") and payload.get("standings") else "roster-missing",
        "snapshotAudited": bool(snapshots) and all(item.get("sha256") for item in snapshots),
        "capturedPlayers": len(payload.get("players") or []),
        "capturedStandings": len(payload.get("standings") or []),
        "capturedRounds": len(payload.get("rounds") or []),
        "lastCapturedAt": payload.get("fetchedAt"),
    }


def demand_index() -> tuple[dict[str, int], list[dict[str, Any]]]:
    by_tnr: dict[str, int] = {}
    queue: list[dict[str, Any]] = []
    for row in rows(DEMAND_GAPS):
        if row.get("status", "open") not in {"", "open", "queued"}:
            continue
        count = max(1, int(row.get("demand_count") or 1))
        tournament_id = tnr(row.get("tournament_id") or row.get("display_query"))
        if tournament_id:
            by_tnr[tournament_id] = by_tnr.get(tournament_id, 0) + count
        queue.append({
            "gapID": row.get("gap_id"),
            "queryType": row.get("query_type") or ("event" if tournament_id else "player"),
            "displayQuery": row.get("display_query"),
            "tournamentID": tournament_id or None,
            "demandCount": count,
            "lastRequestedAt": row.get("last_requested_at") or None,
            "status": row.get("status") or "open",
        })
    queue.sort(key=lambda item: (-item["demandCount"], item.get("lastRequestedAt") or "", item.get("displayQuery") or ""))
    return by_tnr, queue


def build() -> tuple[dict[str, Any], dict[str, Any]]:
    targets: dict[str, dict[str, Any]] = {}

    def upsert(tournament_id: str, **values: Any) -> None:
        if not tournament_id:
            return
        current = targets.setdefault(tournament_id, {"tournamentID": tournament_id, "source": "Chess-Results"})
        discovered_by = values.pop("discoveredBy", [])
        sources = current.setdefault("discoveredBy", [])
        for source in discovered_by:
            if source and source not in sources:
                sources.append(source)
        for key, value in values.items():
            if value not in (None, "", [], {}):
                current[key] = value

    for row in rows(STARTING_RANK):
        tournament_id = tnr(row.get("tournament_id") or row.get("url"))
        category, priority = category_priority(row.get("category", ""), row.get("notes", ""))
        upsert(tournament_id, sourceURL=row.get("url"), eventName=row.get("notes"), category=category, basePriority=priority, discoveredBy=["manual-starting-rank"])

    for row in rows(MASTER_GROUPS):
        tournament_id = tnr(row.get("tournament_id"))
        upsert(
            tournament_id,
            sourceURL=row.get("source_url"),
            eventName=" · ".join(filter(None, [row.get("year"), row.get("station"), row.get("group_code")])),
            category="master-tournament",
            canonicalEventID=row.get("canonical_event_id"),
            sectionID=row.get("section_id"),
            basePriority=200,
            refreshTier="daily-during-event",
            discoveredBy=["community-master-group"],
        )

    for row in rows(SOURCE_CATALOG):
        tournament_id = tnr(row.get("tournament_id") or row.get("official_url"))
        category, inferred = category_priority(row.get("event_type", ""), row.get("event_name", ""))
        try:
            manual_priority = int(row.get("priority") or inferred)
        except ValueError:
            manual_priority = inferred
        upsert(
            tournament_id,
            sourceID=row.get("source_id"),
            sourceURL=row.get("official_url"),
            eventName=row.get("event_name"),
            category=category,
            ageGroup=row.get("age_group"),
            catalogStatus=row.get("status") or "registered",
            redistributable=row.get("redistributable"),
            basePriority=manual_priority,
            refreshTier=row.get("refresh_tier"),
            discoveredBy=["manual-source-catalog"],
        )

    # Close the public projection loop offline: a catalogued TNR without a
    # publishable detail/roster becomes a maintainer-local capture target.
    # Duplicate source/PGN rows have already been coalesced by the catalog,
    # so an existing good detail never causes a redundant source request.
    try:
        public_events = json.loads(PUBLIC_EVENTS.read_text(encoding="utf-8")).get("events") or []
    except (OSError, json.JSONDecodeError):
        public_events = []
    public_gap_priorities = {
        "lichengzhi-cup": 320,
        "chess-association-master": 240,
        "world-youth": 180,
        "asian-youth": 180,
    }
    for event in public_events:
        tournament_id = tnr(event.get("tournamentID"))
        if not tournament_id or event.get("detailPath"):
            continue
        upsert(
            tournament_id,
            eventName=event.get("displayName"),
            category=event.get("series") or "public-event-detail-gap",
            basePriority=public_gap_priorities.get(event.get("series"), 150),
            refreshTier="until-results-complete",
            publicDetailMissing=True,
            discoveredBy=["public-detail-gap"],
        )

    demand_by_tnr, demand_rows = demand_index()
    for gap in demand_rows:
        tournament_id = gap.get("tournamentID")
        if tournament_id:
            upsert(
                tournament_id,
                eventName=gap.get("displayQuery"),
                category="user-demand",
                basePriority=120,
                refreshTier="until-results-complete",
                discoveredBy=["user-demand"],
            )
    overrides = target_overrides()
    result: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    for tournament_id, target in targets.items():
        override = overrides.get(tournament_id) or {}
        if override.get("action") in SUPPRESS_ACTIONS:
            suppressed.append({
                "tournamentID": tournament_id,
                "action": override.get("action"),
                "reason": override.get("reason"),
                "canonicalEventID": override.get("canonical_event_id") or None,
            })
            continue
        if override.get("chinese_name"):
            target["sourceEventName"] = target.get("eventName")
            target["eventName"] = override["chinese_name"]
        state = detail_state(tournament_id)
        demand_count = demand_by_tnr.get(tournament_id, 0)
        needs_snapshot = not state.get("snapshotAudited")
        target.update(state)
        target["demandCount"] = demand_count
        breakdown = {
            "base": int(target.get("basePriority") or 0),
            "demand": demand_count * 20,
            "publicDetailGap": 75 if target.get("publicDetailMissing") else 0,
            "snapshotGap": 35 if needs_snapshot else 0,
        }
        target["priorityBreakdown"] = breakdown
        target["priorityScore"] = sum(breakdown.values())
        target["priorityReasons"] = [key for key, value in breakdown.items() if value]
        target["nextAction"] = (
            "capture-event"
            if state["ingestionStatus"] in {"registered", "roster-missing"} or target.get("publicDetailMissing")
            else "refresh-snapshot"
            if needs_snapshot
            else "monitor"
        )
        result.append(target)
    result.sort(key=lambda item: (-item["priorityScore"], item["ingestionStatus"] != "registered", item["tournamentID"]))

    generated_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    event_queue = {
        "schemaVersion": 1,
        "generatedAt": generated_at,
        "policy": [
            "li-chengzhi", "master-tournament", "provincial-youth", "other-domestic",
            "public-event-detail-gap",
        ],
        "totals": {
            "targets": len(result),
            "registered": sum(item["ingestionStatus"] == "registered" for item in result),
            "captured": sum(item["ingestionStatus"] == "captured" for item in result),
            "rosterMissing": sum(item["ingestionStatus"] == "roster-missing" for item in result),
            "publicDetailMissing": sum(bool(item.get("publicDetailMissing")) for item in result),
            "snapshotAudited": sum(bool(item.get("snapshotAudited")) for item in result),
            "suppressed": len(suppressed),
        },
        "targets": result,
        "suppressed": suppressed,
    }
    demand_queue = {
        "schemaVersion": 1,
        "generatedAt": generated_at,
        "privacy": "Only explicitly submitted gaps are published; browser-local misses are not transmitted automatically.",
        "totals": {"open": len(demand_rows), "demand": sum(item["demandCount"] for item in demand_rows)},
        "gaps": demand_rows,
    }
    return event_queue, demand_queue


def main() -> int:
    event_queue, demand_queue = build()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    write_json(OUTPUT, event_queue, ensure_ascii=False, indent=2)
    write_json(DEMAND_OUTPUT, demand_queue, ensure_ascii=False, indent=2)
    # Public surface only ever sees aggregate counts; target names, source IDs
    # and priorities stay in the maintainer queue (de-sourcing contract).
    write_json(SUMMARY_OUTPUT, {
        "schemaVersion": 1,
        "generatedAt": event_queue.get("generatedAt"),
        "totals": event_queue.get("totals") or {},
    }, ensure_ascii=False, indent=2)
    print(json.dumps({"eventTargets": event_queue["totals"], "demand": demand_queue["totals"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
