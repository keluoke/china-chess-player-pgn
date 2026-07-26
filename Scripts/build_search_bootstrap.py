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
import re

from pypinyin import lazy_pinyin

from stable_json import write_json

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "docs" / "data"
OUTPUT_CORE = DATA / "search-bootstrap.json"
OUTPUT_DOMESTIC = DATA / "search-bootstrap-domestic.json"
SHARD_ROOT = DATA / "search" / "domestic"
ROUTING_OUTPUT = DATA / "search" / "domestic-routing.json"
PRESENTATION_NAMES = DATA / "identity" / "presentation-names.json"
HANZI_BUCKETS = 64
SURNAME_PINYIN = {
    "单": ("shan",),
    "曾": ("zeng",),
    "仇": ("qiu",),
    "解": ("xie",),
    "查": ("zha",),
    "覃": ("qin",),
    "朴": ("piao",),
    "区": ("ou",),
    "乐": ("yue",),
}


def chinese_name_pinyin_aliases(value: str) -> list[str]:
    """Derive search-only pinyin variants without changing identity facts.

    The default pronunciation remains searchable. A small, explicit surname
    table adds common family-name readings for characters whose general
    dictionary reading differs. These values only enter derived search
    artifacts; they are never written back to registry/manual data.
    """
    name = str(value or "").strip()
    if not re.fullmatch(r"[\u4e00-\u9fff]{2,6}", name):
        return []
    default_parts = [part.casefold() for part in lazy_pinyin(name, strict=False) if part]
    if len(default_parts) != len(name):
        return []
    variants = [" ".join(default_parts)]
    for surname in SURNAME_PINYIN.get(name[0], ()):
        variants.append(" ".join([surname, *default_parts[1:]]))
    return list(dict.fromkeys(variant for variant in variants if variant))


def derived_search_aliases(row: dict) -> list[str]:
    existing = {
        re.sub(r"[^a-z]", "", str(value or "").casefold())
        for value in [row.get("pinyin"), *(row.get("aliases") or [])]
    }
    output = []
    for value in [
        row.get("displayName"),
        row.get("chineseName"),
        *(row.get("aliases") or []),
    ]:
        for alias in chinese_name_pinyin_aliases(str(value or "")):
            normalized = re.sub(r"[^a-z]", "", alias)
            if normalized and normalized not in existing:
                existing.add(normalized)
                output.append(alias)
    return output


def roman_search_values(row: dict) -> list[str]:
    return [
        str(value).strip()
        for value in [
            row.get("pinyin"),
            *(row.get("aliases") or []),
            *(row.get("searchAliases") or []),
        ]
        if value and re.search(r"[A-Za-z]", str(value))
    ]


def shard_keys(row: dict) -> list[str]:
    """On-demand search shards (review §5.3): a row lands in the bucket of
    its first hanzi character and in its pinyin-initial bucket, so the
    client only downloads what the current query prefix can match."""
    keys: set[str] = set()
    domestic_id = str(row.get("domesticID") or "").lower()
    match = re.fullmatch(r"domestic-([0-9a-f]+)", domestic_id)
    if match:
        keys.add(f"id{match.group(1)[0]}")
    name = str(row.get("displayName") or row.get("chineseName") or "")
    if name and "一" <= name[0] <= "鿿":
        keys.add(f"h{ord(name[0]) % HANZI_BUCKETS:02x}")
    for roman in roman_search_values(row):
        first = roman[0].lower()
        if first.isascii() and first.isalpha():
            keys.add(f"p{first}")
    for alias in row.get("aliases") or []:
        alias = str(alias).strip()
        if alias and "一" <= alias[0] <= "鿿":
            keys.add(f"h{ord(alias[0]) % HANZI_BUCKETS:02x}")
        elif alias and alias[0].isascii() and alias[0].isalpha():
            keys.add(f"p{alias[0].lower()}")
    if not keys:
        keys.add("p0")
    return sorted(keys)


def primary_search_shard(row: dict) -> str:
    """Choose one existing row shard as the routing destination."""
    name = str(row.get("displayName") or row.get("chineseName") or "")
    if name and "一" <= name[0] <= "鿿":
        return f"h{ord(name[0]) % HANZI_BUCKETS:02x}"
    pinyin = str(row.get("pinyin") or "").strip().lower()
    if pinyin and pinyin[0].isascii() and pinyin[0].isalpha():
        return f"p{pinyin[0]}"
    return shard_keys(row)[0]


