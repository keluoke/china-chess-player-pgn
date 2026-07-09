#!/usr/bin/env python3
"""Apply Chinese-name sources to the static registry JSON in place.

Sources, in descending priority:
  1. data/manual/player-aliases.csv           (curated by hand)
  2. data/generated/chess-results-player-name-map.csv
     (auto-collected by crawl_player_events.py from SpielerSuche CJK
     columns; values are sanitized here — the raw cells carry trailing
     commas like "薛皓文," and the variants column can contain tournament
     titles, so only plausible person names are accepted)

Unlike sync_chinese_players.py this does NOT download the FIDE rating list; it
surgically merges Chinese names, pinyin, and aliases into the already committed
docs/data/registry files (players.json, shards, manifest totals). Use it for a
fast offline refresh between full registry rebuilds. Only players already in
the registry are touched; existing non-empty Chinese names are never replaced.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import pathlib
import re
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
REGISTRY_ROOT = REPO_ROOT / "docs" / "data" / "registry"
ALIAS_CSV = REPO_ROOT / "data" / "manual" / "player-aliases.csv"
NAME_MAP_CSV = REPO_ROOT / "data" / "generated" / "chess-results-player-name-map.csv"

# A plausible Chinese person name: 2-6 CJK chars, optionally with the ethnic
# middle dot (e.g. 玉素甫·艾力). Rejects tournament titles, clubs with digits,
# and mixed-script cells.
PERSON_NAME_RE = re.compile(r"^[㐀-鿿]{1,3}(?:·[㐀-鿿]{1,4})?[㐀-鿿]{0,3}$")


def clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def sanitize_person_name(value: Any) -> str:
    """Strip the separators chess-results appends ('薛皓文,') and accept only
    plausible person names; returns "" for junk like tournament titles."""
    text = clean(value).strip(" ,，;；|、.")
    if 2 <= len(text.replace("·", "")) <= 6 and PERSON_NAME_RE.match(text):
        return text
    return ""


def split_pipe(value: str) -> list[str]:
    return [clean(part) for part in clean(value).split("|") if clean(part)]


def merge_aliases(existing: list[str], additions: list[str]) -> list[str]:
    seen = {value.casefold() for value in existing}
    merged = list(existing)
    for value in additions:
        cleaned = clean(value)
        if cleaned and cleaned.casefold() not in seen:
            seen.add(cleaned.casefold())
            merged.append(cleaned)
    return merged


def write_json(path: pathlib.Path, data: Any) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def apply_to_player(player: dict[str, Any], entry: dict[str, str]) -> tuple[bool, bool]:
    """Merge one alias CSV entry into one registry payload. Returns (changed, conflict)."""
    changed = False
    conflict = False
    chinese_name = entry["chinese_name"]
    pinyin = entry["pinyin_name"]
    additions = [chinese_name, pinyin, pinyin.replace(" ", ""), *split_pipe(entry["aliases"])]

    existing_cn = clean(player.get("chineseName"))
    if chinese_name and not existing_cn:
        player["chineseName"] = chinese_name
        player["displayName"] = chinese_name
        changed = True
    elif chinese_name and existing_cn and existing_cn != chinese_name:
        conflict = True

    if pinyin and not clean(player.get("pinyin")):
        player["pinyin"] = pinyin
        changed = True

    aliases = [clean(v) for v in player.get("aliases", []) if clean(v)]
    merged = merge_aliases(aliases, additions)
    if merged != aliases:
        player["aliases"] = merged
        changed = True
    return changed, conflict


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply manual player aliases to the static registry in place.")
    parser.add_argument("--player-aliases", type=pathlib.Path, default=ALIAS_CSV)
    parser.add_argument("--registry-root", type=pathlib.Path, default=REGISTRY_ROOT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    entries: dict[str, dict[str, str]] = {}
    with args.player_aliases.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            fide_id = clean(row.get("fide_id"))
            if fide_id and clean(row.get("chinese_name")):
                entries[fide_id] = {
                    "chinese_name": clean(row.get("chinese_name")).strip(" ,，;；|"),
                    "pinyin_name": clean(row.get("pinyin_name")),
                    "aliases": clean(row.get("aliases")),
                }

    # Crawler-collected names fill the gaps the curated file doesn't cover.
    # Curated entries always win; crawler values go through strict
    # person-name sanitation because the raw SpielerSuche cells are dirty.
    crawler_entries = 0
    if NAME_MAP_CSV.exists():
        with NAME_MAP_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                fide_id = clean(row.get("fide_id"))
                if not fide_id or fide_id in entries:
                    continue
                chinese = sanitize_person_name(row.get("chinese_name"))
                if not chinese:
                    continue
                variants = [
                    v for v in (
                        sanitize_person_name(part)
                        for part in clean(row.get("name_variants")).split("|")
                    ) if v and v != chinese
                ]
                entries[fide_id] = {
                    "chinese_name": chinese,
                    "pinyin_name": clean(row.get("pinyin_name")),
                    "aliases": "|".join(variants),
                }
                crawler_entries += 1

    players_path = args.registry_root / "players.json"
    players = json.loads(players_path.read_text(encoding="utf-8"))
    by_fide = {clean(player.get("fideID")): player for player in players}

    matched = 0
    updated = 0
    conflicts = 0
    touched_shards: set[str] = set()
    for fide_id, entry in entries.items():
        player = by_fide.get(fide_id)
        if player is None:
            continue
        matched += 1
        changed, conflict = apply_to_player(player, entry)
        conflicts += 1 if conflict else 0
        if changed:
            updated += 1
            shard = clean(player.get("registryShard"))
            if shard:
                touched_shards.add(shard)

    shard_updated = 0
    for shard_rel in sorted(touched_shards):
        shard_path = REPO_ROOT / "docs" / shard_rel
        if not shard_path.exists():
            continue
        shard_players = json.loads(shard_path.read_text(encoding="utf-8"))
        shard_changed = False
        for player in shard_players:
            entry = entries.get(clean(player.get("fideID")))
            if entry is None:
                continue
            changed, _ = apply_to_player(player, entry)
            shard_changed = shard_changed or changed
            shard_updated += 1 if changed else 0
        if shard_changed and not args.dry_run:
            write_json(shard_path, shard_players)

    manifest_path = args.registry_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["generatedAt"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    manifest.setdefault("totals", {})["withChineseName"] = sum(1 for player in players if clean(player.get("chineseName")))

    if not args.dry_run:
        write_json(players_path, players)
        write_json(manifest_path, manifest)

    print(json.dumps(
        {
            "aliasEntries": len(entries),
            "fromCrawlerNameMap": crawler_entries,
            "matchedInRegistry": matched,
            "playersUpdated": updated,
            "shardPlayersUpdated": shard_updated,
            "chineseNameConflictsKeptExisting": conflicts,
            "withChineseName": manifest["totals"]["withChineseName"],
            "dryRun": args.dry_run,
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
