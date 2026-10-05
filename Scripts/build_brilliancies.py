#!/usr/bin/env python3
"""Build public brilliancy projections, shards, PGN snippets and OpenAPI specs.

This script executes as part of the derived-data rebuild pipeline.
It reads curated/approved brilliancies from data/manual/brilliancies/,
verifies registry authority, partitions items into deterministic hex shards,
generates annotated PGN snippets, writes the OpenAPI specification,
and outputs docs/data/brilliancies/manifest.json.

Design Constraints:
- Registry is authoritative for player display names and ratings.
- Deterministic sharding by one or two ID hash digits as the catalogue grows.
- File size budget: manifest < 1 MiB, each shard bucket < 2 MiB.
- No source links or private capture paths in public projections.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import io
import os
import pathlib
import sys
from typing import Any, Dict, List, Optional

import chess
import chess.pgn

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Scripts"))

from snapshot_context import snapshot_id
from stable_json import write_json
import build_static_player_pgn as pgn_helper
import brilliancy_archive

CURATED_PATH = ROOT / "data/manual/brilliancies/curated.json"
REGISTRY_PLAYERS = ROOT / "docs/data/registry/players.json"
PUBLIC_EVENTS = ROOT / "docs/data/index/public-events.json"

TARGET_DATA_DIR = ROOT / "docs/data/brilliancies"
TARGET_SHARDS_DIR = TARGET_DATA_DIR / "shards"
TARGET_PGN_DIR = TARGET_DATA_DIR / "pgn"
TARGET_API_DIR = ROOT / "docs/api/v1/brilliancies"


def compute_brilliancy_id(game_fingerprint: str, ply: int, move_uci: str) -> str:
    seed = f"{game_fingerprint}:{ply}:{move_uci}".encode("utf-8")
    return "br-" + hashlib.sha256(seed).hexdigest()


def load_registry(registry_path: pathlib.Path) -> Dict[str, Dict[str, Any]]:
    if not registry_path.is_file():
        return {}
    players = json.loads(registry_path.read_text(encoding="utf-8"))
    return {str(p.get("fideID")): p for p in players if p.get("fideID")}


def generate_annotated_pgn(item: Dict[str, Any]) -> str:
    """Generate clean standalone PGN snippet for a brilliancy using python-chess Game tree."""
    game = chess.pgn.Game()
    game.headers["Event"] = item.get("event", {}).get("name", "ChessDB Match")
    has_lichess = any(
        "lichess" in r.get("attribution", "").lower() or "cc by-sa" in r.get("license", "").lower()
        for r in item.get("rights", [])
    )
    if has_lichess:
        game.headers["Site"] = "ChessDB / Lichess Broadcast"
        game.headers["License"] = "CC BY-SA 4.0"
        game.headers["Source"] = "Lichess Broadcast (CC BY-SA 4.0)"
    else:
        game.headers["Site"] = "ChessDB"
        game.headers["License"] = "CC BY 4.0"

    game.headers["Date"] = item.get("event", {}).get("date") or "????"
    game.headers["White"] = item.get("white", {}).get("displayName", "White")
    game.headers["Black"] = item.get("black", {}).get("displayName", "Black")
    original = item.get("game", {})
    complete = item["position"]["ply"] + 1 + len(item.get("actualContinuationUci", [])) == len(original.get("movesUci", []))
    game.headers["Result"] = original.get("result", "*") if complete else "*"
    game.headers["OriginalResult"] = original.get("result", "*")
    fen_before = item.get("position", {}).get("fenBefore", "")
    if fen_before:
        game.setup(chess.Board(fen_before))

    board = game.board()
    move_uci = item.get("move", {}).get("uci", "")
    m = None
    try:
        if move_uci:
            m = chess.Move.from_uci(move_uci)
        else:
            m = board.parse_san(item.get("move", {}).get("san", ""))
    except Exception:
        m = None

    if m and m in board.legal_moves:
        brilliancy_node = game.add_variation(m)
        brilliancy_node.nags.add(chess.pgn.NAG_BRILLIANT_MOVE)
        comment = f"{item.get('title', '')}: {item.get('summary', '')}".strip(": ")
        if comment:
            brilliancy_node.comment = comment

        # Add actual continuation along main line
        curr = brilliancy_node
        for uci in item.get("actualContinuationUci", []):
            try:
                mv = chess.Move.from_uci(uci)
                curr = curr.add_variation(mv)
            except Exception:
                break

        # Add analysis lines as side variations
        for line in item.get("analysisLines", []):
            line_title = line.get("title", "")
            var_curr = brilliancy_node
            for idx, uci in enumerate(line.get("uci", [])):
                try:
                    mv = chess.Move.from_uci(uci)
                    child = None
                    for v in var_curr.variations:
                        if v.move == mv:
                            child = v
                            break
                    if child is None:
                        child = var_curr.add_variation(mv)
                        if idx == 0 and line_title:
                            child.comment = f"[{line_title}]"
                    var_curr = child
                except Exception:
                    break

    exporter = chess.pgn.StringExporter(headers=True, variations=True, comments=True)
    return game.accept(exporter) + "\n"


def build_openapi_spec() -> Dict[str, Any]:
    """Generate OpenAPI 3.1 specification for the brilliancies API."""
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "ChessDB 实战妙手 API",
            "version": "1.0.0",
            "description": "精选中国棋手及经典比赛中经引擎复核成立的实战妙手数据接口。",
            "license": {
                "name": "CC BY 4.0 / CC BY-SA 4.0",
                "url": "https://chessdb.aigclabs.cc/methodology",
            },
        },
        "servers": [
            {"url": "https://chessdb.aigclabs.cc"},
            {"url": "https://china-chess-player-pgn.pages.dev"},
        ],
        "paths": {
            "/api/v1/brilliancies/manifest.json": {
                "get": {
                    "summary": "妙手数据集元信息与分片清单",
                    "operationId": "getBrillianciesManifest",
                    "responses": {
                        "200": {
                            "description": "成功返回元数据与分片清单",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Manifest"}
                                }
                            },
                        }
                    },
                }
            },
            "/api/v1/brilliancies": {
                "get": {
                    "summary": "获取妙手列表（支持筛选与分页）",
                    "operationId": "listBrilliancies",
                    "parameters": [
                        {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 20, "maximum": 100}},
                        {"name": "cursor", "in": "query", "schema": {"type": "string"}},
                        {"name": "player", "in": "query", "schema": {"type": "string"}, "description": "棋手 FIDE ID（如 fide-8602980）"},
                        {"name": "theme", "in": "query", "schema": {"type": "string"}, "description": "妙手战术主题（如 queen-sacrifice）"},
                        {"name": "event", "in": "query", "schema": {"type": "string"}, "description": "赛事 ID"},
                        {"name": "sort", "in": "query", "schema": {"type": "string", "enum": ["featured", "newest"], "default": "featured"}},
                        {"name": "snapshot", "in": "query", "schema": {"type": "string"}, "description": "快照一致性校验参数"},
                    ],
                    "responses": {
                        "200": {
                            "description": "妙手摘要列表",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ItemListResponse"}
                                }
                            },
                        },
                        "400": {"description": "参数校验失败"},
                        "409": {"description": "快照已过期（snapshot_changed）"},
                    },
                }
            },
            "/api/v1/brilliancies/{id}.json": {
                "get": {
                    "summary": "获取单条妙手完整详情与引擎验证变化",
                    "operationId": "getBrilliancyDetail",
                    "parameters": [
                        {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "snapshot", "in": "query", "schema": {"type": "string"}},
                    ],
                    "responses": {
                        "200": {
                            "description": "完整妙手对象",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/BrilliancyDetail"}
                                }
                            },
                        },
                        "404": {"description": "条目不存在"},
                        "410": {"description": "条目已被审核撤回（withdrawn）"},
                    },
                }
            },
            "/api/v1/brilliancies/{id}.pgn": {
                "get": {
                    "summary": "获取带注释的妙手片段 PGN",
                    "operationId": "getBrilliancyPgn",
                    "parameters": [
                        {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {
                        "200": {
                            "description": "PGN 格式棋谱片段",
                            "content": {"application/x-chess-pgn": {"schema": {"type": "string"}}},
                        },
                        "404": {"description": "条目不存在"},
                    },
                }
            },
        },
        "components": {
            "schemas": {
                "Manifest": {
                    "type": "object",
                    "required": ["schemaVersion", "snapshotId", "total", "themes", "shards"],
                    "properties": {
                        "schemaVersion": {"type": "integer"},
                        "snapshotId": {"type": "string"},
                        "total": {"type": "integer"},
                        "themes": {"type": "array", "items": {"type": "string"}},
                        "engineVersion": {"type": "string"},
                        "ruleVersion": {"type": "string"},
                        "shards": {"type": "array", "items": {"type": "string"}},
                        "shardPrefixLength": {"type": "integer", "enum": [1, 2]},
                        "pgnLayout": {"type": "string", "enum": ["files", "shards"]},
                        "scanCoverage": {"type": "object"},
                    },
                },
                "ItemListResponse": {
                    "type": "object",
                    "required": ["schemaVersion", "snapshotId", "total", "items", "nextCursor"],
                    "properties": {
                        "schemaVersion": {"type": "integer"},
                        "snapshotId": {"type": "string"},
                        "total": {"type": "integer"},
                        "items": {"type": "array", "items": {"$ref": "#/components/schemas/ItemSummary"}},
                        "nextCursor": {"type": ["string", "null"]},
                    },
                },
                "ItemSummary": {
                    "type": "object",
                    "required": ["id", "title", "summary", "white", "black", "position", "move", "themes", "links"],
                    "properties": {
                        "id": {"type": "string"},
                        "title": {"type": "string"},
                        "summary": {"type": "string"},
                        "white": {"type": "object"},
                        "black": {"type": "object"},
                        "position": {"type": "object"},
                        "move": {"type": "object"},
                        "themes": {"type": "array", "items": {"type": "string"}},
                        "links": {"type": "object"},
                    },
                },
                "BrilliancyDetail": {
                    "type": "object",
                    "required": ["id", "schemaVersion", "game", "white", "black", "position", "move", "classification", "verification", "links"],
                    "properties": {
                        "id": {"type": "string"},
                        "schemaVersion": {"type": "integer"},
                        "snapshotId": {"type": "string"},
                        "title": {"type": "string"},
                        "summary": {"type": "string"},
                        "explanation": {"type": "string"},
                        "game": {"type": "object"},
                        "event": {"type": "object"},
                        "white": {"type": "object"},
                        "black": {"type": "object"},
                        "position": {"type": "object"},
                        "move": {"type": "object"},
                        "classification": {"type": "object"},
                        "themes": {"type": "array", "items": {"type": "string"}},
                        "verification": {"type": "object"},
                        "actualContinuationUci": {"type": "array", "items": {"type": "string"}},
                        "actualContinuationSan": {"type": "array", "items": {"type": "string"}},
                        "analysisLines": {"type": "array", "items": {"type": "object"}},
                        "links": {"type": "object"},
                        "status": {"type": "string", "enum": ["published", "withdrawn"]},
                    },
                },
            }
        },
    }


def build(root: pathlib.Path = ROOT, sid: Optional[str] = None,
          shard_prefix_length: Optional[int] = None) -> Dict[str, Any]:
    root = pathlib.Path(root).resolve()
    sid = sid or snapshot_id()
    now_iso = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()

    curated_path = root / "data/manual/brilliancies/curated.json"
    registry_players = root / "docs/data/registry/players.json"
    target_data_dir = root / "docs/data/brilliancies"
    target_shards_dir = target_data_dir / "shards"
    target_pgn_dir = target_data_dir / "pgn"
    target_api_dir = root / "docs/api/v1/brilliancies"

    registry = load_registry(registry_players)

    # Load curated data
    if not curated_path.is_file():
        raise RuntimeError(f"CURATED_BRILLIANCIES_MISSING: {curated_path}")

    curated_data = json.loads(curated_path.read_text(encoding="utf-8"))
    raw_items = curated_data.get("items", [])
    scan_coverage = {"status": "not-measured", "published": 0}
    if shard_prefix_length is None:
        shard_prefix_length = 2 if len(raw_items) > 255 else 1
    if shard_prefix_length not in (1, 2):
        raise ValueError("BRILLIANCY_SHARD_PREFIX_INVALID")
    packed_pgn = shard_prefix_length == 2


    archive_cache = {}
    global_archive_cache = None
    wanted_fingerprints = {row["game"]["fingerprint"] for row in raw_items}
    published_items = []
    published_ids = set()
    seen_ids = set()
    shard_keys = [f"{i:0{shard_prefix_length}x}" for i in range(16 ** shard_prefix_length)]
    shards: Dict[str, Dict[str, Any]] = {key: {} for key in shard_keys}
    shard_pgn: Dict[str, Dict[str, str]] = {key: {} for key in shard_keys}

    # Ensure directories exist
    target_shards_dir.mkdir(parents=True, exist_ok=True)
    target_pgn_dir.mkdir(parents=True, exist_ok=True)
    target_api_dir.mkdir(parents=True, exist_ok=True)

    themes_set = set()

    for item in raw_items:
        b_id = item["id"]
        status = item.get("status", "published")

        if status not in ("published", "withdrawn"):
            raise ValueError(f"INVALID_STATUS: {status}")
        if b_id in seen_ids:
            raise ValueError(f"DUPLICATE_BRILLIANCY_ID: {b_id}")
        seen_ids.add(b_id)

        # Verify ID formula
        expected_id = compute_brilliancy_id(
            item["game"]["fingerprint"],
            item["position"]["ply"],
            item["move"]["uci"],
        )
        if b_id != expected_id:
            raise ValueError(f"BRILLIANCY_ID_MISMATCH: expected {expected_id} but got {b_id}")

        # Enforce registry authority on player names
        for side in ("white", "black"):
            player_info = item.get(side, {})
            p_id = player_info.get("playerId")
            if p_id:
                clean_fide = str(p_id).replace("fide-", "")
                if clean_fide in registry:
                    reg_entry = registry[clean_fide]
                    authoritative_name = (
                        reg_entry.get("chineseName")
                        or reg_entry.get("displayName")
                        or reg_entry.get("name")
                    )
                    player_info["displayName"] = authoritative_name
                    player_info["playerId"] = f"fide-{clean_fide}"

        # A rebuild can choose a different provider copy of the same played game.
        # Rebind only through an exact, unique move fingerprint, never list order.
        white_id = str(item.get("white", {}).get("playerId") or "").removeprefix("fide-")
        archive_path = root / f"docs/data/pgn/by-player/fide-{white_id}/all.pgn"
        matches = set()
        if archive_path.is_file():
            if white_id not in archive_cache:
                by_fingerprint = {}
                for raw in pgn_helper.split_pgn_games(archive_path.read_text(encoding="utf-8")):
                    game = chess.pgn.read_game(io.StringIO(raw))
                    if not game or game.errors:
                        continue
                    clean = game.accept(chess.pgn.StringExporter(headers=True, variations=False, comments=False))
                    fp = pgn_helper.game_fingerprint(clean)
                    by_fingerprint.setdefault(fp, set()).add(pgn_helper.stable_game_hash(raw))
                archive_cache[white_id] = by_fingerprint
            matches = archive_cache[white_id].get(item["game"]["fingerprint"], set())
        if not matches and (not white_id or archive_path.is_file()):
            if global_archive_cache is None:
                global_archive_cache = brilliancy_archive.matching_games(root, wanted_fingerprints)
            matches = set(global_archive_cache.get(item["game"]["fingerprint"], {}))
        if matches and item["game"]["id"] not in matches:
            if len(matches) != 1:
                raise ValueError(f"ORIGINAL_GAME_MATCH_NOT_UNIQUE: {b_id}")
            item["game"]["id"] = next(iter(matches))
        if not matches and not white_id:
            raise ValueError(f"ORIGINAL_GAME_MATCH_NOT_UNIQUE: {b_id}")

        # Standardize links
        item["snapshotId"] = sid
        item["updatedAt"] = now_iso
        item["publishedAt"] = item.get("publishedAt", now_iso)
        event_id = item.get("event", {}).get("id")
        event_link = f"/events/{event_id}" if (event_id and event_id != "event-unknown") else None

        rights = item.get("rights") or [
            {
                "type": "database-selection",
                "license": "CC BY 4.0",
                "attribution": "ChessDB 社区审核精选",
            }
        ]

        white_fide = str(item.get("white", {}).get("playerId") or "").removeprefix("fide-")
        gid = item.get("game", {}).get("id", "")
        ply = item.get("position", {}).get("ply", 0)

        item["links"] = {
            "self": f"/api/v1/brilliancies/{b_id}.json",
            "page": f"/brilliancies.html?id={b_id}",
            "pgn": f"/api/v1/brilliancies/{b_id}.pgn",
            "game": f"/?fideID={white_fide}&game={gid}&ply={ply}" if white_fide and gid else None,
            "player": f"/players/{item['white']['playerId']}" if item.get("white", {}).get("playerId") else None,
            "event": event_link,
            "rights": rights,
        }

        # Shard bucket assignment
        bucket = b_id.removeprefix("br-")[:shard_prefix_length].lower()
        shards[bucket][b_id] = item

        # Generate PGN snippet: only published items get a public PGN file
        pgn_path = target_pgn_dir / f"{b_id}.pgn"
        if status == "published":
            pgn_text = generate_annotated_pgn(item)
            if packed_pgn:
                shard_pgn[bucket][b_id] = pgn_text
            else:
                pgn_path.write_text(pgn_text, encoding="utf-8")
            published_ids.add(b_id)

            summary = {
                "id": b_id,
                "snapshotId": sid,
                "title": item.get("title", ""),
                "summary": item.get("summary", ""),
                # The list is fetched on every browse. Full move arrays are
                # available from the detail shard after the user selects one
                # item; repeating them here makes a large approved set costly.
                "game": {key: item.get("game", {}).get(key)
                         for key in ("id", "fingerprint", "result", "totalMoves")
                         if item.get("game", {}).get(key) is not None},
                "white": item.get("white", {}),
                "black": item.get("black", {}),
                "event": item.get("event", {}),
                "position": item.get("position", {}),
                "move": {
                    "uci": item.get("move", {}).get("uci", ""),
                    "san": item.get("move", {}).get("san", ""),
                    "classification": item.get("classification", {}).get("symbol", "!!"),
                },
                "themes": item.get("themes", []),
                "verification": {
                    "engine": item.get("verification", {}).get("engine", "Stockfish 17.1"),
                    "evaluation": item.get("verification", {}).get("evaluation", {}),
                },
                "links": item["links"],
            }
            published_items.append(summary)
            for t in item.get("themes", []):
                themes_set.add(t)
        else:
            if pgn_path.is_file():
                pgn_path.unlink()

    # A packed snapshot must not retain thousands of old individual PGN files.
    for existing_pgn in target_pgn_dir.glob("*.pgn"):
        if packed_pgn or existing_pgn.stem not in published_ids:
            existing_pgn.unlink()

    # Sort published items (featured order as in curated)
    write_json(target_data_dir / "items.json", published_items)

    # Write shard files
    for bucket_char, bucket_items in shards.items():
        shard_payload = {
            "schemaVersion": 1,
            "snapshotId": sid,
            "bucket": bucket_char,
            "total": len(bucket_items),
            "items": bucket_items,
        }
        if packed_pgn:
            shard_payload["pgn"] = shard_pgn[bucket_char]
        shard_path = target_shards_dir / f"{bucket_char}.json"
        write_json(shard_path, shard_payload)
        # Check size limit: 2 MiB
        if shard_path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError(f"SHARD_SIZE_LIMIT_EXCEEDED: {shard_path} > 2MB")
    for old_shard in target_shards_dir.glob("*.json"):
        if old_shard.stem not in shards:
            old_shard.unlink()

    # Write manifest
    scan_coverage["published"] = len(published_items)
    manifest_payload = {
        "schemaVersion": 1,
        "snapshotId": sid,
        "total": len(published_items),
        "themes": sorted(list(themes_set)),
        "engineVersion": "Stockfish 17.1",
        "ruleVersion": "2026-09-v1",
        "shards": sorted(list(shards.keys())),
        "shardPrefixLength": shard_prefix_length,
        "pgnLayout": "shards" if packed_pgn else "files",
        "scanCoverage": scan_coverage,
        "generatedAt": now_iso,
    }
    manifest_path = target_data_dir / "manifest.json"
    write_json(manifest_path, manifest_payload)

    # Check manifest size limit: 1 MiB
    if manifest_path.stat().st_size > 1024 * 1024:
        raise ValueError("MANIFEST_SIZE_LIMIT_EXCEEDED: manifest.json > 1MB")

    # Write OpenAPI spec
    openapi_spec = build_openapi_spec()
    write_json(target_data_dir / "openapi.json", openapi_spec)

    # Also mirror static copies to target_api_dir for static hosting fallback
    write_json(target_api_dir / "manifest.json", manifest_payload)
    write_json(target_api_dir / "openapi.json", openapi_spec)

    report = {
        "snapshotId": sid,
        "published": len(published_items),
        "totalEntries": len(raw_items),
        "shards": len(shards),
        "themes": len(themes_set),
    }
    print(json.dumps(report, ensure_ascii=False))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=pathlib.Path, default=ROOT)
    parser.add_argument("--snapshot", type=str, default=None, help="Explicit snapshot ID")
    args = parser.parse_args()
    build(args.root, sid=args.snapshot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