def routing_terms(row: dict) -> set[str]:
    """Compact substring routes point at existing prefix shards.

    Rows stay single-copy in their current shards. Chinese character/bigram
    bigram routes fix middle-name recall without tripling the public search payload.
    Single-character queries intentionally keep using the existing prefix
    bucket, avoiding dozens of downloads for common characters.
    """
    terms: set[str] = set()
    for value in [
        row.get("displayName"),
        row.get("chineseName"),
        *(row.get("aliases") or []),
    ]:
        hanzi = [char for char in str(value or "") if "\u4e00" <= char <= "\u9fff"]
        terms.update(f"g:{hanzi[index]}{hanzi[index + 1]}" for index in range(len(hanzi) - 1))
    for value in roman_search_values(row):
        tokens = re.findall(r"[a-z]+", str(value or "").casefold())
        for normalized in [*tokens, "".join(tokens)]:
            if normalized:
                terms.add(f"p:{normalized}")
    return terms


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
    presentation_names = {
        str(row.get("fideID")): str(row.get("suggestedChineseName") or "")
        for row in read(PRESENTATION_NAMES, {}).get("players", [])
        if row.get("confidence") == "high"
        and row.get("displayPolicy") == "default"
        and row.get("suggestedChineseName")
    }

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
        search_aliases = derived_search_aliases({
            **row,
            "aliases": [
                *(row.get("aliases") or []),
                presentation_names.get(fide_id, ""),
            ],
        })
        if search_aliases:
            payload["searchAliases"] = search_aliases
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
            "domesticID", "displayName", "sightingCount", "publicLocation",
            "federation", "domesticEligibilityBasis"
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
        search_aliases = derived_search_aliases(row)
        if search_aliases:
            payload["searchAliases"] = search_aliases
        years = sorted({str(value) for value in (row.get("eventYears") or []) if value})
        if years:
            payload["eventYears"] = [years[0]] if len(years) == 1 else [years[0], years[-1]]
        domestic_rows.append(payload)

    import sys
    if str(ROOT / "Scripts") not in sys.path:
        sys.path.append(str(ROOT / "Scripts"))
    try:
        from snapshot_context import snapshot_id
        sid = snapshot_id()
    except Exception:
        sid = "unknown"

    generated_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    core = {
        "schemaVersion": 2,
        "snapshotId": sid,
        "generatedAt": generated_at,
        "competitionYear": youth.get("competitionYear"),
        "ageRule": youth.get("ageRule"),
        "totals": {"players": len(players) + len(domestic_rows), "fide": len(registry), "domestic": len(domestic_rows)},
        "deferred": {
            "domestic": "data/search-bootstrap-domestic.json",
            "domesticRouting": "data/search/domestic-routing.json",
        },
        "players": players,
    }
    write_json(OUTPUT_CORE, core, ensure_ascii=False, separators=(",", ":"))
    write_json(OUTPUT_DOMESTIC, {
        "schemaVersion": 2,
        "snapshotId": sid,
        "generatedAt": generated_at,
        "players": domestic_rows,
    }, ensure_ascii=False, separators=(",", ":"))

    # Prefix shards for on-demand loading; the monolith above remains the
    # deep-link / legacy fallback.
    shards: dict[str, list[dict]] = {}
    routes: dict[str, set[str]] = {}
    for row in domestic_rows:
        row_shards = shard_keys(row)
        for key in row_shards:
            shards.setdefault(key, []).append(row)
        route_shard = primary_search_shard(row)
        for term in routing_terms(row):
            routes.setdefault(term, set()).add(route_shard)
    SHARD_ROOT.mkdir(parents=True, exist_ok=True)
    written = set()
    for key, rows in shards.items():
        write_json(SHARD_ROOT / f"{key}.json", {
            "schemaVersion": 1,
            "snapshotId": sid,
            "generatedAt": generated_at,
            "players": rows,
        }, ensure_ascii=False, separators=(",", ":"))
        written.add(f"{key}.json")
    for stale in SHARD_ROOT.glob("*.json"):
        if stale.name not in written:
            stale.unlink()
    write_json(ROUTING_OUTPUT, {
        "schemaVersion": 1,
        "snapshotId": sid,
        "generatedAt": generated_at,
        "routes": {term: sorted(keys) for term, keys in sorted(routes.items())},
    }, ensure_ascii=False, separators=(",", ":"))
    shard_bytes = sum((SHARD_ROOT / name).stat().st_size for name in written)
    print(json.dumps({
        "corePlayers": len(players), "coreBytes": OUTPUT_CORE.stat().st_size,
        "domesticPlayers": len(domestic_rows), "domesticBytes": OUTPUT_DOMESTIC.stat().st_size,
        "domesticShards": len(written), "domesticShardBytes": shard_bytes,
        "domesticRoutingBytes": ROUTING_OUTPUT.stat().st_size,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
