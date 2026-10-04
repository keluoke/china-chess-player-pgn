#!/usr/bin/env python3
"""Conservative, position-only second-stage brilliancy quality decision.

The pilot review grades in :mod:`brilliancy_quality` are intentionally ignored.
Both authenticated engine records must describe the same candidate and position.
This module classifies chess evidence; it cannot publish a record by itself.
"""
from __future__ import annotations

from typing import Any

RULE_VERSION = "sacrifice-quality-gate-1"


def _engine_features(record: dict[str, Any]) -> dict[str, Any]:
    engines = record.get("engines") or []
    if (len(engines) != 2 or not all(isinstance(engine, dict) for engine in engines)
            or any(not isinstance(engine.get("engine"), str) or not engine["engine"]
                   for engine in engines)
            or len({engine["engine"] for engine in engines}) != 2):
        raise ValueError("QC_GATE_INDEPENDENT_ENGINES_REQUIRED")
    try:
        for engine in engines:
            for search in ("chosen", "alternative", "acceptance", "refusal"):
                if engine.get(search) is None:
                    if search == "refusal":
                        continue
                    raise ValueError("QC_GATE_SEARCH_MISSING")
                if not isinstance(engine[search]["cp"], int):
                    raise ValueError("QC_GATE_SCORE_INVALID")
                if not isinstance(engine[search]["nodes"], int) or engine[search]["nodes"] <= 0:
                    raise ValueError("QC_GATE_NODES_INVALID")
            if (not engine.get("materialDeltas")
                    or any(not isinstance(delta, int) for delta in engine["materialDeltas"])):
                raise ValueError("QC_GATE_MATERIAL_MISSING")
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError("QC_GATE_EVIDENCE_INVALID") from error
    return {
        "marginCp": min(engine["chosen"]["cp"] - engine["alternative"]["cp"]
                        for engine in engines),
        "soundCp": min(min(engine["chosen"]["cp"], engine["acceptance"]["cp"],
                           engine["refusal"]["cp"] if engine.get("refusal") else 100_000)
                       for engine in engines),
        "offer": all(engine["materialDeltas"][0] <= -150 for engine in engines),
        "tacticalGain": all(len(engine["materialDeltas"]) >= 2
                            and engine["materialDeltas"][1] >= 150 for engine in engines),
        "persistentSacrifice": all(len(engine["materialDeltas"]) >= 4
                                   and engine["materialDeltas"][3] <= -150 for engine in engines),
    }


def assess(first: dict[str, Any] | None, deep: dict[str, Any] | None) -> dict[str, Any]:
    """Return a chess-only tier; private callers authenticate input records first.

    ``S`` and ``A`` are technical candidates for a later publication transaction.
    The deployment gate is separate, and must remain closed until calibrated.
    """
    if not first or not deep:
        return {"ruleVersion": RULE_VERSION, "tier": "pending-independent-evidence"}
    if first.get("candidateId") != deep.get("candidateId") or not first.get("candidateId"):
        raise ValueError("QC_GATE_ID_MISMATCH")
    if not first.get("positionKey") or not deep.get("positionKey"):
        return {"ruleVersion": RULE_VERSION, "tier": "not-proven",
                "reason": "position_proof_missing"}
    if first["positionKey"] != deep["positionKey"]:
        raise ValueError("QC_GATE_POSITION_MISMATCH")
    try:
        one = _engine_features(first)
        five = _engine_features(deep)
    except ValueError:
        return {"ruleVersion": RULE_VERSION, "tier": "not-proven",
                "reason": "incomplete_independent_evidence"}
    if not one["offer"] or not five["offer"]:
        return {"ruleVersion": RULE_VERSION, "tier": "not-proven",
                "reason": "no_consistent_material_offer"}
    if one["soundCp"] < -50 or five["soundCp"] < -25:
        return {"ruleVersion": RULE_VERSION, "tier": "not-proven",
                "reason": "compensation_not_stable"}
    if (one["persistentSacrifice"] and five["persistentSacrifice"]
            and one["marginCp"] >= 100 and five["marginCp"] >= 140
            and one["soundCp"] >= 0 and five["soundCp"] >= 80):
        tier, reason = "S", "decisive_persistent_sacrifice"
    elif (one["persistentSacrifice"] and five["persistentSacrifice"]
          and one["marginCp"] >= 50 and five["marginCp"] >= 70):
        tier, reason = "A", "stable_persistent_sacrifice"
    elif (one["tacticalGain"] and five["tacticalGain"]
          and one["marginCp"] >= 100 and five["marginCp"] >= 140):
        tier, reason = "A", "forced_tactical_payoff"
    else:
        tier, reason = "not-proven", "insufficient_distinctiveness_or_stability"
    return {"ruleVersion": RULE_VERSION, "tier": tier, "reason": reason,
            "firstMarginCp": one["marginCp"], "deepMarginCp": five["marginCp"],
            "deepSoundCp": five["soundCp"]}
