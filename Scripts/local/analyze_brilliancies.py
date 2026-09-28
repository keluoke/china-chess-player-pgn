#!/usr/bin/env python3
"""Offline heuristic scanning and Stockfish verification for tactical brilliancies.

This script scans canonical PGN files, detects tactical piece sacrifices (queen,
rook, minor piece, exchange sacrifices), evaluates candidate positions using
Stockfish (2-stage: screen budget + deep verification budget), and outputs
structured candidate objects ready for curation and publication.

Rules & Governance:
- Pure offline maintainer tool; never accesses external network sources.
- Registry is authoritative for player identities and names.
- All IDs are deterministic: br-<sha256(fingerprint:ply:uci)>.
- Evaluations explicitly record POV (side to move who played the brilliancy).
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import hashlib
import json
import os
import pathlib
import sys
from typing import Any, Dict, List, Optional, Tuple

import chess
import chess.engine
import chess.pgn

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Scripts"))

import build_static_player_pgn as pgn_helper
from stable_json import write_json

PIECE_VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,
}


def compute_brilliancy_id(game_fingerprint: str, ply: int, move_uci: str) -> str:
    """Deterministic, immutable ID based on game fingerprint, 0-indexed ply and move UCI."""
    seed = f"{game_fingerprint}:{ply}:{move_uci}".encode("utf-8")
    return "br-" + hashlib.sha256(seed).hexdigest()


def detect_sacrifice_moves(game: chess.pgn.Game) -> List[Dict[str, Any]]:
    """Scan game mainline for tactical piece sacrifice candidates."""
    board = game.board()
    candidates = []
    prev_captured_type: Optional[int] = None
    prev_captured_square: Optional[int] = None

    ply = 0  # Index within this PGN, including games with a custom starting FEN.
    for node in game.mainline():
        move = node.move
        mover = board.turn
        piece = board.piece_at(move.from_square)
        if not piece:
            board.push(move)
            ply += 1
            continue

        piece_type = piece.piece_type
        captured_piece = board.piece_at(move.to_square)
        captured_type = (
            captured_piece.piece_type
            if captured_piece
            else (chess.PAWN if board.is_en_passant(move) else None)
        )

        # Skip immediate direct equal-material recapture on same square
        is_direct_recapture = False
        if prev_captured_type and captured_type:
            if (
                move.to_square == prev_captured_square
                and PIECE_VALUES.get(captured_type, 0) == PIECE_VALUES.get(prev_captured_type, 0)
            ):
                is_direct_recapture = True

        # Tactical brilliancies rarely occur before move 8 (ply 15) in master play
        if (board.fullmove_number - 1) * 2 + int(not board.turn) >= 15 and not is_direct_recapture:
            # Check piece sacrifices: Q, R, B, N
            val_moved = PIECE_VALUES.get(piece_type, 0)
            val_captured = PIECE_VALUES.get(captured_type, 0) if captured_type else 0

            # Look ahead at position after the move
            board_after = board.copy()
            board_after.push(move)

            # Can opponent legally capture the piece at move.to_square?
            # Note: board_after.legal_moves correctly accounts for absolute pins to the King
            opp_captures = [op for op in board_after.legal_moves if op.to_square == move.to_square]

            is_sac = False
            theme = ""

            # Must have net material deficit and legal opponent capture on destination
            if opp_captures and val_moved > val_captured:
                if piece_type == chess.QUEEN and val_captured < 9:
                    is_sac = True
                    theme = "queen-sacrifice"
                elif piece_type == chess.ROOK:
                    if val_captured == 3:
                        is_sac = True
                        theme = "exchange-sacrifice"
                    elif val_captured <= 1:
                        is_sac = True
                        theme = "rook-sacrifice"
                elif piece_type in (chess.BISHOP, chess.KNIGHT) and val_captured <= 1:
                    is_sac = True
                    theme = "minor-piece-sacrifice"

            if is_sac:
                san = board.san(move)
                candidates.append({
                    "ply": ply,
                    "sideToMove": "white" if mover == chess.WHITE else "black",
                    "moveUci": move.uci(),
                    "moveSan": san,
                    "theme": theme,
                    "fenBefore": board.fen(),
                    "fullmoveNumber": board.fullmove_number,
                })

        prev_captured_type = captured_type
        prev_captured_square = move.to_square
        board.push(move)
        ply += 1

    return candidates


def score_to_cp_equivalent(pov_score: chess.engine.PovScore, mover_color: chess.Color) -> int:
    """Convert PovScore to monotonic integer centipawns for reliable comparisons."""
    score = pov_score.pov(mover_color)
    if score.is_mate():
        mate = score.mate()
        if mate is not None:
            return (30000 - mate * 100) if mate > 0 else (-30000 - mate * 100)
        return 0
    cp = score.score()
    return cp if cp is not None else 0


def score_to_pov_dict(
    score: chess.engine.PovScore,
    mover_color: chess.Color,
    info_dict: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Convert chess.engine.PovScore to explicit pov dict with cp/mate without fabricated WDL."""
    pov_score = score.pov(mover_color)
    result: Dict[str, Any] = {
        "pov": "white" if mover_color == chess.WHITE else "black",
    }
    if pov_score.is_mate():
        mate = pov_score.mate()
        result["type"] = "mate"
        result["score"] = mate
    else:
        result["type"] = "cp"
        result["score"] = pov_score.score() if pov_score.score() is not None else 0

    # Only include WDL if the engine actually reported it
    if info_dict and "wdl" in info_dict and info_dict["wdl"]:
        wdl = info_dict["wdl"]
        result["wdl"] = [getattr(wdl, "wins", 0), getattr(wdl, "draws", 0), getattr(wdl, "losses", 0)]

    return result


