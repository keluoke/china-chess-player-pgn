#!/usr/bin/env python3
"""Fail-closed chess evidence check before a machine quality tier can be released.

The pilot grades and metadata are never publication authority. Callers must
authenticate the encrypted checkpoint and verify its candidate input hash first.
"""
from __future__ import annotations

import chess

import brilliancy_quality as quality
import brilliancy_quality_gate as gate

ENGINE_NAMES = frozenset(("Stockfish 16", "Stockfish 17.1"))
SEARCHES = ("chosen", "alternative", "acceptance", "refusal")


def _legal_pv(board: chess.Board, search: dict, minimum_nodes: int) -> chess.Move:
    if (not isinstance(search, dict) or not isinstance(search.get("cp"), int)
            or not isinstance(search.get("depth"), int) or search["depth"] < 14
            or not isinstance(search.get("nodes"), int)
            or search["nodes"] < minimum_nodes
            or not isinstance(search.get("pv"), list) or not search["pv"]):
        raise ValueError("QC_RELEASE_SEARCH_INCOMPLETE")
    line = board.copy()
    first = None
    for uci in search["pv"]:
        try:
            move = chess.Move.from_uci(uci)
        except (TypeError, ValueError) as error:
            raise ValueError("QC_RELEASE_PV_ILLEGAL") from error
        if move not in line.legal_moves:
            raise ValueError("QC_RELEASE_PV_ILLEGAL")
        if first is None:
            first = move
        line.push(move)
    return first


def certify(candidate: dict, first: dict | None, deep: dict | None) -> dict:
    """Check both budgets and legal branches, then return a chess-only decision.

    Missing independent evidence stays pending. A malformed or mismatched proof
    raises, so the publisher can isolate it rather than silently approve it.
    """
    board, target = quality.replay(candidate)
    position_key = quality.position_key(candidate)
    if first is None or deep is None:
        return {"ruleVersion": gate.RULE_VERSION, "tier": "pending-independent-evidence"}
    after = board.copy()
    after.push(target)
    for record, nodes in ((first, 1_000_000), (deep, 5_000_000)):
        if (record.get("candidateId") != candidate["id"]
                or record.get("positionKey") != position_key
                or record.get("qualityRuleVersion") != quality.RULE_VERSION):
            raise ValueError("QC_RELEASE_RECORD_MISMATCH")
        engines = record.get("engines")
        if (not isinstance(engines, list) or len(engines) != 2
                or {e.get("engine") for e in engines if isinstance(e, dict)} != ENGINE_NAMES):
            raise ValueError("QC_RELEASE_ENGINES_INVALID")
        for engine in engines:
            roots = {}
            for name in SEARCHES:
                search = engine.get(name)
                if name == "refusal" and search is None:
                    if all(after.is_capture(move) and move.to_square == target.to_square
                           for move in after.legal_moves):
                        continue
                    raise ValueError("QC_RELEASE_REFUSAL_MISSING")
                roots[name] = _legal_pv(board if name in ("chosen", "alternative")
                                        else after, search, nodes)
            if roots["chosen"] != target or roots["alternative"] == target:
                raise ValueError("QC_RELEASE_ROOT_MISMATCH")
            accepted = roots["acceptance"]
            if not after.is_capture(accepted) or accepted.to_square != target.to_square:
                raise ValueError("QC_RELEASE_ACCEPTANCE_MISMATCH")
            refused = roots.get("refusal")
            if refused is not None and after.is_capture(refused) and refused.to_square == target.to_square:
                raise ValueError("QC_RELEASE_REFUSAL_MISMATCH")
    return gate.assess(first, deep)
