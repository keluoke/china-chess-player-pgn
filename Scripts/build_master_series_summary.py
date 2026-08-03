#!/usr/bin/env python3
"""Build the public 2022-2026 Chess Association Master series summary.

The page payload is derived from the public event catalog plus the canonical
completeness report.  Only events with a published structured detail count as
"ingested"; metadata-only catalog rows remain outside the totals.
"""

from __future__ import annotations

import json
import pathlib
from collections import Counter, defaultdict
from typing import Any

from build_event_catalog import parse_master_title_hints
from snapshot_context import stamp
from stable_json import write_json


ROOT = pathlib.Path(__file__).resolve().parents[1]
PUBLIC_EVENTS = ROOT / "docs" / "data" / "index" / "public-events.json"
COMPLETENESS = ROOT / "data" / "generated" / "event-completeness-report.json"
OUTPUT = ROOT / "docs" / "data" / "master-series-summary.json"
SERIES = "chess-association-master"
START_YEAR = 2022
END_YEAR = 2026

STATUS_META = {
    "full": {
        "label": "全台 PGN 完整",
        "description": "全部实际对局均已匹配归档。",
    },
    "live": {
        "label": "仅直播台次 PGN 完整",
        "description": "来源公开的直播台次已全部归档，不代表全台完整。",
    },
    "partial": {
        "label": "直播 PGN 待补齐",
        "description": "已有部分直播棋谱，但公开范围尚未全部匹配归档。",
    },
    "missing": {
        "label": "直播 PGN 待归档",
        "description": "已确认存在直播棋谱，但当前公开归档仍为空。",
    },
    "none": {
        "label": "无 PGN",
        "description": "赛事组别已入库，但没有已公开棋谱。",
    },
    "unknown": {
        "label": "PGN 状态待核",
        "description": "当前快照尚不能确认棋谱覆盖状态。",
    },
}


def read_json(path: pathlib.Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def as_int(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def pgn_category(report: dict[str, Any]) -> str:
    status = str(report.get("pgnIngestStatus") or "")
    archived = as_int((report.get("counts") or {}).get("archivedGames")) or 0
    if status == "full-board-complete":
        return "full"
    if status == "source-published-complete":
        return "live"
    if status == "source-published-partial" or archived > 0:
        return "partial"
    if status == "source-published-missing":
        return "missing"
    if status in {"not-published", "not-applicable"}:
        return "none"
    return "unknown"


def coverage_percent(archived: int | None, played: int | None) -> float | None:
    if archived is None or not played:
        return None
    return round(archived * 100 / played, 1)


def group_row(event: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    parsed_station, parsed_group = parse_master_title_hints(event)
    station = str(event.get("station") or parsed_station or "站名待核").strip()
    group_label = str(event.get("groupLabel") or parsed_group or "组别待核").strip()
    counts = report.get("counts") or {}
    archived = as_int(counts.get("archivedGames"))
    played = as_int(counts.get("playedGames"))
    category = pgn_category(report)
    tournament_id = str(event.get("tournamentID") or "").strip()
    route_id = tournament_id or str(event.get("id") or "").strip()
    return {
        "tournamentID": tournament_id or None,
        "routeID": route_id,
        "date": event.get("date"),
        "station": station,
        "groupLabel": group_label,
        "groupLabelPending": group_label == "组别待核" or None,
        "participants": as_int(event.get("participants")) or as_int(counts.get("players")),
        "rounds": as_int(event.get("rounds")) or as_int(counts.get("roundsExpected")),
        "pgnStatus": category,
        "pgnStatusLabel": STATUS_META[category]["label"],
        "archivedGames": archived,
        "playedGames": played,
        "allBoardCoveragePercent": coverage_percent(archived, played),
    }


def build_summary(public_payload: dict[str, Any], completeness_payload: dict[str, Any]) -> dict[str, Any]:
    reports = {
        str(item.get("tournamentID") or ""): item
        for item in completeness_payload.get("events", [])
        if str(item.get("tournamentID") or "")
    }
    groups: list[dict[str, Any]] = []
    excluded_metadata = 0
    for event in public_payload.get("events", []):
        if event.get("series") != SERIES:
            continue
        year = as_int(event.get("year"))
        if year is None or not START_YEAR <= year <= END_YEAR:
            continue
        if event.get("detailStatus") != "published":
            excluded_metadata += 1
            continue
        row = group_row(event, reports.get(str(event.get("tournamentID") or ""), {}))
        row["year"] = year
        groups.append(row)

    groups.sort(
        key=lambda item: (
            item["year"],
            item.get("date") or "",
            item["station"],
            item["groupLabel"],
            item.get("tournamentID") or "",
        ),
        reverse=True,
    )
    years: list[dict[str, Any]] = []
    for year in range(END_YEAR, START_YEAR - 1, -1):
        year_groups = [item for item in groups if item["year"] == year]
        station_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in year_groups:
            station_groups[item["station"]].append(item)
        stations = []
        for station, items in station_groups.items():
            items.sort(key=lambda item: (item.get("date") or "", item["groupLabel"]), reverse=True)
            status_counts = Counter(item["pgnStatus"] for item in items)
            stations.append({
                "station": station,
                "groupCount": len(items),
                "latestDate": max((item.get("date") or "" for item in items), default="") or None,
                "statusCounts": dict(status_counts),
                "groups": items,
            })
        stations.sort(key=lambda item: (item.get("latestDate") or "", item["station"]), reverse=True)
        years.append({
            "year": year,
            "stationCount": len(stations),
            "groupCount": len(year_groups),
            "statusCounts": dict(Counter(item["pgnStatus"] for item in year_groups)),
            "stations": stations,
        })

    status_counts = Counter(item["pgnStatus"] for item in groups)
    return {
        "schemaVersion": 1,
        "range": {"startYear": START_YEAR, "endYear": END_YEAR},
        "definition": "仅统计已发布完整赛果详情的组别；站数按年份与站名去重。",
        "totals": {
            "years": END_YEAR - START_YEAR + 1,
            "stations": sum(item["stationCount"] for item in years),
            "groups": len(groups),
            "metadataOnlyExcluded": excluded_metadata,
            "statusCounts": {key: status_counts.get(key, 0) for key in STATUS_META},
        },
        "statusLegend": STATUS_META,
        "years": years,
    }


def main() -> int:
    public_payload = read_json(PUBLIC_EVENTS, {})
    completeness_payload = read_json(COMPLETENESS, {})
    payload = stamp(build_summary(public_payload, completeness_payload))
    write_json(OUTPUT, payload, ensure_ascii=False, indent=2)
    print(json.dumps({
        "years": payload["totals"]["years"],
        "stations": payload["totals"]["stations"],
        "groups": payload["totals"]["groups"],
        "statusCounts": payload["totals"]["statusCounts"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
