#!/usr/bin/env python3
"""Project Entry/Standing facts into PersonObservation rows (plan §5, P1-1).

One observation = one source roster row in one event section, carrying the
participation facts the identity layer needs (group, rank, score, club,
public region, date). Observations are machine artifacts — they never merge
people and never write the manual layer; ``sync_domestic_players.py`` reads
them to enrich existing sightings (stable IDs preserved) and to add event
participation for entities the manual layer has never seen.

Scope: entries WITHOUT a FIDE ID. Entries carrying a FIDE ID are already
served by the registry + event details layers.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import pathlib
import re
from typing import Any

from apply_aliases_to_registry import sanitize_person_name

ROOT = pathlib.Path(__file__).resolve().parents[1]
DETAILS = ROOT / "data" / "generated" / "chess-results-event-details"
EVENTS_CATALOG = ROOT / "data" / "generated" / "events-catalog.json"
MAPPINGS = ROOT / "data" / "community" / "tournament-name-mappings.csv"
COMPLETENESS = ROOT / "data" / "generated" / "event-completeness-report.json"
OUTPUT = ROOT / "data" / "generated" / "person-observations.csv"

COLUMNS = [
    "sighting_id", "source", "event_id", "event_name", "event_date", "group",
    "age_stage", "player_name", "chinese_name", "pinyin_name", "sex",
    "birth_year", "province", "club", "rank", "score", "rounds",
    "source_player_no", "source_url", "notes",
]

AGE_RE = re.compile(r"U\s?(8|10|12|14|16|18|20)\b", re.IGNORECASE)
HANZI_NAME_RE = re.compile(r"^[一-鿿·]{2,6}$")
GROUP_NOISE = ("棋协", "大师", "候补", "棋士", "组", "男子", "女子", "公开")


def clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def read_json(path: pathlib.Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def load_event_dates() -> dict[str, str]:
    """Best available event date per TNR: exact catalog date, else the
    reviewed season year ("YYYY") — never a fabricated exact day."""
    dates: dict[str, str] = {}
    master_csv = ROOT / "data" / "community" / "master-tournament-groups.csv"
    if master_csv.exists():
        with master_csv.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                tid = clean(row.get("tournament_id"))
                year = clean(row.get("year"))
                if tid and re.fullmatch(r"\d{4}", year):
                    dates[tid] = year
    if MAPPINGS.exists():
        with MAPPINGS.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                tid = clean(row.get("tournament_id"))
                match = re.search(r"(\d{4})", clean(row.get("canonical_event_id")))
                if tid and match and tid not in dates:
                    dates[tid] = match.group(1)
    for event in read_json(EVENTS_CATALOG, []) or []:
        tid = clean(event.get("tournamentID"))
        date = clean(event.get("date"))
        if tid and date:
            dates[tid] = date
    # Event PGN archives carry authoritative EventDate headers — the best
    # exact-date source for events the player crawler never listed.
    archive_root = ROOT / "data" / "generated" / "chess-results-event-pgn"
    date_re = re.compile(r'\[(?:Event)?Date\s+"(\d{4})\.(\d{2})\.(\d{2})"\]')
    for path in archive_root.glob("tnr*.pgn"):
        tid = path.stem.removeprefix("tnr")
        if len(dates.get(tid, "")) >= 10:
            continue
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                head = handle.read(2048)
        except OSError:
            continue
        match = date_re.search(head)
        if match and match.group(1) != "????":
            dates[tid] = "-".join(match.groups())
    return dates


def load_mappings() -> dict[str, str]:
    result: dict[str, str] = {}
    if MAPPINGS.exists():
        with MAPPINGS.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                tid = clean(row.get("tournament_id"))
                name = clean(row.get("chinese_name"))
                if tid and name:
                    result[tid] = name
    return result


def best_chinese_name(entry: dict[str, Any]) -> str:
    """Pick a plausible personal Chinese name from roster columns.

    Chess-Results startlists frequently leak the group label ("男子棋协")
    into the Chinese-name column; the real name often sits in ``name``.
    Anything failing sanitize_person_name stays out of the observation."""
    for candidate in (clean(entry.get("name")), clean(entry.get("chineseName"))):
        if not candidate or any(noise in candidate for noise in GROUP_NOISE):
            continue
        if HANZI_NAME_RE.match(candidate):
            sanitized = sanitize_person_name(candidate)
            if sanitized:
                return sanitized
    return ""


def sex_from_title(title: str) -> str:
    if "女子" in title or re.search(r"\bG\d{1,2}\b", title):
        return "F"
    if "男子" in title:
        return "M"
    return ""


def age_stage_from_title(title: str) -> str:
    match = AGE_RE.search(title)
    return f"U{match.group(1)}" if match else ""


def build() -> list[dict[str, str]]:
    dates = load_event_dates()
    names = load_mappings()
    completeness = {
        clean(item.get("tournamentID")): item
        for item in (read_json(COMPLETENESS, {}).get("events") or [])
    }
    rows: list[dict[str, str]] = []
    for path in sorted(DETAILS.glob("tnr*.json")):
        payload = read_json(path, {})
        tid = clean(payload.get("tournamentID"))
        if not tid:
            continue
        report = completeness.get(tid) or {}
        if report and report.get("resultsStatus") not in (None, "results-complete"):
            # Partial captures stay out of the identity layer: an incomplete
            # roster or standings table must not create person facts.
            continue
        title = clean(payload.get("sourceName")) or f"tnr{tid}"
        display = names.get(tid) or title
        standings = {
            clean(row.get("playerNo")): row
            for row in payload.get("standings") or []
            if clean(row.get("playerNo"))
        }
        for entry in payload.get("players") or []:
            if clean(entry.get("fideID")):
                continue
            player_no = clean(entry.get("playerNo"))
            if not player_no:
                continue
            standing = standings.get(player_no) or {}
            rows.append({
                "sighting_id": f"obs-cr-tnr{tid}-p{player_no}",
                "source": "chess-results-event",
                "event_id": f"chess-results-tnr{tid}",
                "event_name": display,
                "event_date": dates.get(tid, ""),
                "group": title,
                "age_stage": age_stage_from_title(title),
                "player_name": clean(entry.get("name")),
                "chinese_name": best_chinese_name(entry),
                "pinyin_name": "",
                "sex": sex_from_title(title),
                "birth_year": "",
                "province": "",
                "club": clean(entry.get("club")),
                "rank": clean(standing.get("rank")),
                "score": clean(standing.get("score")),
                "rounds": clean(payload.get("roundCount")),
                "source_player_no": player_no,
                "source_url": "",
                "notes": "",
            })
    return rows


def main() -> int:
    # Shrink guard: without the full private capture layer (e.g. CI), the
    # committed observations CSV is already the best projection — never
    # regenerate a smaller one from a partial input set.
    report_events = len((read_json(COMPLETENESS, {}) or {}).get("events") or [])
    visible = len(list(DETAILS.glob("tnr*.json")))
    if report_events and visible < report_events and OUTPUT.exists():
        print(json.dumps({
            "skipped": "private capture layer incomplete; keeping committed observations",
            "visibleDetails": visible,
            "reportEvents": report_events,
        }, ensure_ascii=False))
        return 0
    rows = build()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    with_rank = sum(1 for row in rows if row["rank"])
    with_name = sum(1 for row in rows if row["chinese_name"])
    print(json.dumps({
        "observations": len(rows),
        "withRank": with_rank,
        "withChineseName": with_name,
        "generatedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
