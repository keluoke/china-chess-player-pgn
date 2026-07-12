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


ROOT = pathlib.Path(__file__).resolve().parents[1]
STARTING_RANK = ROOT / "data" / "manual" / "chess-results-starting-rank-sources.csv"
MASTER_GROUPS = ROOT / "data" / "community" / "master-tournament-groups.csv"
SOURCE_CATALOG = ROOT / "data" / "manual" / "domestic-source-catalog.csv"
DEMAND_GAPS = ROOT / "data" / "manual" / "data-demand-gaps.csv"
EVENT_DETAILS = ROOT / "data" / "generated" / "chess-results-event-details"
OUTPUT = ROOT / "docs" / "data" / "audit" / "domestic-event-queue.json"
DEMAND_OUTPUT = ROOT / "docs" / "data" / "audit" / "demand-queue.json"


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
        "ingestionStatus": "captured",
        "snapshotAudited": bool(snapshots) and all(item.get("sha256") for item in snapshots),
        "capturedPlayers": len(payload.get("players") or []),
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
        for key, value in values.items():
            if value not in (None, "", [], {}):
                current[key] = value

    for row in rows(STARTING_RANK):
        tournament_id = tnr(row.get("tournament_id") or row.get("url"))
        category, priority = category_priority(row.get("category", ""), row.get("notes", ""))
        upsert(tournament_id, sourceURL=row.get("url"), eventName=row.get("notes"), category=category, basePriority=priority)

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
        )

    demand_by_tnr, demand_rows = demand_index()
    result: list[dict[str, Any]] = []
    for tournament_id, target in targets.items():
        state = detail_state(tournament_id)
        demand_count = demand_by_tnr.get(tournament_id, 0)
        needs_snapshot = not state.get("snapshotAudited")
        target.update(state)
        target["demandCount"] = demand_count
        target["priorityScore"] = int(target.get("basePriority") or 0) + demand_count * 20 + (35 if needs_snapshot else 0)
        target["nextAction"] = "capture-event" if state["ingestionStatus"] == "registered" else "refresh-snapshot" if needs_snapshot else "monitor"
        result.append(target)
    result.sort(key=lambda item: (-item["priorityScore"], item["ingestionStatus"] != "registered", item["tournamentID"]))

    generated_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    event_queue = {
        "schemaVersion": 1,
        "generatedAt": generated_at,
        "policy": ["li-chengzhi", "master-tournament", "provincial-youth", "other-domestic"],
        "totals": {
            "targets": len(result),
            "registered": sum(item["ingestionStatus"] == "registered" for item in result),
            "captured": sum(item["ingestionStatus"] == "captured" for item in result),
            "snapshotAudited": sum(bool(item.get("snapshotAudited")) for item in result),
        },
        "targets": result,
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
    OUTPUT.write_text(json.dumps(event_queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    DEMAND_OUTPUT.write_text(json.dumps(demand_queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"eventTargets": event_queue["totals"], "demand": demand_queue["totals"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
