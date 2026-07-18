#!/usr/bin/env python3
"""Import a downloaded web contribution into the offline demand/source queues.

Privacy requests and identity claims are intentionally not written to the
public repository by this helper; they require private maintainer handling.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import pathlib
import re
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
GAPS = ROOT / "data" / "manual" / "data-demand-gaps.csv"
SOURCES = ROOT / "data" / "manual" / "domestic-source-catalog.csv"


def clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def normalized(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", clean(value).casefold())


def read_rows(path: pathlib.Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def write_rows(path: pathlib.Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in rows)


def extract_tnr(value: Any) -> str:
    match = re.search(r"(?:tnr)?(\d{5,9})", clean(value), flags=re.IGNORECASE)
    return match.group(1) if match else ""


def import_gap(payload: dict[str, Any]) -> dict[str, Any]:
    query = clean(payload.get("data_query") or payload.get("player_name") or payload.get("event_name") or payload.get("event_ref"))
    if not query:
        raise SystemExit("数据缺口贡献缺少 query/player/event 字段")
    tournament_id = extract_tnr(payload.get("event_ref") or query)
    key = normalized(query)
    fields, rows = read_rows(GAPS)
    existing = next((row for row in rows if clean(row.get("normalized_query")) == key), None)
    if existing:
        existing["demand_count"] = str(int(existing.get("demand_count") or 0) + 1)
        existing["last_requested_at"] = dt.date.today().isoformat()
    else:
        rows.append({
            "gap_id": "gap-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:12],
            "query_type": "tnr" if tournament_id else "player",
            "display_query": query,
            "normalized_query": key,
            "tournament_id": tournament_id,
            "demand_count": "1",
            "last_requested_at": dt.date.today().isoformat(),
            "status": "open",
            "evidence_url": clean(payload.get("evidence_url")),
            "notes": clean(payload.get("notes")),
        })
    write_rows(GAPS, fields, rows)
    return {"kind": "demand-gap", "query": query, "tournamentID": tournament_id or None}


def import_event_source(payload: dict[str, Any]) -> dict[str, Any]:
    tournament_id = extract_tnr(payload.get("event_ref"))
    if not tournament_id:
        raise SystemExit("赛事贡献缺少有效 tnr")
    fields, rows = read_rows(SOURCES)
    if any(clean(row.get("tournament_id")) == tournament_id for row in rows):
        return {"kind": "event-source", "tournamentID": tournament_id, "existing": True}
    supplied_url = clean(payload.get("evidence_url") or payload.get("event_ref"))
    official_url = supplied_url if supplied_url.startswith(("http://", "https://")) else f"https://chess-results.com/tnr{tournament_id}.aspx?lan=1"
    rows.append({
        "source_id": f"community-tnr-{tournament_id}",
        "event_name": clean(payload.get("event_name")) or f"tnr{tournament_id}",
        "event_type": "community-event",
        "official_url": official_url,
        "tournament_id": tournament_id,
        "status": "registered",
        "redistributable": "unknown",
        "priority": "80",
        "refresh_tier": "weekly",
        "evidence_note": clean(payload.get("notes")),
    })
    write_rows(SOURCES, fields, rows)
    return {"kind": "event-source", "tournamentID": tournament_id, "existing": False}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+", type=pathlib.Path)
    args = parser.parse_args()
    results = []
    for path in args.files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema") != "china-chess-community-contribution/v1":
            raise SystemExit(f"不支持的贡献格式: {path}")
        kind = clean(payload.get("type"))
        if kind == "data-gap":
            results.append(import_gap(payload))
        elif kind == "event-tnr":
            results.append(import_event_source(payload))
        elif kind in {"privacy-request", "identity-clue", "identity-dispute"}:
            raise SystemExit(f"{kind} 含身份/隐私/争议信息，必须私下处理，不得写入公开仓库")
        else:
            raise SystemExit(f"该贡献类型需人工审核后入库: {kind}")
    print(json.dumps({"imported": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
