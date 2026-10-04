"""The second-stage gate must distinguish a real tactical payoff from pilot grades."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Scripts"))
import brilliancy_quality_gate as gate


def record(margin=160, material=(-320, 500, 500, 500), score=180):
    def search(cp):
        return {"cp": cp, "nodes": 5_000_000}
    return {
        "candidateId": "br-" + "a" * 64,
        "positionKey": "position-hash",
        "grade": "B",  # The provisional pilot grade has no release authority.
        "engines": [{
            "engine": name,
            "chosen": search(score),
            "alternative": search(score - margin),
            "acceptance": search(score),
            "refusal": search(score),
            "materialDeltas": list(material),
        } for name in ("Stockfish 16", "Stockfish 17.1")],
    }


class QualityGateTests(unittest.TestCase):
    def test_forced_tactical_gain_is_not_lost_by_persistent_sacrifice_filter(self):
        result = gate.assess(record(margin=110), record(margin=160))
        self.assertEqual((result["tier"], result["reason"]),
                         ("A", "forced_tactical_payoff"))

    def test_one_engine_refutation_blocks_tactical_release(self):
        deep = record()
        deep["engines"][1]["refusal"]["cp"] = -170
        self.assertEqual(gate.assess(record(), deep)["reason"],
                         "compensation_not_stable")

    def test_horizon_flip_blocks_deep_release(self):
        first = record(material=(-320, 0, 0, 0))
        deep = record(material=(-320, -320, -320, -320))
        self.assertEqual(gate.assess(first, deep)["tier"], "not-proven")

    def test_stable_persistent_sacrifice_is_highest_tier(self):
        first = record(margin=120, material=(-320, -320, -320, -320))
        deep = record(margin=180, material=(-320, -320, -320, -320))
        self.assertEqual(gate.assess(first, deep)["tier"], "S")

    def test_missing_and_mismatched_position_evidence_fails_closed(self):
        first, deep = record(), record()
        self.assertEqual(gate.assess(first, None)["tier"], "pending-independent-evidence")
        deep.pop("positionKey")
        self.assertEqual(gate.assess(first, deep)["tier"], "not-proven")
        deep["positionKey"] = "another-position"
        with self.assertRaisesRegex(ValueError, "QC_GATE_POSITION_MISMATCH"):
            gate.assess(first, deep)

    def test_independent_engines_required(self):
        first, deep = record(), record()
        deep["engines"][1]["engine"] = deep["engines"][0]["engine"]
        self.assertEqual(gate.assess(first, deep)["reason"],
                         "incomplete_independent_evidence")


if __name__ == "__main__":
    unittest.main()
