"""A machine tier requires legal, budgeted evidence for the exact played position."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Scripts"))
import brilliancy_quality as quality
import brilliancy_publication_gate as release


def proof(candidate, nodes):
    board, target = quality.replay(candidate)
    alternative = next(move for move in board.legal_moves if move != target)
    after = board.copy()
    after.push(target)
    acceptance = next(move for move in after.legal_moves
                      if after.is_capture(move) and move.to_square == target.to_square)
    refusal = next(move for move in after.legal_moves if move != acceptance)

    def search(cp, move):
        return {"cp": cp, "depth": 20, "nodes": nodes,
                "pv": [move.uci()]}

    engines = [{"engine": name, "chosen": search(180, target),
                "alternative": search(10, alternative),
                "acceptance": search(180, acceptance),
                "refusal": search(180, refusal),
                "materialDeltas": [-320, 500, 500, 500]}
               for name in ("Stockfish 16", "Stockfish 17.1")]
    return {"candidateId": candidate["id"], "positionKey": quality.position_key(candidate),
            "qualityRuleVersion": quality.RULE_VERSION, "engines": engines}


class PublicationGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.candidate = json.loads((ROOT / "data/manual/brilliancies/curated.json").read_text())["items"][0]

    def test_valid_tactical_proof_ignores_player_and_event_ids(self):
        item = copy.deepcopy(self.candidate)
        item["white"]["playerId"] = None
        item["black"]["playerId"] = None
        item["event"]["id"] = "event-unknown"
        self.assertEqual(release.certify(item, proof(item, 1_000_000),
                                         proof(item, 5_000_000))["tier"], "A")

    def test_missing_second_budget_remains_pending(self):
        self.assertEqual(release.certify(self.candidate, proof(self.candidate, 1_000_000),
                                         None)["tier"], "pending-independent-evidence")

    def test_insufficient_nodes_cannot_certify(self):
        deep = proof(self.candidate, 5_000_000)
        deep["engines"][1]["chosen"]["nodes"] -= 1
        with self.assertRaisesRegex(ValueError, "QC_RELEASE_SEARCH_INCOMPLETE"):
            release.certify(self.candidate, proof(self.candidate, 1_000_000), deep)

    def test_wrong_root_and_illegal_pv_are_rejected(self):
        first = proof(self.candidate, 1_000_000)
        deep = proof(self.candidate, 5_000_000)
        deep["engines"][0]["chosen"]["pv"] = deep["engines"][0]["alternative"]["pv"]
        with self.assertRaisesRegex(ValueError, "QC_RELEASE_ROOT_MISMATCH"):
            release.certify(self.candidate, first, deep)
        deep["engines"][0]["chosen"]["pv"] = ["a1a1"]
        with self.assertRaisesRegex(ValueError, "QC_RELEASE_PV_ILLEGAL"):
            release.certify(self.candidate, first, deep)

    def test_wrong_candidate_evidence_is_rejected(self):
        deep = proof(self.candidate, 5_000_000)
        deep["positionKey"] = "another-position"
        with self.assertRaisesRegex(ValueError, "QC_RELEASE_RECORD_MISMATCH"):
            release.certify(self.candidate, proof(self.candidate, 1_000_000), deep)


if __name__ == "__main__":
    unittest.main()
