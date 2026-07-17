#!/usr/bin/env python3
"""Build an offline anomaly queue for event metadata and PGN/result joins."""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
from typing import Any

from stable_json import write_json


ROOT = pathlib.Path(__file__).resolve().parents[1]
EVENTS = ROOT / "data" / "generated" / "events-catalog.json"
EVENT_DETAILS = ROOT / "docs" / "data" / "index" / "event-details"
OUTPUT = ROOT / "docs" / "data" / "audit" / "data-quality-review.json"
REGISTRY = ROOT / "docs" / "data" / "registry" / "players.json"


def clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def normalized_latin_name(value: Any) -> str:
    text = clean(value).casefold().replace(",", " ")
    return re.sub(r"[^a-z0-9]+", "", text)


# Canonical game-result enum. Sources spell the same result many ways
# ("0 - 1" vs "0-1", "½ - ½" vs "1/2-1/2", "+ - -" forfeit notations); compare
# the normalized enum, never raw strings.
_RESULT_ALIASES = {
    "1-0": "1-0", "1:0": "1-0", "+--": "1-0", "1-0f": "1-0", "+-": "1-0",
    "0-1": "0-1", "0:1": "0-1", "--+": "0-1", "0-1f": "0-1", "-+": "0-1",
    "1/2-1/2": "1/2", "0.5-0.5": "1/2", "½-½": "1/2", "1/2": "1/2", "½": "1/2",
    "*": "*", "": "",
}


def normalized_result(value: Any) -> str:
    text = clean(value).casefold()
    # Drop all whitespace, unify unicode halves and dash variants.
    text = re.sub(r"\s+", "", text)
    text = text.replace("–", "-").replace("—", "-")
    text = text.replace("½", "1/2")
    if text in _RESULT_ALIASES:
        return _RESULT_ALIASES[text]
    # Forfeit / adjudication markers such as "1-0(forfeit)" or "+/-".
    compact = re.sub(r"[^01/2+*-]", "", text)
    return _RESULT_ALIASES.get(compact, text)


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
                result = normalized_result(pairing.get("result"))
                local_result = normalized_result(local.get("result"))
                if result and local_result and result != "*" and local_result != "*" and result != local_result:
                    issues.append({
                        "type": "pgn-result-mismatch",
                        "canonicalEventID": canonical_id,
                        "round": round_no,
                        "playerRef": "|".join(filter(None, [normalized_latin_name(pairing.get("white", {}).get("name")), normalized_latin_name(pairing.get("black", {}).get("name"))])),
                        "resultTable": clean(pairing.get("result")),
                        "resultPGN": clean(local.get("result")),
                        "normalized": [result, local_result],
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
    write_json(OUTPUT, payload, ensure_ascii=False, indent=2)
    print(json.dumps(payload["totals"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
