#!/usr/bin/env python3
"""Build an offline anomaly queue for event metadata and PGN/result joins."""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
EVENTS = ROOT / "docs" / "data" / "index" / "events.json"
EVENT_DETAILS = ROOT / "docs" / "data" / "index" / "event-details"
OUTPUT = ROOT / "docs" / "data" / "audit" / "data-quality-review.json"
REGISTRY = ROOT / "docs" / "data" / "registry" / "players.json"


def clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def normalized_latin_name(value: Any) -> str:
    text = clean(value).casefold().replace(",", " ")
    return re.sub(r"[^a-z0-9]+", "", text)


def person_name_index() -> set[str]:
    if not REGISTRY.exists():
        return set()
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    names: set[str] = set()
    for player in registry:
        for value in [player.get("displayName"), player.get("name"), player.get("chineseName"), *(player.get("aliases") or [])]:
            normalized = re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", clean(value).casefold())
            if normalized:
                names.add(normalized)
    return names


def main() -> int:
    now = dt.datetime.now(dt.timezone.utc)
    future_limit = now.date() + dt.timedelta(days=366)
    issues: list[dict[str, Any]] = []
    known_person_names = person_name_index()
    events = json.loads(EVENTS.read_text(encoding="utf-8")) if EVENTS.exists() else []
    for event in events:
        event_id = clean(event.get("id"))
        title = clean(event.get("displayName") or event.get("name"))
        date = clean(event.get("date"))
        normalized_title = re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", title.casefold())
        if normalized_title in known_person_names:
            issues.append({"type": "person-like-event-title", "eventID": event_id, "value": title, "severity": "review"})
        if date:
            try:
                if dt.date.fromisoformat(date[:10]) > future_limit:
                    issues.append({"type": "far-future-event-date", "eventID": event_id, "value": date, "severity": "review"})
            except ValueError:
                issues.append({"type": "invalid-event-date", "eventID": event_id, "value": date, "severity": "review"})

    for path in sorted(EVENT_DETAILS.glob("tnr*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        canonical_id = clean(payload.get("canonicalEventID")) or f"chess-results:{clean(payload.get('tournamentID'))}"
        for round_row in payload.get("rounds", []):
            round_no = clean(round_row.get("round"))
            for pairing in round_row.get("pairings", []):
                local = pairing.get("localGame") or {}
                if not local:
                    continue
                result = clean(pairing.get("result"))
                local_result = clean(local.get("result"))
                if result and local_result and result != local_result:
                    issues.append({
                        "type": "pgn-result-mismatch",
                        "canonicalEventID": canonical_id,
                        "round": round_no,
                        "playerRef": "|".join(filter(None, [normalized_latin_name(pairing.get("white", {}).get("name")), normalized_latin_name(pairing.get("black", {}).get("name"))])),
                        "resultTable": result,
                        "resultPGN": local_result,
                        "severity": "review",
                    })

    payload = {
        "schemaVersion": 1,
        "generatedAt": now.replace(microsecond=0).isoformat(),
        "rules": ["future-date-over-one-year", "person-like-event-title", "canonical-event-round-player-ref-result-cross-check"],
        "totals": {"issues": len(issues), "eventsScanned": len(events), "eventDetailsScanned": len(list(EVENT_DETAILS.glob('tnr*.json')))},
        "issues": issues,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["totals"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
