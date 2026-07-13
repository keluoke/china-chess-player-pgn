#!/usr/bin/env python3
"""Validate a staged registry before it is atomically promoted or released."""

from __future__ import annotations

import argparse
import csv
import json
import pathlib
from typing import Any


def load_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def corrections(path: pathlib.Path) -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    if not path.exists():
        return result
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            fide_id = str(row.get("fide_id") or "").strip()
            correct = str(row.get("correct_chinese_name") or "").strip()
            if fide_id and correct:
                result[fide_id] = (
                    str(row.get("wrong_chinese_name") or "").strip(),
                    correct,
                )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", required=True, type=pathlib.Path)
    parser.add_argument("--corrections", required=True, type=pathlib.Path)
    args = parser.parse_args()

    manifest = load_json(args.registry / "manifest.json")
    players = load_json(args.registry / "players.json")
    if not isinstance(players, list) or not (5000 <= len(players) <= 50000):
        raise SystemExit(f"VALIDATION_REGRESSION: registry player count={len(players) if isinstance(players, list) else '?'}")
    by_id: dict[str, dict[str, Any]] = {}
    for player in players:
        fide_id = str(player.get("fideID") or "")
        if not fide_id or fide_id in by_id:
            raise SystemExit(f"VALIDATION_REGRESSION: duplicate/missing FIDE ID {fide_id!r}")
        by_id[fide_id] = player
    expected = int((manifest.get("totals") or {}).get("players") or 0)
    if expected != len(players):
        raise SystemExit(f"VALIDATION_REGRESSION: manifest={expected} players={len(players)}")

    shard_ids: set[str] = set()
    authority_fields = (
        "displayName", "name", "chineseName", "pinyin", "federation", "sex",
        "title", "womenTitle", "birthYear", "standard", "rapid", "blitz",
        "inactive", "formerFederation", "transfer", "aliases",
    )
    for shard_path in sorted((args.registry / "shards").glob("*.json")):
        for row in load_json(shard_path):
            fide_id = str(row.get("fideID") or "")
            authority = by_id.get(fide_id)
            if not authority or fide_id in shard_ids:
                raise SystemExit(f"VALIDATION_REGRESSION: shard identity mismatch {fide_id}")
            shard_ids.add(fide_id)
            for field in authority_fields:
                if row.get(field) != authority.get(field):
                    raise SystemExit(f"REGISTRY_AUTHORITY_MISMATCH: {fide_id} field={field}")
    if shard_ids != set(by_id):
        raise SystemExit(f"VALIDATION_REGRESSION: shard coverage {len(shard_ids)}/{len(by_id)}")

    for fide_id, (wrong, correct) in corrections(args.corrections).items():
        player = by_id.get(fide_id)
        if not player:
            continue
        aliases = [str(value) for value in player.get("aliases") or []]
        if player.get("chineseName") != correct or player.get("displayName") != correct:
            raise SystemExit(f"NAME_CORRECTION_REGRESSION: {fide_id} must be {correct}")
        if wrong and (wrong in aliases or wrong in {player.get("chineseName"), player.get("displayName")}):
            raise SystemExit(f"NAME_CORRECTION_REGRESSION: {fide_id} still contains {wrong}")

    print(json.dumps({"ok": True, "players": len(players), "shards": len(list((args.registry / 'shards').glob('*.json')))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