def evaluate_candidate(
    engine: chess.engine.SimpleEngine,
    board_fen: str,
    move_uci: str,
    nodes_screen: int = 200000,
    nodes_verify: int = 2000000,
) -> Optional[Dict[str, Any]]:
    """Two-stage evaluation: screen with nodes_screen, verify with nodes_verify."""
    board = chess.Board(board_fen)
    target_move = chess.Move.from_uci(move_uci)
    if target_move not in board.legal_moves:
        return None

    mover_color = board.turn

    # Stage 1: Screen (MultiPV=2)
    screen_info = engine.analyse(board, chess.engine.Limit(nodes=nodes_screen), multipv=2)
    if not screen_info:
        return None

    top_pv = screen_info[0].get("pv", [])
    top_move = top_pv[0] if top_pv else None
    top_score = screen_info[0].get("score")

    # Filter: before the move, position shouldn't be already completely trivial (+6.00) or hopeless (<-2.00)
    pov_before_cp = score_to_cp_equivalent(top_score, mover_color)
    if pov_before_cp > 600 or pov_before_cp < -200:
        return None

    # Check if target move is rank 1 or rank 2 within tolerance
    is_top = (top_move == target_move)
    is_second = False
    score_diff_cp = 0

    if not is_top and len(screen_info) > 1:
        second_pv = screen_info[1].get("pv", [])
        if second_pv and second_pv[0] == target_move:
            is_second = True
            s1 = score_to_cp_equivalent(top_score, mover_color)
            s2 = score_to_cp_equivalent(screen_info[1].get("score"), mover_color)
            score_diff_cp = s1 - s2

    # Filter: must be top move or <= 30 cp difference
    if not (is_top or (is_second and score_diff_cp <= 30)):
        return None

    # Stage 2: Deep Verification (MultiPV=2, nodes_verify)
    verify_info = engine.analyse(board, chess.engine.Limit(nodes=nodes_verify), multipv=2)
    if not verify_info:
        return None

    v_top_pv = verify_info[0].get("pv", [])
    v_top_move = v_top_pv[0] if v_top_pv else None
    v_top_score = verify_info[0].get("score")

    v_is_top = (v_top_move == target_move)
    v_is_second = False
    v_diff_cp = 0

    if not v_is_top and len(verify_info) > 1:
        v_second_pv = verify_info[1].get("pv", [])
        if v_second_pv and v_second_pv[0] == target_move:
            v_is_second = True
            s1 = score_to_cp_equivalent(v_top_score, mover_color)
            s2 = score_to_cp_equivalent(verify_info[1].get("score"), mover_color)
            v_diff_cp = s1 - s2

    if not (v_is_top or (v_is_second and v_diff_cp <= 30)):
        return None

    # POV score after playing the brilliancy move
    board_after = board.copy()
    board_after.push(target_move)

    # Opponent analysis (MultiPV=2 to get acceptance vs refusal lines)
    opp_info = engine.analyse(board_after, chess.engine.Limit(nodes=nodes_screen), multipv=2)
    analysis_lines = []
    if opp_info:
        # Best response
        opp_pv1 = opp_info[0].get("pv", [])
        if opp_pv1:
            line1_uci = [m.uci() for m in opp_pv1[:8]]
            temp_b = board_after.copy()
            line1_san = []
            for m in opp_pv1[:8]:
                if m in temp_b.legal_moves:
                    line1_san.append(temp_b.san(m))
                    temp_b.push(m)
                else:
                    break
            analysis_lines.append({
                "title": "最佳应对变化",
                "fenBefore": board_after.fen(),
                "uci": line1_uci,
                "san": line1_san,
            })

        # Alternative response (if available)
        if len(opp_info) > 1:
            opp_pv2 = opp_info[1].get("pv", [])
            if opp_pv2:
                line2_uci = [m.uci() for m in opp_pv2[:8]]
                temp_b = board_after.copy()
                line2_san = []
                for m in opp_pv2[:8]:
                    if m in temp_b.legal_moves:
                        line2_san.append(temp_b.san(m))
                        temp_b.push(m)
                    else:
                        break
                analysis_lines.append({
                    "title": "主要替代防守变化",
                    "fenBefore": board_after.fen(),
                    "uci": line2_uci,
                    "san": line2_san,
                })

    chosen_score = v_top_score if v_is_top else verify_info[1].get("score")
    chosen_info = verify_info[0] if v_is_top else verify_info[1]
    eval_dict = score_to_pov_dict(chosen_score, mover_color, chosen_info)

    # Brilliancy must leave player in a winning position (cp >= 80 or positive mate)
    if eval_dict["type"] == "mate":
        if eval_dict["score"] is None or eval_dict["score"] <= 0:
            return None  # Negative mate rejected
    elif eval_dict["type"] == "cp":
        if eval_dict["score"] < 80:
            return None
    else:
        return None

    if not all(key in verify_info[0] for key in ("nodes", "depth")):
        return None
    return {
        "engine": getattr(engine, "id", {}).get("name", "unknown"),
        "nodes": verify_info[0]["nodes"],
        "depth": verify_info[0]["depth"],
        "evaluation": eval_dict,
        "analysisLines": analysis_lines,
        "isTopMove": v_is_top,
        "lossCp": v_diff_cp if v_is_second else 0,
    }


