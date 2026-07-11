#!/usr/bin/env python3
"""Import verified Chess-Results master-event sections from a Markdown list.

The source document remains evidence; this importer only accepts table rows
that contain an explicit ``tnr<ID>`` link. It never guesses missing adjacent
IDs. Existing manually reviewed rows win on fields the document cannot supply.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import pathlib
import re
from dataclasses import dataclass
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
MAPPINGS = ROOT / "data" / "community" / "tournament-name-mappings.csv"
MASTER_GROUPS = ROOT / "data" / "community" / "master-tournament-groups.csv"
SOURCES = ROOT / "data" / "manual" / "chess-results-starting-rank-sources.csv"

MAPPING_FIELDS = ["source", "tournament_id", "canonical_event_id", "chinese_name", "evidence_url", "notes"]
GROUP_FIELDS = [
    "canonical_event_id", "section_id", "year", "station", "group_code", "sex", "level",
    "tournament_id", "source_url", "rounds", "promotion_rate", "evidence_status", "notes",
]
SOURCE_FIELDS = ["tournament_id", "url", "category", "notes"]


@dataclass(frozen=True)
class Section:
    year: int
    station: str
    group_name: str
    group_code: str
    sex: str
    level: str
    tournament_id: str
    source_url: str

    @property
    def canonical_id(self) -> str:
        digest = hashlib.sha256(f"{self.year}|{self.station}".encode("utf-8")).hexdigest()[:10]
        return f"chess-association-master-{self.year}-{digest}"

    @property
    def section_id(self) -> str:
        suffix = self.group_code.lower().replace("_", "-")
        if "A组" in self.group_name.upper():
            suffix += "-a"
        elif "B组" in self.group_name.upper():
            suffix += "-b"
        return f"{self.canonical_id}-{suffix}"

    @property
    def chinese_name(self) -> str:
        return f"{self.year}年全国国际象棋棋协大师赛（{self.station}）{self.group_name}"


def clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def clean_station(heading: str) -> str:
    value = re.sub(r"^\d+\.\s*", "", clean(heading))
    value = re.split(r"（(?:\d{1,2}[.月]|\d{4})", value, maxsplit=1)[0]
    value = re.split(r"\s*[—-]\s*", value, maxsplit=1)[0]
    return value.strip(" ·—-")


def classify_group(name: str) -> tuple[str, str, str] | None:
    value = clean(name)
    if "女子棋协大师" in value:
        return "WOMEN_OPEN", "F", "OPEN"
    if value == "棋协大师组" or value.endswith("公开组"):
        return "OPEN", "", "OPEN"
    if "男子候补" in value:
        return "MEN_CANDIDATE", "M", "CANDIDATE"
    if "女子候补" in value:
        return "WOMEN_CANDIDATE", "F", "CANDIDATE"
    if "男子一级" in value:
        level = "LEVEL_1_A" if "A组" in value.upper() else "LEVEL_1_B" if "B组" in value.upper() else "LEVEL_1"
        return "MEN_LEVEL_1", "M", level
    if "女子一级" in value:
        return "WOMEN_LEVEL_1", "F", "LEVEL_1"
    return None


def parse_markdown(path: pathlib.Path) -> list[Section]:
    year: int | None = None
    station = ""
    sections: list[Section] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        year_match = re.match(r"^###\s+(20\d{2})年", raw)
        if year_match:
            year = int(year_match.group(1))
            station = ""
            continue
        station_match = re.match(r"^####\s+(.+)$", raw)
        if station_match and year:
            station = clean_station(station_match.group(1))
            continue
        if not year or not station or not raw.startswith("|"):
            continue
        cells = [clean(cell) for cell in raw.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        group_name = cells[0]
        classification = classify_group(group_name)
        link_match = re.search(r"https?://(?:www\.)?chess-results\.com/tnr(\d+)\.aspx", raw, flags=re.IGNORECASE)
        if not classification or not link_match:
            continue
        tournament_id = link_match.group(1)
        group_code, sex, level = classification
        sections.append(Section(
            year=year,
            station=station,
            group_name=group_name,
            group_code=group_code,
            sex=sex,
            level=level,
            tournament_id=tournament_id,
            source_url=f"https://chess-results.com/tnr{tournament_id}.aspx?lan=1",
        ))
    unique = {section.tournament_id: section for section in sections}
    return sorted(unique.values(), key=lambda item: (item.year, item.canonical_id, item.group_code, item.tournament_id))


def read_rows(path: pathlib.Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [{key: clean(value) for key, value in row.items()} for row in csv.DictReader(handle)]


def write_rows(path: pathlib.Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("markdown", type=pathlib.Path)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    sections = parse_markdown(args.markdown)

    mappings = {(row.get("source", "").lower(), row.get("tournament_id", "")): row for row in read_rows(MAPPINGS)}
    groups = {row.get("tournament_id", ""): row for row in read_rows(MASTER_GROUPS)}
    sources = {row.get("tournament_id", ""): row for row in read_rows(SOURCES)}
    for section in sections:
        key = ("chess-results", section.tournament_id)
        mappings[key] = {
            **mappings.get(key, {}),
            "source": "Chess-Results",
            "tournament_id": section.tournament_id,
            "canonical_event_id": section.canonical_id,
            "chinese_name": section.chinese_name,
            "evidence_url": section.source_url,
            "notes": "棋协大师赛资料清单明确列出的分组 TNR；页面需本地 verify",
        }
        groups[section.tournament_id] = {
            **groups.get(section.tournament_id, {}),
            "canonical_event_id": section.canonical_id,
            "section_id": section.section_id,
            "year": str(section.year),
            "station": section.station,
            "group_code": section.group_code,
            "sex": section.sex,
            "level": section.level,
            "tournament_id": section.tournament_id,
            "source_url": section.source_url,
            "rounds": groups.get(section.tournament_id, {}).get("rounds") or "9",
            "promotion_rate": "0.65",
            "evidence_status": "source-list-needs-page-verify",
            "notes": f"资料清单组别：{section.group_name}；轮次 9 为规则默认值，抓取后以页面为准",
        }
        sources[section.tournament_id] = {
            **sources.get(section.tournament_id, {}),
            "tournament_id": section.tournament_id,
            "url": section.source_url,
            "category": "national-amateur-master",
            "notes": section.chinese_name,
        }

    if args.write:
        write_rows(MAPPINGS, MAPPING_FIELDS, sorted(mappings.values(), key=lambda row: (row.get("source", ""), row.get("tournament_id", ""))))
        write_rows(MASTER_GROUPS, GROUP_FIELDS, sorted(groups.values(), key=lambda row: (row.get("year", ""), row.get("canonical_event_id", ""), row.get("section_id", ""))))
        write_rows(SOURCES, SOURCE_FIELDS, sorted(sources.values(), key=lambda row: (row.get("category", ""), row.get("tournament_id", ""))))

    print(json.dumps({
        "sections": len(sections),
        "years": sorted({section.year for section in sections}),
        "stations": len({section.canonical_id for section in sections}),
        "write": args.write,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
