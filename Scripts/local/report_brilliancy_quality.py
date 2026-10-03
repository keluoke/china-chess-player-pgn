#!/usr/bin/env python3
"""Aggregate the private quality pilot without printing candidate identities."""
from __future__ import annotations

import argparse
import base64
from collections import Counter, defaultdict
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import chess

import run_brilliancy_quality as runner
import brilliancy_quality as qc


def report(sample_size: int, nodes: int, engines: list[Path]) -> dict:
    key_path = runner.PRIVATE / "queue-encryption.key"
    if (key_path.is_symlink() or not key_path.is_file()
            or key_path.stat().st_uid != os.getuid()
            or key_path.stat().st_mode & 0o077):
        raise ValueError("QC_KEY_MISSING_OR_EXPOSED")
    key_text = key_path.read_text().strip()
    cipher = AESGCM(base64.b64decode(key_text, validate=True))
    profile = qc.digest({"rule": qc.RULE_VERSION, "nodes": nodes,
                         "engines": [runner.sha256_file(p) for p in engines],
                         "reviewCode": runner.sha256_file(Path(qc.__file__)),
                         "chess": chess.__version__})
    population = Counter()
    observed = defaultdict(Counter)
    total = complete = 0
    source_versions = set()
    practical = Counter()
    all_chess = Counter()
    for shard in (0, 1):
        items, version = runner.load_candidates(shard, key_text)
        source_versions.add(version)
        selected = runner.stratified_sample(items, sample_size)
        for item in items:
            population[runner.stratum(item)] += 1
        db_path = runner.PRIVATE / f"quality-shard-{shard}.sqlite3"
        with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as db:
            for item in selected:
                total += 1
                result = runner.cached_result(db, cipher, item, profile)
                if result is None:
                    continue
                complete += 1
                stratum = runner.stratum(item)
                observed[stratum][result["grade"]] += 1
                evidence = result.get("engines", [])
                if len(evidence) == 2:
                    margins = [e["chosen"]["cp"] - e["alternative"]["cp"] for e in evidence]
                    least_margin = min(margins)
                    margin_band = ("below20" if least_margin < 20 else
                                   "20to69" if least_margin < 70 else
                                   "70to139" if least_margin < 140 else "atLeast140")
                    all_chess["margin_" + margin_band] += 1
                    deltas = evidence[0].get("materialDeltas", [])
                    if len(deltas) > 1:
                        kind = ("immediateGain" if deltas[1] >= 150 else
                                "lastingDeficit" if deltas[1] <= -150 else "evenTrade")
                        all_chess["material_" + kind] += 1
                if result["grade"] in {"S", "A"}:
                    actual = item.get("actualContinuationUci", [])
                    accepted = bool(actual) and actual[0][-2:] == item["move"]["uci"][-2:]
                    practical["accepted" if accepted else "declined"] += 1
                    if evidence:
                        margins = [e["chosen"]["cp"] - e["alternative"]["cp"] for e in evidence]
                        practical["marginBelow20" if min(margins) < 20 else "marginAtLeast20"] += 1
                        deltas = evidence[0].get("materialDeltas", [])
                        if len(deltas) > 1:
                            kind = ("immediateGain" if deltas[1] >= 150 else
                                    "lastingDeficit" if deltas[1] <= -150 else "evenTrade")
                            practical[kind] += 1
    raw_grades = Counter()
    for counts in observed.values():
        raw_grades.update(counts)
    output = {"schemaVersion": 1, "qualityRuleVersion": qc.RULE_VERSION,
              "profile": profile, "sourceVersions": sorted(source_versions),
              "populationCandidates": sum(population.values()),
              "sampleSelected": total, "sampleGraded": complete,
              "sampleGrades": dict(sorted(raw_grades.items())),
              "chessEvidenceFeatures": dict(sorted(all_chess.items())),
              "approvedChessFeatures": dict(sorted(practical.items())),
              "publicAutoPublish": False}
    if complete == total and total:
        weighted = Counter()
        approval_variance = 0.0
        population_total = sum(population.values())
        for stratum, size in population.items():
            counts = observed[stratum]
            n = sum(counts.values())
            if n == 0:
                raise ValueError("QC_STRATUM_UNOBSERVED")
            for grade, amount in counts.items():
                weighted[grade] += size * amount / n
            approved = counts["S"] + counts["A"]
            a, b = approved + 0.5, n - approved + 0.5
            variance = a * b / ((a + b) ** 2 * (a + b + 1))
            approval_variance += (size / population_total) ** 2 * variance * max(0, 1 - n / size)
        output["projectedCandidatesByGrade"] = {
            grade: round(weighted[grade]) for grade in ("S", "A", "B", "C", "D")}
        output["projectedProvisionalSACandidates"] = round(weighted["S"] + weighted["A"])
        estimate = output["projectedProvisionalSACandidates"]
        margin = round(1.96 * population_total * approval_variance ** 0.5)
        output["approximateProvisionalSACi95Candidates"] = [max(0, estimate - margin),
                                                             min(population_total, estimate + margin)]
        output["projectionStatus"] = "provisional_rule_not_calibrated"
    else:
        output["projectionStatus"] = "sample_in_progress"
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-size", type=int, default=300)
    parser.add_argument("--nodes", type=int, default=1_000_000)
    parser.add_argument("--engines", nargs=2, type=Path,
                        default=[runner.STOCKFISH_16, runner.STOCKFISH_17])
    args = parser.parse_args()
    print(json.dumps(report(args.sample_size, args.nodes, args.engines), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
