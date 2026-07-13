#!/usr/bin/env python3
"""Build docs/data/leaderboards.json — all age groups, youth AND adult.

Pure computation from the committed registry (docs/data/registry/players.json),
no network. Supersedes the youth-only leaderboard for the frontend; the
legacy docs/data/youth-leaderboards.json is left untouched for backward
compatibility until the frontend fully migrates.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from age_groups import LEADERBOARD_GROUPS, age_of, reference_year  # noqa: E402
from stable_json import write_json  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
REGISTRY_PLAYERS = REPO_ROOT / "docs" / "data" / "registry" / "players.json"
OUTPUT = REPO_ROOT / "docs" / "data" / "leaderboards.json"


def player_row(player: dict, age: int) -> dict:
    row = {
        "fideID": player.get("fideID"),
        "displayName": player.get("displayName"),
        "chineseName": player.get("chineseName") or "",
        "name": player.get("name") or "",
        "sex": player.get("sex") or "",
        "title": player.get("title") or "",
        "birthYear": player.get("birthYear"),
        "age": age,
        "standard": player.get("standard"),
        "rapid": player.get("rapid"),
        "blitz": player.get("blitz"),
        "inactive": bool(player.get("inactive")),
    }
    if player.get("formerFederation"):
        row["formerFederation"] = player["formerFederation"]
    if player.get("transfer"):
        row["transferType"] = player["transfer"].get("type", "")
    if player.get("federation") and player.get("federation") != "CHN":
        row["federation"] = player["federation"]
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description="Build all-age leaderboards from the registry.")
    parser.add_argument("--top", type=int, default=100, help="players per group")
    parser.add_argument("--include-inactive", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    players = json.loads(REGISTRY_PLAYERS.read_text(encoding="utf-8"))
    ref_year = reference_year()

    groups_out = []
    for group in LEADERBOARD_GROUPS:
        rows = []
        for player in players:
            age = age_of(player.get("birthYear"), ref_year)
            if age is None:
                continue
            lo, hi = group["minAge"], group["maxAge"]
            if age < lo or (hi is not None and age > hi):
                continue
            if player.get("inactive") and not args.include_inactive:
                continue
            if player.get("standard") is None:
                continue
            rows.append(player_row(player, age))
        rows.sort(key=lambda r: (-(r["standard"] or 0), r["fideID"]))
        groups_out.append(
            {
                "id": group["id"],
                "label": group["label"],
                "minAge": group["minAge"],
                "maxAge": group["maxAge"],
                "totalEligible": len(rows),
                "players": rows[: args.top],
            }
        )

    payload = {
        "generatedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "basisYear": ref_year,
        "note": "age = basisYear - birthYear (自然年龄口径); groups may overlap (OPEN ⊇ S50 ⊇ S65)",
        "groups": groups_out,
    }
    if not args.dry_run:
        write_json(OUTPUT, payload, ensure_ascii=False, indent=1)
    print(json.dumps({g["id"]: g["totalEligible"] for g in groups_out}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
