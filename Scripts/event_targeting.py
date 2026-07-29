#!/usr/bin/env python3
"""Shared maintainer-local event targeting helpers.

The reviewed queue lives in the repository. Player-search discoveries and the
FIDE IDs that led to them stay in the maintainer state directory and are never
part of a release manifest.
"""

from __future__ import annotations

import csv
import json
import pathlib
import re
from typing import Any

from source_policy import local_state_root

ROOT = pathlib.Path(__file__).resolve().parents[1]
STATIC_QUEUE = ROOT / "data" / "generated" / "audit" / "domestic-event-queue.json"
TARGET_OVERRIDES = ROOT / "data" / "community" / "tournament-target-overrides.csv"
PUBLIC_EVENTS = ROOT / "docs" / "data" / "index" / "public-events.json"
DISCOVERY_POOL = local_state_root() / "chess-results" / "event-discovery-pool.json"

SUPPRESS_ACTIONS = {"exclude", "aggregate", "duplicate"}


def clean_tnr(value: Any) -> str:
    match = re.search(r"(?:tnr)?(\d{4,9})", str(value or ""), flags=re.IGNORECASE)
    return match.group(1) if match else ""


def read_json(path: pathlib.Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def target_overrides(path: pathlib.Path = TARGET_OVERRIDES) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    result: dict[str, dict[str, str]] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            tournament_id = clean_tnr(row.get("tournament_id"))
            if tournament_id:
                result[tournament_id] = {
                    key: " ".join(str(value or "").split())
                    for key, value in row.items()
                }
    return result


def localized_event_names(path: pathlib.Path = PUBLIC_EVENTS) -> dict[str, str]:
    payload = read_json(path, {})
    result: dict[str, str] = {}
    for event in payload.get("events") or []:
        tournament_id = clean_tnr(event.get("tournamentID"))
        name = " ".join(str(event.get("displayName") or "").split())
        if tournament_id and name:
            result[tournament_id] = name
    for tournament_id, row in target_overrides().items():
        if row.get("chinese_name"):
            result[tournament_id] = row["chinese_name"]
    return result


def load_discovery_pool(path: pathlib.Path = DISCOVERY_POOL) -> dict[str, Any]:
    payload = read_json(path, {})
    return payload if isinstance(payload, dict) else {}


def merged_target_items(
    *,
    queue_path: pathlib.Path = STATIC_QUEUE,
    pool_path: pathlib.Path = DISCOVERY_POOL,
) -> list[dict[str, Any]]:
    """Merge reviewed and private discoveries, with reviewed overrides applied."""
    queue = read_json(queue_path, {})
    pool = load_discovery_pool(pool_path)
    overrides = target_overrides()
    names = localized_event_names()
    merged: dict[str, dict[str, Any]] = {}

    def add(item: dict[str, Any], private: bool) -> None:
        tournament_id = clean_tnr(item.get("tournamentID"))
        if not tournament_id:
            return
        override = overrides.get(tournament_id) or {}
        if override.get("action") in SUPPRESS_ACTIONS:
            return
        current = merged.setdefault(tournament_id, {"tournamentID": tournament_id})
        for key, value in item.items():
            if value not in (None, "", [], {}):
                current[key] = value
        sources = list(current.get("discoveredBy") or [])
        for source in item.get("discoveredBy") or ([] if private else ["reviewed-queue"]):
            if source not in sources:
                sources.append(source)
        current["discoveredBy"] = sources
        if private:
            current["privateDiscovery"] = True
            current.setdefault("nextAction", "capture-event")
            current.setdefault("category", "fide-player-discovery")
        if names.get(tournament_id):
            current["sourceEventName"] = current.get("eventName")
            current["eventName"] = names[tournament_id]

    candidates = pool.get("candidates") or {}
    iterable = candidates.values() if isinstance(candidates, dict) else candidates
    for item in iterable:
        if isinstance(item, dict) and item.get("status", "pending") == "pending":
            add(dict(item), True)
    # Reviewed queue data wins when a private discovery points at an already
    # catalogued target (especially nextAction=monitor for a completed event).
    for item in queue.get("targets") or []:
        if isinstance(item, dict):
            add(dict(item), False)

    return sorted(
        merged.values(),
        key=lambda item: (-int(item.get("priorityScore") or 0), item["tournamentID"]),
    )
