#!/usr/bin/env python3
"""Import pre-fetched player name-mapping rows (TSV) into the manual mapping CSVs.

Use this when Chess-Results pages were fetched outside this repo (for example
by hand or by an assistant without direct network access). The TSV must have a
header row with at least: name, chinese_name; optional: fide_id, title,
federation, rating, club, player_no.

Rows with a FIDE ID update data/manual/player-aliases.csv; rows without one
become domestic sightings. The merge logic is shared with
sync_chess_results_starting_rank_aliases.py, so the same conservative rules
apply (existing reviewed Chinese names are never overwritten).
"""

from __future__ import annotations

import argparse
import csv
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from sync_chess_results_starting_rank_aliases import (  # noqa: E402
    ALIAS_CSV,
    SIGHTINGS_CSV,
    SIGHTING_FIELDS,
    SOURCE_NAME,
    StartingRankRow,
    age_stage_from_event,
    clean,
    dedupe_rows,
    digits_only,
    domestic_sighting_id,
    evidence_note,
    group_name,
    has_cjk,
    read_csv_rows,
    update_aliases,
    write_csv_rows,
)


def append_domestic_sightings(path: pathlib.Path, rows: list[StartingRankRow], dry_run: bool) -> dict[str, int]:
    """Append-only variant: unlike update_domestic_sightings it never rewrites
    rows produced by earlier full scrapes."""
    existing = read_csv_rows(path, SIGHTING_FIELDS)
    seen = {clean(row.get("sighting_id")) for row in existing if clean(row.get("sighting_id"))}
    additions: list[dict[str, str]] = []
    for row in rows:
        if row.fide_id:
            continue
        sighting_id = domestic_sighting_id(row)
        if sighting_id in seen:
            continue
        additions.append(
            {
                "sighting_id": sighting_id,
                "source": SOURCE_NAME,
                "event_id": f"chess-results-tnr{row.tournament_id}",
                "event_name": row.event_name,
                "event_date": "",
                "group": group_name(row),
                "age_stage": age_stage_from_event(row.event_name),
                "player_name": row.name,
                "chinese_name": row.chinese_name,
                "pinyin_name": "",
                "sex": row.sex,
                "birth_year": "",
                "province": "",
                "club": row.club,
                "rank": "",
                "score": "",
                "source_player_no": row.player_no,
                "source_url": row.url,
                "notes": evidence_note(row),
            }
        )
        seen.add(sighting_id)
    if additions and not dry_run:
        write_csv_rows(path, existing + additions, SIGHTING_FIELDS)
    return {"domesticSightingsAdded": len(additions), "domesticSightingsTotal": len(existing) + len(additions)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Import name-mapping TSV rows into manual mapping CSVs.")
    parser.add_argument("tsv", type=pathlib.Path, nargs="+", help="TSV file(s) with header row")
    parser.add_argument("--tournament-id", default="", help="Chess-Results tournament ID for evidence notes")
    parser.add_argument("--event-name", default="", help="event name for evidence notes")
    parser.add_argument("--source-url", default="", help="source page URL for evidence notes")
    parser.add_argument("--player-aliases", type=pathlib.Path, default=ALIAS_CSV)
    parser.add_argument("--domestic-sightings", type=pathlib.Path, default=SIGHTINGS_CSV)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    rows: list[StartingRankRow] = []
    skipped = 0
    for path in args.tsv:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for record in csv.DictReader(handle, delimiter="\t"):
                name = clean(record.get("name"))
                chinese_name = clean(record.get("chinese_name"))
                if not name or not has_cjk(chinese_name):
                    skipped += 1
                    continue
                rows.append(
                    StartingRankRow(
                        tournament_id=clean(record.get("tournament_id")) or args.tournament_id,
                        snode="",
                        url=clean(record.get("source_url")) or args.source_url,
                        event_name=clean(record.get("event_name")) or args.event_name,
                        player_no=clean(record.get("player_no")),
                        title=clean(record.get("title")),
                        name=name,
                        fide_id=digits_only(record.get("fide_id", "")),
                        federation=clean(record.get("federation")),
                        rating=clean(record.get("rating")),
                        sex=clean(record.get("sex")),
                        chinese_name=chinese_name,
                        chinese_name_source="Typ",
                        club=clean(record.get("club")),
                    )
                )

    unique_rows = dedupe_rows(rows)
    alias_stats = update_aliases(args.player_aliases, unique_rows, args.dry_run)
    sighting_stats = append_domestic_sightings(args.domestic_sightings, unique_rows, args.dry_run)
    print(json.dumps({"rows": len(unique_rows), "skipped": skipped, **alias_stats, **sighting_stats, "dryRun": args.dry_run}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
