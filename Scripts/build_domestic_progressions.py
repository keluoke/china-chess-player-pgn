#!/usr/bin/env python3
"""Build reviewed domestic-player progression and promotion evidence.

Names never merge entities here. Timelines only span multiple sightings after
player-identity-links.csv has connected them to one domestic/FIDE identity.
"""

from __future__ import annotations

import csv
import json
import pathlib
import re
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
GROUPS = ROOT / "data" / "community" / "master-tournament-groups.csv"
PLAYERS = ROOT / "docs" / "data" / "registry" / "domestic" / "players.json"
OUTPUT = ROOT / "docs" / "data" / "registry" / "domestic" / "progressions.json"
PROMOTION_REVIEW = ROOT / "docs" / "data" / "registry" / "domestic" / "promotion-review.json"


def clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def read_json(path: pathlib.Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def tournament_number(value: Any) -> str:
    match = re.search(r"(?:tnr)?(\d{5,})", clean(value), flags=re.IGNORECASE)
    return match.group(1) if match else ""


def read_groups() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not GROUPS.exists():
        return result
    with GROUPS.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            tnr = tournament_number(row.get("tournament_id"))
            if not tnr:
                continue
            result[tnr] = {
                "canonicalEventID": clean(row.get("canonical_event_id")),
                "sectionID": clean(row.get("section_id")),
                "year": int(row["year"]) if clean(row.get("year")).isdigit() else None,
                "station": clean(row.get("station")),
                "groupCode": clean(row.get("group_code")),
                "sex": clean(row.get("sex")),
                "level": clean(row.get("level")),
                "tournamentID": tnr,
                "sourceURL": clean(row.get("source_url")),
                "rounds": int(row["rounds"]) if clean(row.get("rounds")).isdigit() else None,
                "promotionRate": float(row.get("promotion_rate") or 0.65),
                "evidenceStatus": clean(row.get("evidence_status")),
            }
    return result


def parse_score(value: Any) -> float | None:
    match = re.search(r"\d+(?:\.5)?", clean(value))
    return float(match.group()) if match else None


def build() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    groups = read_groups()
    progressions: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []
    for player in read_json(PLAYERS, []):
        timeline: list[dict[str, Any]] = []
        for sighting in player.get("sightings", []):
            tnr = tournament_number(sighting.get("eventID") or sighting.get("sourceURL"))
            group = groups.get(tnr)
            if not group:
                continue
            score = parse_score(sighting.get("score"))
            rounds = group.get("rounds")
            rate = (score / rounds) if score is not None and rounds else None
            qualified = rate >= group["promotionRate"] if rate is not None else None
            row = {
                **group,
                "eventName": sighting.get("eventName"),
                "score": score,
                "scoreRate": round(rate, 4) if rate is not None else None,
                "promotionQualified": qualified,
                "sourceRef": {"source": "Chess-Results", "tournamentID": tnr, "url": sighting.get("sourceURL") or group.get("sourceURL")},
            }
            timeline.append({key: value for key, value in row.items() if value is not None and value != ""})
        if not timeline:
            continue
        timeline.sort(key=lambda row: (row.get("year") or 0, row.get("sectionID") or ""))
        entity = {
            "playerID": player.get("id"),
            "domesticID": player.get("domesticID"),
            "fideID": player.get("fideID"),
            "displayName": player.get("displayName"),
            "identityStatus": player.get("identityStatus"),
            "identityConfidence": player.get("confidence"),
            "timeline": timeline,
            "reachedOpenWithFIDE": bool(player.get("fideID") and any(row.get("groupCode") == "OPEN" for row in timeline)),
        }
        progressions.append({key: value for key, value in entity.items() if value is not None and value != ""})
        for row in timeline:
            if row.get("promotionQualified") is True or player.get("confidence", {}).get("reviewRequired"):
                review.append({
                    "playerID": player.get("id"),
                    "displayName": player.get("displayName"),
                    "sectionID": row.get("sectionID"),
                    "promotionQualified": row.get("promotionQualified"),
                    "identityReviewRequired": player.get("confidence", {}).get("reviewRequired", True),
                    "reason": "达到 65% 晋级线" if row.get("promotionQualified") is True else "身份置信分低，禁止自动串联成长路径",
                })
    return progressions, review


def main() -> int:
    progressions, review = build()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(progressions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    PROMOTION_REVIEW.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"progressions": len(progressions), "promotionReview": len(review)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
