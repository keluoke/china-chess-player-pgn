#!/usr/bin/env python3
"""Build the single compact payload needed before homepage search is usable."""

from __future__ import annotations

import datetime as dt
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "docs" / "data"
OUTPUT = DATA / "search-bootstrap.json"


def read(path: pathlib.Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def compact_aliases(player: dict) -> list[str]:
    primary = {str(player.get(key) or "").replace(" ", "").casefold() for key in ("fideID", "displayName", "name", "chineseName", "pinyin")}
    result = []
    for value in player.get("aliases") or []:
        text = str(value or "").strip()
        if text and text.replace(" ", "").casefold() not in primary and text not in result:
            result.append(text)
    return result


def main() -> int:
    youth = read(DATA / "youth-leaderboards.json", {})
    registry = read(DATA / "registry" / "players.json", [])
    aggregate = {str(row.get("fideID")): row for row in read(DATA / "index" / "by-player" / "players.json", [])}
    domestic = read(DATA / "registry" / "domestic" / "search-index.json", [])
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
    for row in domestic:
        payload = {key: row.get(key) for key in (
            "id", "domesticID", "displayName", "chineseName", "pinyin", "entityType", "publicIdentityStatus",
            "sightingCount", "detailPath", "publicLocation"
        ) if row.get(key) not in (None, "", False)}
        aliases = compact_aliases(row)
        if aliases:
            payload["aliases"] = aliases
        names = [str(value) for value in (row.get("eventNames") or []) if value][:2]
        if names:
            payload["eventNames"] = names
        players.append(payload)
    output = {
        "schemaVersion": 1,
        "generatedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "competitionYear": youth.get("competitionYear"),
        "ageRule": youth.get("ageRule"),
        "totals": {"players": len(players), "fide": len(registry), "domestic": len(domestic)},
        "players": players,
    }
    OUTPUT.write_text(json.dumps(output, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps({"players": len(players), "bytes": OUTPUT.stat().st_size}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
