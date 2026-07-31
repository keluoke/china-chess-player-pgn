#!/usr/bin/env python3
"""Validate reviewed master-tournament group labels against local event titles."""

from __future__ import annotations

import csv
import json
import pathlib
import re
import sys
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
GROUPS = ROOT / "data" / "community" / "master-tournament-groups.csv"
DETAILS = ROOT / "data" / "generated" / "chess-results-event-details"
MAPPINGS = ROOT / "data" / "community" / "tournament-name-mappings.csv"

ISOLATED_STATUSES = {"source-target-mismatch", "source-page-record-not-found"}
GROUP_PATTERNS = (
    ("WOMEN_CANDIDATE", re.compile(r"女子候补(?:棋协)?大师组", re.IGNORECASE)),
    ("MEN_CANDIDATE", re.compile(r"男子候补(?:棋协)?大师组", re.IGNORECASE)),
    ("WOMEN_LEVEL_1", re.compile(r"女子一级棋士(?:[ABC])?组", re.IGNORECASE)),
    ("MEN_LEVEL_1", re.compile(r"男子一级棋士(?:[ABC])?组", re.IGNORECASE)),
    ("OPEN", re.compile(r"棋协大师组|公开组|\bOpen\b", re.IGNORECASE)),
)


def clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def title_group_code(title: str) -> str:
    return next((code for code, pattern in GROUP_PATTERNS if pattern.search(title or "")), "")


def load_mapping_groups(path: pathlib.Path | None) -> dict[str, str]:
    if path is None or not path.is_file():
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return {
            clean(row.get("tournament_id")): title_group_code(clean(row.get("chinese_name")))
            for row in csv.DictReader(handle)
            if clean(row.get("tournament_id"))
        }


def validate(
    groups_path: pathlib.Path = GROUPS,
    details_root: pathlib.Path = DETAILS,
    mappings_path: pathlib.Path | None = None,
) -> dict[str, Any]:
    failures: list[str] = []
    checked = matched = unresolved = isolated = 0
    mapping_groups = load_mapping_groups(mappings_path)
    with groups_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for line_number, row in enumerate(csv.DictReader(handle), start=2):
            tournament_id = clean(row.get("tournament_id"))
            status = clean(row.get("evidence_status")).lower()
            path = details_root / f"tnr{tournament_id}.json"
            if not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                failures.append(f"line {line_number} tnr{tournament_id}: unreadable detail ({error})")
                continue
            title = clean(payload.get("sourceName"))
            actual = title_group_code(title)
            expected = clean(row.get("group_code"))
            checked += 1

            if status in ISOLATED_STATUSES:
                isolated += 1
                continue
            mapped = mapping_groups.get(tournament_id, "")
            if mapped and mapped != expected:
                failures.append(
                    f"line {line_number} tnr{tournament_id}: group_code={expected}, "
                    f"name mapping implies {mapped}"
                )
                continue
            if actual and actual != expected:
                failures.append(
                    f"line {line_number} tnr{tournament_id}: group_code={expected}, "
                    f"page title implies {actual} ({title})"
                )
                continue
            if actual:
                matched += 1
            else:
                unresolved += 1
                if status == "page-verified":
                    failures.append(
                        f"line {line_number} tnr{tournament_id}: page-verified but title has no group token ({title})"
                    )

    return {
        "ok": not failures,
        "checked": checked,
        "matched": matched,
        "unresolved": unresolved,
        "isolated": isolated,
        "failures": failures,
    }


def main() -> int:
    result = validate(mappings_path=MAPPINGS)
    if result["failures"]:
        print("MASTER GROUP LABEL VALIDATION FAILED:", file=sys.stderr)
        for failure in result["failures"][:30]:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print(json.dumps({key: value for key, value in result.items() if key != "failures"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
