"""Quality-review invariants independent of player and tournament identifiers."""
import base64
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import chess

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Scripts"))
import brilliancy_quality as qc
sys.path.insert(0, str(ROOT / "Scripts/local"))
import run_brilliancy_quality as runner
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from brilliancy_queue import R2Store, new_state
from Scripts.tests.test_brilliancy_queue import FakeS3
from Scripts.tests.test_brilliancy_quality_gate import record as engine_record


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

    def test_only_legal_move_is_recorded_without_engine_search(self):
        board = MagicMock()
        board.legal_moves.count.return_value = 1
        with patch.object(qc, "replay", return_value=(board, None)):
            with patch.object(qc, "position_key", return_value="position"):
                result = qc.review(self.candidate, [], 100_000)
        self.assertEqual(result["grade"], "C")
        self.assertEqual(result["reasons"], ["only_legal_move"])

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

    def test_private_checkpoint_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "target"
            target.write_text("keep")
            link = Path(folder) / "quality.sqlite3"
            link.symlink_to(target)
            with self.assertRaisesRegex(ValueError, "QC_PRIVATE_SYMLINK_FORBIDDEN"):
                runner.database(link)
            self.assertEqual(target.read_text(), "keep")

    def test_quality_sample_does_not_use_identity_or_internal_event_id(self):
        items = []
        for index in range(40):
            item = copy.deepcopy(self.candidate)
            item["id"] = "br-" + f"{index:064x}"
            item["themes"] = ["queen-sacrifice" if index % 2 else "rook-sacrifice"]
            items.append(item)
        selected = {x["id"] for x in runner.stratified_sample(items, 20)}
        for item in items:
            item["white"]["playerId"] = None
            item["black"]["playerId"] = None
            item["event"]["id"] = "event-unknown"
        self.assertEqual(selected, {x["id"] for x in runner.stratified_sample(items, 20)})

    def test_deep_checkpoint_is_separate_from_running_full_scan(self):
        self.assertEqual(runner.checkpoint_name(0, "first", "sqlite3"),
                         "quality-shard-0.sqlite3")
        self.assertEqual(runner.checkpoint_name(0, "deep", "sqlite3"),
                         "quality-deep-shard-0.sqlite3")
        with self.assertRaisesRegex(ValueError, "QC_LANE_INVALID"):
            runner.checkpoint_name(0, "unknown", "sqlite3")

    def test_full_deep_scan_prioritizes_chess_quality_without_dropping_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            db = runner.database(Path(folder) / "first.sqlite3")
            try:
                cipher = AESGCM(bytes(range(32)))
                low = copy.deepcopy(self.candidate)
                low["id"] = "br-" + "a" * 64
                high = copy.deepcopy(self.candidate)
                high["id"] = "br-" + "b" * 64
                for item, margin in ((low, 0), (high, 120)):
                    outcome = engine_record(margin=margin)
                    outcome["candidateId"] = item["id"]
                    runner.save_result(db, cipher, item, "first-profile", outcome)
                ordered = runner.prioritized_deep_items([low, high], db, cipher,
                                                        "first-profile")
                self.assertEqual([row["id"] for row in ordered], [high["id"], low["id"]])
                with self.assertRaisesRegex(ValueError, "QC_FIRST_INCOMPLETE"):
                    runner.prioritized_deep_items([self.candidate], db, cipher,
                                                  "first-profile")
            finally:
                db.close()

    def test_authenticated_history_source_is_cached_privately(self):
        key = base64.b64encode(bytes(range(32))).decode()
        client = FakeS3()
        state = new_state(0)
        state["baselineGames"] = ["a" * 64]
        state["activeVersion"] = "v1"
        state["records"]["a" * 64] = {
            "status": "complete", "version": "v1", "candidates": [self.candidate],
        }
        store = R2Store(client, "chess-data", 0, key)
        store.save(state)
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(runner, "PRIVATE", Path(folder)), patch.object(
                    runner, "r2_client", return_value=(client, "chess-data")):
                items, version = runner.load_candidates(0, key)
                self.assertEqual((len(items), version), (1, "v1"))
                cached = Path(folder) / "quality-source-history-shard-0.bin"
                self.assertEqual(cached.stat().st_mode & 0o077, 0)
                client.objects.clear()
                self.assertEqual(runner.load_candidates(0, key)[0], items)
                cached.unlink()
                cached.symlink_to(Path(folder) / "missing")
                with self.assertRaisesRegex(ValueError, "QC_PRIVATE_SYMLINK_FORBIDDEN"):
                    runner.load_candidates(0, key)


if __name__ == "__main__":
    unittest.main()