def analyze_game(game, engine, nodes_screen=200000, nodes_verify=1500000):
    """Analyze one parsed legal game; callers own durable completion tracking."""
    results = []
    if game.errors:
        raise ValueError("INVALID_PGN")
    # Get raw text for fingerprint
    exporter = chess.pgn.StringExporter(headers=True, variations=False, comments=False)
    game_text = game.accept(exporter)
    fingerprint = pgn_helper.game_fingerprint(game_text)
    game_id = fingerprint.removeprefix("fp:")

    headers = game.headers
    white_name = headers.get("White", "未知棋手")
    black_name = headers.get("Black", "未知棋手")
    event_name = headers.get("Event", "公开赛事")
    event_date = headers.get("Date", "")
    result = headers.get("Result", "*")

    # Collect sacrifice candidates
    sacs = detect_sacrifice_moves(game)
    if not sacs:
        return []

    # Extract actual continuation moves
    all_moves = list(game.mainline_moves())
    replay = game.board()
    all_san = []
    for move in all_moves:
        all_san.append(replay.san(move))
        replay.push(move)

    for sac in sacs:
        ply = sac["ply"]
        ver = evaluate_candidate(
            engine,
            sac["fenBefore"],
            sac["moveUci"],
            nodes_screen=nodes_screen,
            nodes_verify=nodes_verify,
        )
        if not ver:
            continue

        # Actual continuation from this move onward (next 6 moves)
        actual_uci = [m.uci() for m in all_moves[ply + 1 : ply + 9]]
        actual_san = []
        temp_b = chess.Board(sac["fenBefore"])
        temp_b.push(chess.Move.from_uci(sac["moveUci"]))
        for uci_str in actual_uci:
            m = chess.Move.from_uci(uci_str)
            if m in temp_b.legal_moves:
                actual_san.append(temp_b.san(m))
                temp_b.push(m)
            else:
                break

        b_id = compute_brilliancy_id(fingerprint, ply, sac["moveUci"])

        # Determine mover name and player ID
        mover_side = sac["sideToMove"]
        mover_name = white_name if mover_side == "white" else black_name
        opp_name = black_name if mover_side == "white" else white_name

        rights = [
            {
                "type": "database-selection",
                "license": "CC BY 4.0",
                "attribution": "ChessDB 引擎筛选候选",
            }
        ]
        if headers.get("BroadcastURL") or "lichess.org" in (headers.get("Site") or ""):
            rights.append({
                "type": "game-broadcast",
                "license": "CC BY-SA 4.0",
                "attribution": "Lichess Broadcast (CC BY-SA 4.0)",
            })

        candidate = {
            "id": b_id,
            "schemaVersion": 1,
            "status": "candidate",
            "game": {
                "id": game_id,
                "fingerprint": fingerprint,
                "result": result,
                "initialFen": game.board().fen(),
                "movesUci": [move.uci() for move in all_moves],
                "movesSan": all_san,
                "totalMoves": len(all_moves),
            },
            "event": {
                "id": headers.get("TournamentID") or headers.get("EventID") or "event-unknown",
                "name": event_name,
                "date": event_date,
            },
            "white": {
                "displayName": white_name,
                "playerId": headers.get("WhiteFideId") or None,
            },
            "black": {
                "displayName": black_name,
                "playerId": headers.get("BlackFideId") or None,
            },
            "position": {
                "fenBefore": sac["fenBefore"],
                "ply": ply,
                "fullmoveNumber": sac["fullmoveNumber"],
                "sideToMove": mover_side,
            },
            "move": {
                "uci": sac["moveUci"],
                "san": sac["moveSan"],
            },
            "classification": {
                "symbol": "!!",
                "ruleVersion": "2026-09-v1",
            },
            "themes": [sac["theme"]],
            "verification": {
                "engine": ver["engine"],
                "nodes": ver["nodes"],
                "depth": ver["depth"],
                "evaluation": ver["evaluation"],
            },
            "rights": rights,
            "actualContinuationUci": actual_uci,
            "actualContinuationSan": actual_san,
            "analysisLines": ver["analysisLines"],
        }
        results.append(candidate)
    return results


