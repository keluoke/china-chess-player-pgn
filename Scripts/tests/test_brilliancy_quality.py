"""Quality-review invariants independent of player and tournament identifiers."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

import chess

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Scripts"))
import brilliancy_quality as qc
sys.path.insert(0, str(ROOT / "Scripts/local"))
import run_brilliancy_quality as runner
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class QualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        curated = json.loads((ROOT / "data/manual/brilliancies/curated.json").read_text())
        cls.candidate = curated["items"][0]

    def test_original_game_replays_without_fide_ids_or_internal_event_id(self):
        item = copy.deepcopy(self.candidate)
        item["white"]["playerId"] = None
        item["black"]["playerId"] = None
        item["event"]["id"] = "event-unknown"
        board, move = qc.replay(item)
        self.assertIn(move, board.legal_moves)
        self.assertEqual(board.fen(), item["position"]["fenBefore"])

    def test_tampered_original_game_is_rejected(self):
        item = copy.deepcopy(self.candidate)
        item["game"]["movesUci"][0] = "e2e5"
        with self.assertRaisesRegex(ValueError, "QC_GAME_MOVE_ILLEGAL"):
            qc.replay(item)

    def test_accepted_sacrifice_does_not_require_actual_opponent_capture(self):
        item = copy.deepcopy(self.candidate)
        item["actualContinuationUci"] = []
        qc.replay(item)

    def test_near_best_sacrifice_can_be_a_grade(self):
        def engine(score, alternative):
            return {"chosen": {"cp": score}, "alternative": {"cp": alternative},
                    "acceptance": {"cp": score}, "refusal": {"cp": score},
                    "materialDeltas": [-320, -320, -320, -320]}
        grade, _ = qc.grade([engine(110, 125), engine(90, 115)])
        self.assertEqual(grade, "A")

    def test_unsound_capture_does_not_get_auto_approval(self):
        def engine(score):
            return {"chosen": {"cp": score}, "alternative": {"cp": 30},
                    "acceptance": {"cp": score}, "refusal": {"cp": score},
                    "materialDeltas": [-500, -500, -500, -500]}
        grade, _ = qc.grade([engine(-260), engine(-300)])
        self.assertEqual(grade, "D")

    def test_private_checkpoint_authenticates_cached_grade(self):
        with tempfile.TemporaryDirectory() as folder:
            db_path = Path(folder) / "quality.sqlite3"
            db = runner.database(db_path)
            try:
                cipher = AESGCM(bytes(range(32)))
                result = {"candidateId": self.candidate["id"], "grade": "A"}
                runner.save_result(db, cipher, self.candidate, "profile", result)
                self.assertEqual(db_path.stat().st_mode & 0o077, 0)
                self.assertEqual(runner.cached_result(db, cipher, self.candidate, "profile"), result)
                db.execute("UPDATE results SET grade='S'")
                db.commit()
                with self.assertRaisesRegex(ValueError, "QC_ENCRYPTED_RESULT_MISMATCH"):
                    runner.cached_result(db, cipher, self.candidate, "profile")
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
