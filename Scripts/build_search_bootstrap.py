#!/usr/bin/env python3
"""Build the search bootstrap payloads for the homepage.

Two-stage loading keeps time-to-first-search low on mobile:

  search-bootstrap.json           core: FIDE registry players (search enabled
                                  as soon as this file arrives)
  search-bootstrap-domestic.json  deferred: trimmed no-FIDE domestic entities,
                                  fetched in the background and merged in

Domestic rows are aggressively trimmed: `shard` (2-hex prefix) replaces the
full detailPath, constant fields (entityType/federation) are inferred by the
client, and aliases that merely repeat the primary names are dropped.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib

from stable_json import write_json

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "docs" / "data"
OUTPUT_CORE = DATA / "search-bootstrap.json"
OUTPUT_DOMESTIC = DATA / "search-bootstrap-domestic.json"


def read(path: pathlib.Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def compact_aliases(player: dict) -> list[str]:
    primary = {str(player.get(key) or "").replace(" ", "").casefold() for key in ("fideID", "id", "domesticID", "displayName", "name", "chineseName", "pinyin")}
    result = []
    for value in player.get("aliases") or []:
        text = str(value or "").strip()
        if text and text.replace(" ", "").casefold() not in primary and text not in result:
            result.append(text)
    return result


def main() -> int:
    youth = read(ROOT / "data" / "generated" / "youth-leaderboards.json", {})
    registry = read(DATA / "registry" / "players.json", [])
    aggregate = {str(row.get("fideID")): row for row in read(DATA / "index" / "by-player" / "players.json", [])}
    domestic = read(ROOT / "data" / "generated" / "domestic-search-index.json", [])

    players = []
    for row in registry:
        fide_id = str(row.get("fideID") or "")
        games = aggregate.get(fide_id, {})
        payload = {key: row.get(key) for key in (
            "fideID", "displayName", "name", "chineseName", "pinyin", "federation", "sex", "title",
            "birthYear", "standard", "rapid", "blitz", "inactive", "transfer", "formerFederation"
        ) if row.get(key) not in (None, "", False)}
        aliases = compact_aliases(row)
        if aliases:
            payload["aliases"] = aliases
        for key in ("gameCount", "eventCount", "playerPgnPath", "playerIndexPath", "stages", "sources"):
            if games.get(key) not in (None, "", [], {}):
                payload[key] = games[key]
        players.append(payload)

    domestic_rows = []
    for row in domestic:
        # Aggressive byte budget (plan §8.2): the domestic pool tripled once
        # event observations landed, so every redundant byte matters. The
        # client reconstructs id/detailPath/entityType from domesticID+shard.
        payload = {key: row.get(key) for key in (
            "domesticID", "displayName", "sightingCount", "publicLocation"
        ) if row.get(key) not in (None, "", False)}
        if row.get("id") and row.get("id") != row.get("domesticID"):
            payload["id"] = row["id"]
        if row.get("chineseName") and row.get("chineseName") != row.get("displayName"):
            payload["chineseName"] = row["chineseName"]
        if row.get("pinyin"):
            payload["pinyin"] = row["pinyin"]
        if row.get("publicIdentityStatus") not in (None, "", "pending"):
            payload["publicIdentityStatus"] = row["publicIdentityStatus"]
        # `data/registry/domestic/shards/<xx>.json` → keep only the 2-hex prefix.
        detail = str(row.get("detailPath") or "")
        if detail:
            payload["shard"] = detail.rsplit("/", 1)[-1].removesuffix(".json")
        aliases = [
            alias for alias in compact_aliases(row)
            # Space-stripped pinyin duplicates are reconstructed client-side.
            if alias.replace(" ", "").casefold() != str(row.get("pinyin") or "").replace(" ", "").casefold()
        ]
        if aliases:
            payload["aliases"] = aliases
        years = sorted({str(value) for value in (row.get("eventYears") or []) if value})
        if years:
            payload["eventYears"] = [years[0]] if len(years) == 1 else [years[0], years[-1]]
        domestic_rows.append(payload)

    generated_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    core = {
        "schemaVersion": 2,
        "generatedAt": generated_at,
        "competitionYear": youth.get("competitionYear"),
        "ageRule": youth.get("ageRule"),
        "totals": {"players": len(players) + len(domestic_rows), "fide": len(registry), "domestic": len(domestic_rows)},
        "deferred": {"domestic": "data/search-bootstrap-domestic.json"},
        "players": players,
    }
    write_json(OUTPUT_CORE, core, ensure_ascii=False, separators=(",", ":"))
    write_json(OUTPUT_DOMESTIC, {
        "schemaVersion": 2,
        "generatedAt": generated_at,
        "players": domestic_rows,
    }, ensure_ascii=False, separators=(",", ":"))
    print(json.dumps({
        "corePlayers": len(players), "coreBytes": OUTPUT_CORE.stat().st_size,
        "domesticPlayers": len(domestic_rows), "domesticBytes": OUTPUT_DOMESTIC.stat().st_size,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