def analyze_pgn_games(
    pgn_path: pathlib.Path,
    engine: chess.engine.SimpleEngine,
    limit_games: int = 1000,
    nodes_screen: int = 200000,
    nodes_verify: int = 1500000,
) -> List[Dict[str, Any]]:
    """Scan and verify brilliancies from a PGN file."""
    results = []
    scanned_games = 0

    with open(pgn_path, "r", encoding="utf-8", errors="ignore") as f:
        while scanned_games < limit_games:
            game = chess.pgn.read_game(f)
            if not game:
                break
            scanned_games += 1
            if game.errors:
                continue

            results.extend(analyze_game(game, engine, nodes_screen, nodes_verify))

    return results, scanned_games


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=pathlib.Path, help="Input PGN file or directory")
    parser.add_argument("--limit-games", type=int, default=1000, help="Maximum games to analyze")
    parser.add_argument("--engine", type=str, default="/opt/homebrew/bin/stockfish", help="Stockfish path")
    parser.add_argument("--nodes-screen", type=int, default=200000, help="Screen nodes budget")
    parser.add_argument("--nodes-verify", type=int, default=1500000, help="Verify nodes budget")
    parser.add_argument("--output", type=pathlib.Path, default=pathlib.Path.home() / "Library/Application Support/ChinaChessPlayerPGN/brilliancies/candidates.json")
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(ROOT):
        parser.error("Candidate output must be outside the repository; review before publishing")

    engine = chess.engine.SimpleEngine.popen_uci(args.engine)
    print(f"Stockfish initialized at {args.engine}")

    try:
        candidates = []
        if args.input and args.input.is_file():
            files = [args.input]
        elif args.input and args.input.is_dir():
            files = sorted(args.input.glob("*.pgn"))
        else:
            # Default: sample from top Chinese player PGNs
            files = sorted((ROOT / "docs/data/pgn/by-player").glob("fide-*/all.pgn"))

        total_scanned = 0
        for pgn_file in files:
            remaining = args.limit_games - total_scanned
            if remaining <= 0:
                break
            print(f"Scanning {pgn_file.name} (budget {remaining} games)...")
            found, scanned = analyze_pgn_games(
                pgn_file,
                engine,
                limit_games=remaining,
                nodes_screen=args.nodes_screen,
                nodes_verify=args.nodes_verify,
            )
            candidates.extend(found)
            total_scanned += scanned
            print(f"  Found {len(found)} candidate brilliancies.")

        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.output, {
            "schemaVersion": 1,
            "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
            "totalCandidates": len(candidates),
            "candidates": candidates,
        })
        print(f"Wrote {len(candidates)} candidates to {args.output}")

    finally:
        engine.quit()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
