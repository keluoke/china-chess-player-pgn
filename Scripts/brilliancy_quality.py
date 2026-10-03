#!/usr/bin/env python3
"""Position-first, reproducible pilot quality review of sacrifice candidates.

Grades are provisional until a stratified pilot calibrates this rule. This
module never publishes candidates or changes the archived-game queue.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

import chess
import chess.engine

RULE_VERSION = "sacrifice-qc-pilot-1"
MATERIAL = {chess.PAWN: 100, chess.KNIGHT: 320, chess.BISHOP: 330,
            chess.ROOK: 500, chess.QUEEN: 900}


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                  separators=(",", ":")).encode()).hexdigest()


def position_key(candidate: dict) -> str:
    # Move counters do not affect legal moves. Keep en-passant and castling rights.
    board = chess.Board(candidate["position"]["fenBefore"])
    return digest({"fen": board.fen().split(" ")[:4], "move": candidate["move"]["uci"]})


def replay(candidate: dict) -> tuple[chess.Board, chess.Move]:
    """Check the complete recorded mainline without using player/event metadata."""
    game = candidate["game"]
    moves = game["movesUci"]
    ply = candidate["position"]["ply"]
    if not isinstance(ply, int) or ply < 0 or ply >= len(moves):
        raise ValueError("QC_PLY_INVALID")
    expected = "br-" + hashlib.sha256(
        f'{game["fingerprint"]}:{ply}:{candidate["move"]["uci"]}'.encode()
    ).hexdigest()
    if candidate["id"] != expected:
        raise ValueError("QC_ID_MISMATCH")
    board = chess.Board(game["initialFen"])
    at_move = None
    target = None
    for index, uci in enumerate(moves):
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            raise ValueError("QC_GAME_MOVE_ILLEGAL")
        if index == ply:
            if board.fen() != candidate["position"]["fenBefore"]:
                raise ValueError("QC_FEN_MISMATCH")
            if uci != candidate["move"]["uci"] or board.san(move) != candidate["move"]["san"]:
                raise ValueError("QC_TARGET_MISMATCH")
            at_move, target = board.copy(), move
        board.push(move)
    if at_move is None or target is None:
        raise ValueError("QC_TARGET_MISSING")
    continuation = candidate.get("actualContinuationUci", [])
    if continuation != moves[ply + 1:ply + 1 + len(continuation)]:
        raise ValueError("QC_CONTINUATION_MISMATCH")
    return at_move, target


def material(board: chess.Board, color: chess.Color) -> int:
    total = 0
    for square, piece in board.piece_map().items():
        del square
        value = MATERIAL.get(piece.piece_type, 0)
        total += value if piece.color == color else -value
    return total


def score_cp(info: dict, color: chess.Color) -> int:
    score = info.get("score")
    if score is None:
        raise ValueError("QC_ENGINE_SCORE_MISSING")
    return score.pov(color).score(mate_score=100000)


def search(engine: chess.engine.SimpleEngine, board: chess.Board,
           nodes: int, color: chess.Color, root_moves=None) -> dict:
    info = engine.analyse(board, chess.engine.Limit(nodes=nodes), root_moves=root_moves)
    pv = info.get("pv", [])
    if not pv or not all(k in info for k in ("nodes", "depth", "score")):
        raise ValueError("QC_ENGINE_EVIDENCE_INCOMPLETE")
    return {"cp": score_cp(info, color), "nodes": info["nodes"],
            "depth": info["depth"], "pv": [m.uci() for m in pv[:12]]}


def evaluate_engine(engine: chess.engine.SimpleEngine, board: chess.Board,
                    target: chess.Move, nodes: int) -> dict:
    mover = board.turn
    alternatives = [m for m in board.legal_moves if m != target]
    if not alternatives:
        raise ValueError("QC_NO_ALTERNATIVE")
    if "Clear Hash" in engine.options:
        engine.configure({"Clear Hash": None})
    chosen = search(engine, board, nodes, mover, [target])
    alternative = search(engine, board, nodes, mover, alternatives)
    after = board.copy()
    after.push(target)
    captures = [m for m in after.legal_moves
                if m.to_square == target.to_square and after.is_capture(m)]
    if not captures:
        raise ValueError("QC_NO_LEGAL_ACCEPTANCE")
    refusals = [m for m in after.legal_moves if m not in captures]
    acceptance = search(engine, after, nodes, mover, captures)
    refusal = search(engine, after, nodes, mover, refusals) if refusals else None

    # A genuine offer must lose material when accepted. Track the best capture
    # line long enough to distinguish a lasting sacrifice from an immediate trade.
    line = after.copy()
    baseline = material(board, mover)
    deltas = []
    for uci in acceptance["pv"][:5]:
        move = chess.Move.from_uci(uci)
        if move not in line.legal_moves:
            raise ValueError("QC_ENGINE_PV_ILLEGAL")
        line.push(move)
        deltas.append(material(line, mover) - baseline)
    return {"engine": engine.id.get("name", "unknown"), "chosen": chosen,
            "alternative": alternative, "acceptance": acceptance,
            "refusal": refusal, "captureCount": len(captures),
            "materialDeltas": deltas}


def grade(evidence: list[dict]) -> tuple[str, list[str]]:
    """Conservative provisional grade based only on chess evidence."""
    if len(evidence) < 2:
        return "B", ["independent_engine_missing"]
    deltas = [e["materialDeltas"] for e in evidence]
    if any(not d or d[0] > -150 for d in deltas):
        return "D", ["not_a_material_sacrifice"]
    scores = [min(e["chosen"]["cp"], e["acceptance"]["cp"],
                  e["refusal"]["cp"] if e["refusal"] else 100000) for e in evidence]
    margins = [e["chosen"]["cp"] - e["alternative"]["cp"] for e in evidence]
    if any(len(d) < 3 for d in deltas):
        return "B", ["capture_line_too_short"]
    if all(-150 < d[1] < 150 for d in deltas) and max(margins) < 20:
        return "C", ["routine_immediate_trade"]
    if max(scores) < -100 or max(margins) < -100:
        return "D", ["unsound_under_independent_search"]
    if min(scores) < -60 or min(margins) < -50 or max(scores) - min(scores) > 200:
        return "B", ["engine_disagreement_or_uncertain_compensation"]
    if (min(scores) >= 80 and min(margins) >= 140
            and max(scores) - min(scores) <= 60
            and all(len(d) >= 4 and d[3] <= -150 for d in deltas)):
        return "S", ["stable_deep_sacrifice"]
    if (min(scores) >= -25 and min(margins) >= -35
            and max(scores) - min(scores) <= 150
            and all(d[1] <= -150 or d[1] >= 150 for d in deltas)):
        return "A", ["stable_sacrifice"]
    return "B", ["sound_but_not_proven_brilliant"]


def review(candidate: dict, engines: list[chess.engine.SimpleEngine], nodes: int) -> dict:
    board, target = replay(candidate)
    evidence = [evaluate_engine(engine, board, target, nodes) for engine in engines]
    quality, reasons = grade(evidence)
    return {"candidateId": candidate["id"], "positionKey": position_key(candidate),
            "qualityRuleVersion": RULE_VERSION, "grade": quality,
            "reasons": reasons, "engines": evidence}
