#!/usr/bin/env python3
"""Validation gate for brilliancies datasets, shards, PGN snippets and OpenAPI specs.

Ensures:
- Every brilliancy ID strictly matches br-<sha256(fingerprint:ply:uci)>.
- FEN before is legal and reachable; move UCI is legal and matches SAN.
- All continuation and analysis lines are strictly legal move sequences.
- Registry authority is preserved: player display names must match registry.
- Privacy & De-sourcing: no private paths (/Volumes/, Scripts/local/), no chess-results links.
- Withdrawn items are omitted from items.json and manifest totals.
- Size budget: manifest.json < 1 MiB, each shard < 2 MiB.
- Shard coverage: every item in items.json exists in its designated hex shard.
- PGN snippets parse cleanly and contain required SetUp/FEN and NAG $3 (!!).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import pathlib
import re
import sys
from typing import Any, Dict, List, Optional

import chess
import chess.pgn

ROOT = pathlib.Path(__file__).resolve().parents[1]
import logging
import build_static_player_pgn as pgn_helper
import brilliancy_archive
from snapshot_context import snapshot_id

logging.getLogger("chess.pgn").setLevel(logging.CRITICAL)

DATA_DIR = ROOT / "docs/data/brilliancies"
SHARDS_DIR = DATA_DIR / "shards"
PGN_DIR = DATA_DIR / "pgn"
REGISTRY_PLAYERS = ROOT / "docs/data/registry/players.json"

FORBIDDEN_PATTERNS = [
    re.compile(r"chess-results\.com", re.IGNORECASE),
    re.compile(r"/Volumes/", re.IGNORECASE),
    re.compile(r"Scripts/local/", re.IGNORECASE),
    re.compile(r"sourceRefs", re.IGNORECASE),
]


def compute_brilliancy_id(game_fingerprint: str, ply: int, move_uci: str) -> str:
    seed = f"{game_fingerprint}:{ply}:{move_uci}".encode("utf-8")
    return "br-" + hashlib.sha256(seed).hexdigest()


def validate_privacy(text: str, context: str) -> None:
    for pattern in FORBIDDEN_PATTERNS:
        if pattern.search(text):
            raise ValueError(f"BRILLIANCY_PRIVACY_VIOLATION in {context}: matched {pattern.pattern}")


def validate_brilliancies(
    root: pathlib.Path = ROOT,
    expected_snapshot: Optional[str] = None,
) -> Dict[str, Any]:
    data_dir = root / "docs/data/brilliancies"
    shards_dir = data_dir / "shards"
    pgn_dir = data_dir / "pgn"
    registry_path = root / "docs/data/registry/players.json"

    manifest_path = data_dir / "manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError("BRILLIANCIES_MANIFEST_MISSING")

    # Check manifest size
    if manifest_path.stat().st_size > 1024 * 1024:
        raise ValueError("BRILLIANCIES_MANIFEST_SIZE_EXCEEDED (> 1MB)")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    if expected_snapshot:
        sid = expected_snapshot
    elif "SNAPSHOT_ID" in os.environ:
        sid = os.environ["SNAPSHOT_ID"]
    else:
        sid = manifest.get("snapshotId")

    if manifest.get("snapshotId") != sid:
        raise ValueError(
            f"BRILLIANCIES_SNAPSHOT_MISMATCH: expected {sid}, got {manifest.get('snapshotId')}"
        )
    if manifest.get("schemaVersion") != 1:
        raise ValueError("BRILLIANCIES_INVALID_SCHEMA_VERSION")

    items_path = data_dir / "items.json"
    if not items_path.is_file():
        raise RuntimeError("BRILLIANCIES_ITEMS_MISSING")

    items = json.loads(items_path.read_text(encoding="utf-8"))
    if len(items) != manifest.get("total"):
        raise ValueError(f"ITEMS_TOTAL_MISMATCH: manifest={manifest.get('total')}, items={len(items)}")

    # Load registry
    registry = {}
    if registry_path.is_file():
        for p in json.loads(registry_path.read_text(encoding="utf-8")):
            fide_id = p.get("fideID")
            if fide_id:
                registry[str(fide_id)] = p

    # Verify the exact declared layout; stale shards must not be served by ID.
    prefix_length = manifest.get("shardPrefixLength", 1)
    pgn_layout = manifest.get("pgnLayout", "files")
    if prefix_length not in (1, 2) or pgn_layout not in ("files", "shards"):
        raise ValueError("BRILLIANCIES_SHARD_LAYOUT_INVALID")
    if (prefix_length == 2) != (pgn_layout == "shards"):
        raise ValueError("BRILLIANCIES_SHARD_LAYOUT_INVALID")
    expected_keys = {f"{i:0{prefix_length}x}" for i in range(16 ** prefix_length)}
    declared_shards = manifest.get("shards", [])
    if (not isinstance(declared_shards, list) or len(declared_shards) != len(expected_keys)
            or set(declared_shards) != expected_keys):
        raise ValueError("BRILLIANCIES_SHARD_MANIFEST_INVALID")
    if {path.stem for path in shards_dir.glob("*.json")} != expected_keys:
        raise ValueError("BRILLIANCIES_SHARD_FILES_INVALID")
    shards: Dict[str, Dict[str, Any]] = {}
    packed_pgns: Dict[str, Dict[str, str]] = {}
    for hex_char in sorted(expected_keys):
        shard_path = shards_dir / f"{hex_char}.json"
        if not shard_path.is_file():
            raise RuntimeError(f"BRILLIANCIES_SHARD_MISSING: {hex_char}.json")
        if shard_path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError(f"BRILLIANCIES_SHARD_SIZE_EXCEEDED: {hex_char}.json (> 2MB)")
        shard_data = json.loads(shard_path.read_text(encoding="utf-8"))
        if shard_data.get("snapshotId") != sid:
            raise ValueError(f"SHARD_SNAPSHOT_MISMATCH: {hex_char}.json")
        if shard_data.get("bucket") != hex_char:
            raise ValueError(f"SHARD_BUCKET_MISMATCH: {hex_char}.json")
        bucket_items = shard_data.get("items", {})
        if not isinstance(bucket_items, dict) or shard_data.get("total") != len(bucket_items):
            raise ValueError(f"SHARD_TOTAL_MISMATCH: {hex_char}.json")
        shards[hex_char] = bucket_items
        packed_pgns[hex_char] = shard_data.get("pgn", {})
        if not isinstance(packed_pgns[hex_char], dict):
            raise ValueError(f"SHARD_PGN_INVALID: {hex_char}.json")
        if pgn_layout == "files" and packed_pgns[hex_char]:
            raise ValueError(f"SHARD_PGN_LAYOUT_MISMATCH: {hex_char}.json")

    published_ids = set()
    player_games_cache: Dict[str, set] = {}
    global_archive_cache = None
    wanted_fingerprints = {row.get("game", {}).get("fingerprint") for row in items}
    wanted_fingerprints.discard(None)

    for idx, item in enumerate(items):
        b_id = item.get("id", "")
        if not b_id.startswith("br-"):
            raise ValueError(f"INVALID_BRILLIANCY_ID_FORMAT: {b_id}")

        hex_char = b_id.removeprefix("br-")[:prefix_length].lower()
        if b_id not in shards[hex_char]:
            raise ValueError(f"ITEM_MISSING_FROM_SHARD: {b_id} not in shard {hex_char}")

        full_item = shards[hex_char][b_id]
        published_ids.add(b_id)

        # Check status
        status = full_item.get("status")
        if status not in ("published", "withdrawn"):
            raise ValueError(f"INVALID_STATUS in {b_id}: expected 'published' or 'withdrawn', got '{status}'")

        # Privacy check on full item json
        validate_privacy(json.dumps(full_item, ensure_ascii=False), f"item {b_id}")

        # Check ID formula
        fp = full_item.get("game", {}).get("fingerprint", "")
        ply = full_item.get("position", {}).get("ply")
        fen_before = full_item.get("position", {}).get("fenBefore", "")
        move_uci = full_item.get("move", {}).get("uci", "")
        if not (fp and ply is not None and move_uci and fen_before):
            raise ValueError(f"BRILLIANCY_MISSING_ID_FIELDS: {b_id}")

        expected_id = compute_brilliancy_id(fp, ply, move_uci)
        if b_id != expected_id:
            raise ValueError(f"BRILLIANCY_ID_CALCULATION_MISMATCH: {b_id} != {expected_id}")

        # Validate verification object
        ver = full_item.get("verification")
        if not ver or not isinstance(ver, dict):
            raise ValueError(f"MISSING_VERIFICATION in {b_id}")
        if not ver.get("engine"):
            raise ValueError(f"MISSING_ENGINE_IN_VERIFICATION in {b_id}")
        nodes = ver.get("nodes", 0)
        if not isinstance(nodes, (int, float)) or nodes < 100000:
            raise ValueError(f"INSUFFICIENT_NODES in {b_id}: {nodes} < 100000")
        depth = ver.get("depth", 0)
        if not isinstance(depth, int) or depth < 14:
            raise ValueError(f"INSUFFICIENT_DEPTH in {b_id}: {depth} < 14")
        eval_info = ver.get("evaluation")
        if not eval_info or not isinstance(eval_info, dict):
            raise ValueError(f"MISSING_EVALUATION in {b_id}")
        score_type = eval_info.get("type")
        score_val = eval_info.get("score")
        if score_type == "mate":
            if not isinstance(score_val, int) or score_val <= 0:
                raise ValueError(f"NON_WINNING_MATE_IN_VERIFICATION in {b_id}: {score_val}")
        elif score_type == "cp":
            if not isinstance(score_val, (int, float)) or score_val < 80:
                raise ValueError(f"INSUFFICIENT_CP_IN_VERIFICATION in {b_id}: {score_val}")
        else:
            raise ValueError(f"UNKNOWN_EVAL_TYPE in {b_id}: {score_type}")

        if "wdl" in eval_info:
            raise ValueError(f"UNTRUSTED_WDL_IN_VERIFICATION in {b_id}: WDL must not be fabricated")

        # Validate game.id and replay game from archive to exact ply
        game_obj = full_item.get("game", {})
        gid = game_obj.get("id", "")
        if not gid:
            raise ValueError(f"GAME_ID_MISSING in {b_id}")

        fide_candidates = []
        for side in ("white", "black"):
            side_fid = str(full_item.get(side, {}).get("playerId") or "").removeprefix("fide-")
            if side_fid and side_fid not in fide_candidates:
                fide_candidates.append(side_fid)

        found_game = None
        archive_checked = 0
        for fid in fide_candidates:
            player_pgn = root / f"docs/data/pgn/by-player/fide-{fid}/all.pgn"
            if player_pgn.is_file():
                archive_checked += 1
                if fid not in player_games_cache:
                    cached_games = {}
                    text = player_pgn.read_text(encoding="utf-8")
                    for raw in pgn_helper.split_pgn_games(text):
                        g = chess.pgn.read_game(io.StringIO(raw))
                        if not g or g.errors:
                            continue
                        cached_games[pgn_helper.stable_game_hash(raw)] = g
                        exp = chess.pgn.StringExporter(headers=True, variations=False, comments=False)
                        cached_games[pgn_helper.game_fingerprint(g.accept(exp))] = g
                    player_games_cache[fid] = cached_games

                if gid in player_games_cache[fid]:
                    found_game = player_games_cache[fid][gid]
                    break

        if found_game is None:
            if global_archive_cache is None:
                global_archive_cache = brilliancy_archive.matching_games(root, wanted_fingerprints)
            raw = global_archive_cache.get(fp, {}).get(gid)
            if raw:
                found_game = chess.pgn.read_game(io.StringIO(raw))
                if not found_game or found_game.errors:
                    raise ValueError(f"ORIGINAL_GAME_PARSE_INVALID in {b_id}")

        if archive_checked == 0 and found_game is None:
            raise ValueError(
                f"ORIGINAL_GAME_ARCHIVE_MISSING in {b_id}: "
                f"no archive found for players {fide_candidates}"
            )
        if not found_game:
            raise ValueError(
                f"GAME_NOT_FOUND_IN_ARCHIVE in {b_id}: "
                f"game id '{gid}' / fingerprint {fp} not found in player archives {fide_candidates}"
            )

        if game_obj.get("initialFen") != found_game.board().fen():
            raise ValueError(f"INITIAL_FEN_MISMATCH in {b_id}")
        if game_obj.get("result") != found_game.headers.get("Result"):
            raise ValueError(f"GAME_RESULT_MISMATCH in {b_id}")
        if pgn_helper.game_fingerprint(found_game.accept(chess.pgn.StringExporter(headers=True, variations=False, comments=False))) != fp:
            raise ValueError(f"GAME_FINGERPRINT_MISMATCH in {b_id}")

        # Mandatory replay to specified ply to prove position and move appear in the game
        moves = list(found_game.mainline_moves())
        if ply > len(moves):
            raise ValueError(
                f"PLY_EXCEEDS_GAME_LENGTH in {b_id}: ply {ply} > total game moves {len(moves)}"
            )

        replayed_board = found_game.board()
        for step_idx in range(ply):
            replayed_board.push(moves[step_idx])

        actual_fen = replayed_board.fen()
        if actual_fen != fen_before:
            raise ValueError(
                f"FEN_BEFORE_MISMATCH_WITH_ARCHIVE in {b_id}: "
                f"replayed board at ply {ply} ({actual_fen}) != "
                f"expected fenBefore ({fen_before})"
            )

        expected_stm = "white" if replayed_board.turn == chess.WHITE else "black"
        actual_stm = full_item.get("position", {}).get("sideToMove")
        if actual_stm and actual_stm != expected_stm:
            raise ValueError(
                f"SIDE_TO_MOVE_MISMATCH in {b_id}: expected {expected_stm}, got {actual_stm}"
            )

        expected_fullmove = replayed_board.fullmove_number
        actual_fullmove = full_item.get("position", {}).get("fullmoveNumber")
        if actual_fullmove is not None and actual_fullmove != expected_fullmove:
            raise ValueError(
                f"FULLMOVE_NUMBER_MISMATCH in {b_id}: expected {expected_fullmove}, got {actual_fullmove}"
            )

        if ply >= len(moves):
            raise ValueError(
                f"MOVE_NOT_PLAYED_AT_PLY in {b_id}: game ended before brilliancy ply {ply}"
            )
        played_move = moves[ply]
        if played_move.uci() != move_uci:
            raise ValueError(
                f"MOVE_NOT_PLAYED_AT_PLY in {b_id}: "
                f"move at ply {ply} was {played_move.uci()} ({replayed_board.san(played_move)}), "
                f"expected {move_uci}"
            )

        # Validate full game moves against archive mainline
        game_moves_uci = game_obj.get("movesUci")
        if game_moves_uci is not None:
            if not isinstance(game_moves_uci, list):
                raise ValueError(f"INVALID_GAME_MOVES_TYPE in {b_id}: movesUci must be a list")
            if len(game_moves_uci) != len(moves):
                raise ValueError(
                    f"GAME_MOVES_COUNT_MISMATCH in {b_id}: "
                    f"movesUci length {len(game_moves_uci)} != archive mainline length {len(moves)}"
                )
            for m_idx, m in enumerate(moves):
                if game_moves_uci[m_idx] != m.uci():
                    raise ValueError(
                        f"GAME_MOVE_MISMATCH_WITH_ARCHIVE in {b_id}: "
                        f"at move index {m_idx}, movesUci is {game_moves_uci[m_idx]}, archive is {m.uci()}"
                    )

        game_moves_san = game_obj.get("movesSan")
        if game_moves_san is not None:
            if not isinstance(game_moves_san, list):
                raise ValueError(f"INVALID_GAME_MOVES_SAN_TYPE in {b_id}: movesSan must be a list")
            if len(game_moves_san) != len(moves):
                raise ValueError(
                    f"GAME_MOVES_SAN_COUNT_MISMATCH in {b_id}: "
                    f"movesSan length {len(game_moves_san)} != archive mainline length {len(moves)}"
                )
            san_board = found_game.board()
            for m_idx, m in enumerate(moves):
                expected_san = san_board.san(m)
                if game_moves_san[m_idx] != expected_san:
                    raise ValueError(
                        f"GAME_MOVE_SAN_MISMATCH_WITH_ARCHIVE in {b_id}: "
                        f"at move index {m_idx}, movesSan is {game_moves_san[m_idx]}, archive is {expected_san}"
                    )
                san_board.push(m)

        if game_obj.get("totalMoves") is not None and game_obj.get("totalMoves") != len(moves):
            raise ValueError(
                f"TOTAL_MOVES_MISMATCH in {b_id}: "
                f"totalMoves {game_obj.get('totalMoves')} != archive length {len(moves)}"
            )

        # Validate actual continuation moves against archive mainline
        cont_moves = full_item.get("actualContinuationUci", [])
        if not isinstance(cont_moves, list):
            raise ValueError(f"INVALID_CONTINUATION_TYPE in {b_id}: actualContinuationUci must be a list")
        for c_idx, c_uci in enumerate(cont_moves):
            target_ply = ply + 1 + c_idx
            if target_ply >= len(moves):
                raise ValueError(
                    f"CONTINUATION_EXCEEDS_GAME_LENGTH in {b_id}: "
                    f"continuation step {c_idx} (ply {target_ply}) exceeds archive length {len(moves)}"
                )
            if moves[target_ply].uci() != c_uci:
                raise ValueError(
                    f"CONTINUATION_NOT_PLAYED_IN_ARCHIVE in {b_id}: "
                    f"continuation step {c_idx} was {c_uci}, but archive played {moves[target_ply].uci()}"
                )

        # Validate board FEN and move legality
        try:
            board = chess.Board(fen_before)
        except ValueError as err:
            raise ValueError(f"INVALID_FEN_BEFORE in {b_id}: {err}") from err

        target_move = chess.Move.from_uci(move_uci)
        if target_move not in board.legal_moves:
            raise ValueError(f"ILLEGAL_BRILLIANCY_MOVE in {b_id}: {move_uci} in {fen_before}")

        san = board.san(target_move)
        if san != full_item.get("move", {}).get("san"):
            raise ValueError(f"SAN_MISMATCH in {b_id}: expected {san}, got {full_item.get('move', {}).get('san')}")

        # Check registry authority
        for side in ("white", "black"):
            p_info = full_item.get(side, {})
            p_id = p_info.get("playerId")
            if p_id:
                clean_fide = str(p_id).replace("fide-", "")
                if clean_fide in registry:
                    reg_entry = registry[clean_fide]
                    expected_name = (
                        reg_entry.get("chineseName")
                        or reg_entry.get("displayName")
                        or reg_entry.get("name")
                    )
                    if p_info.get("displayName") != expected_name:
                        raise ValueError(
                            f"REGISTRY_AUTHORITY_VIOLATION in {b_id}: {side} displayName "
                            f"'{p_info.get('displayName')}' != registry '{expected_name}'"
                        )

        # Check continuation legality
        temp_board = board.copy()
        temp_board.push(target_move)
        for c_uci in full_item.get("actualContinuationUci", []):
            m = chess.Move.from_uci(c_uci)
            if m not in temp_board.legal_moves:
                raise ValueError(f"ILLEGAL_CONTINUATION_MOVE in {b_id}: {c_uci}")
            temp_board.push(m)

        # Check analysis lines legality
        after = board.copy()
        after.push(target_move)
        for line in full_item.get("analysisLines", []):
            if line.get("fenBefore") != after.fen():
                raise ValueError(f"ANALYSIS_START_MISMATCH in {b_id}")
            line_fen = line.get("fenBefore", "")
            try:
                line_board = chess.Board(line_fen)
            except ValueError as err:
                raise ValueError(f"INVALID_ANALYSIS_FEN in {b_id}: {err}") from err
            for l_uci in line.get("uci", []):
                m = chess.Move.from_uci(l_uci)
                if m not in line_board.legal_moves:
                    raise ValueError(f"ILLEGAL_ANALYSIS_MOVE in {b_id}: {l_uci}")
                line_board.push(m)

        # Check rights attribution
        for r in full_item.get("rights", []):
            if "game-broadcast" in r.get("type", ""):
                if r.get("license") != "CC BY-SA 4.0":
                    raise ValueError(f"LICHESS_LICENSE_MISMATCH in {b_id}: expected CC BY-SA 4.0, got {r.get('license')}")
                if "lichess" not in r.get("attribution", "").lower():
                    raise ValueError(f"LICHESS_ATTRIBUTION_MISSING in {b_id}: attribution must mention Lichess")

        # Check PGN snippet
        pgn_file = pgn_dir / f"{b_id}.pgn"
        if pgn_layout == "shards":
            pgn_text = packed_pgns[hex_char].get(b_id)
            if not isinstance(pgn_text, str):
                raise RuntimeError(f"PGN_SNIPPET_MISSING: {b_id}.pgn")
        else:
            if not pgn_file.is_file():
                raise RuntimeError(f"PGN_SNIPPET_MISSING: {b_id}.pgn")
            pgn_text = pgn_file.read_text(encoding="utf-8")
        validate_privacy(pgn_text, f"pgn {b_id}")
        parsed_game = chess.pgn.read_game(io.StringIO(pgn_text))
        if not parsed_game or parsed_game.errors:
            raise ValueError(f"PGN_SNIPPET_PARSE_FAILED: {b_id}.pgn")
        if parsed_game.headers.get("SetUp") != "1" or not parsed_game.headers.get("FEN"):
            raise ValueError(f"PGN_SNIPPET_MISSING_SETUP_OR_FEN: {b_id}.pgn")

        mainline = list(parsed_game.mainline())
        if not mainline:
            raise ValueError(f"PGN_SNIPPET_EMPTY_MOVES in {b_id}.pgn: no moves in snippet")

        first_node = mainline[0]
        if first_node.move != target_move:
            raise ValueError(f"PGN_SNIPPET_FIRST_MOVE_MISMATCH in {b_id}.pgn: expected {target_move}, got {first_node.move}")
        if chess.pgn.NAG_BRILLIANT_MOVE not in first_node.nags:
            raise ValueError(f"PGN_SNIPPET_MISSING_BRILLIANCY_NAG ($3) in {b_id}.pgn on {first_node.san()}")

        actual_ucis = full_item.get("actualContinuationUci", [])
        mainline_ucis = [node.move.uci() for node in mainline[1:]]
        if actual_ucis and mainline_ucis[: len(actual_ucis)] != actual_ucis:
            raise ValueError(f"PGN_SNIPPET_CONTINUATION_MISMATCH in {b_id}.pgn")

    # Check that withdrawn items in shards are NOT in items.json and have NO pgn file
    for hex_char, bucket in shards.items():
        for item_id, item in bucket.items():
            if not item_id.removeprefix("br-").startswith(hex_char):
                raise ValueError(f"SHARD_ID_MISPLACED: {item_id}")
            if item.get("status") == "published" and item_id not in published_ids:
                raise ValueError(f"PUBLISHED_ITEM_MISSING_FROM_LIST: {item_id}")
            if item.get("status") == "withdrawn":
                if item_id in published_ids:
                    raise ValueError(f"WITHDRAWN_ITEM_PUBLISHED: {item_id} is in items.json")
                withdrawn_pgn = pgn_dir / f"{item_id}.pgn"
                if withdrawn_pgn.is_file():
                    raise ValueError(f"WITHDRAWN_PGN_STILL_EXISTS: {item_id}.pgn should be deleted")
                if item_id in packed_pgns[hex_char]:
                    raise ValueError(f"WITHDRAWN_PGN_STILL_EXISTS: {item_id}.pgn should be deleted")
        if pgn_layout == "shards" and set(packed_pgns[hex_char]) != {
                item_id for item_id, item in bucket.items() if item.get("status") == "published"}:
            raise ValueError(f"SHARD_PGN_COVERAGE_MISMATCH: {hex_char}.json")
    if pgn_layout == "shards" and any(pgn_dir.glob("*.pgn")):
        raise ValueError("BRILLIANCIES_STALE_PGN_FILES")
    if pgn_layout == "files" and {path.stem for path in pgn_dir.glob("*.pgn")} != published_ids:
        raise ValueError("BRILLIANCIES_PGN_FILES_MISMATCH")

    # Check OpenAPI spec
    openapi_path = data_dir / "openapi.json"
    if not openapi_path.is_file():
        raise RuntimeError("OPENAPI_SPEC_MISSING")
    openapi_spec = json.loads(openapi_path.read_text(encoding="utf-8"))
    if not openapi_spec.get("openapi", "").startswith("3.1"):
        raise ValueError("OPENAPI_VERSION_MISMATCH: expected 3.1.x")

    return {
        "snapshotId": sid,
        "validItems": len(items),
        "shardsChecked": len(shards),
        "manifestBytes": manifest_path.stat().st_size,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=pathlib.Path, default=ROOT)
    parser.add_argument("--snapshot", type=str, default=None)
    args = parser.parse_args()

    result = validate_brilliancies(args.root, args.snapshot)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
