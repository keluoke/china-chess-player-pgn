#!/usr/bin/env python3
"""Fill missing domestic-player pinyin search aliases in the manual evidence CSV.

This is a local, deterministic enrichment step. It does not merge identities,
change Chinese names, or run in CI. Persisting the alias keeps site builds
network-free and makes new no-FIDE records searchable by full pinyin.
"""

from __future__ import annotations

import argparse
import csv
import pathlib
import re
import tempfile

from pypinyin import lazy_pinyin


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_PATH = REPO_ROOT / "data" / "manual" / "domestic-player-sightings.csv"


def as_pinyin(value: str) -> str:
    value = (value or "").strip()
    if not re.fullmatch(r"[\u4e00-\u9fff·]{2,12}", value):
        return ""
    return " ".join(part.lower() for part in lazy_pinyin(value) if part).strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=pathlib.Path, default=DEFAULT_PATH)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with args.path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    if "pinyin_name" not in fieldnames:
        raise SystemExit(f"missing pinyin_name column: {args.path}")

    changed = 0
    for row in rows:
        if (row.get("pinyin_name") or "").strip():
            continue
        value = row.get("chinese_name") or row.get("player_name") or ""
        pinyin = as_pinyin(value)
        if pinyin:
            row["pinyin_name"] = pinyin
            changed += 1

    if not args.dry_run and changed:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", delete=False, dir=args.path.parent) as tmp:
            writer = csv.DictWriter(tmp, fieldnames=fieldnames, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
            temp_path = pathlib.Path(tmp.name)
        temp_path.replace(args.path)

    print(f"domestic pinyin aliases: {changed} added, {len(rows) - changed} unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
