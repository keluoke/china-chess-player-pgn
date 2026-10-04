"""The cloud lane must be isolated, resumable, and reject ambiguous candidates."""
import base64
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Scripts"))
import brilliancy_incremental_quality as quality
from brilliancy_queue import R2Store, new_state
from Scripts.tests.test_brilliancy_quality_gate import record
from Scripts.tests.test_brilliancy_queue import FakeS3


class IncrementalQualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.item = json.loads((ROOT / "data/manual/brilliancies/curated.json").read_text())["items"][0]

    def state(self):
        state = new_state(0)
        state["activeVersion"] = "current"
        return state

    def test_only_complete_current_rows_are_reviewed_without_identity_requirement(self):
        state = self.state()
        item = copy.deepcopy(self.item)
        item["white"]["playerId"] = None
        item["black"]["playerId"] = None
        item["event"]["id"] = "event-unknown"
        state["records"] = {
            "one": {"status": "complete", "version": "current", "candidates": [item]},
            "two": {"status": "complete", "version": "current", "candidates": [copy.deepcopy(item)]},
            "old": {"status": "complete", "version": "old", "candidates": [self.item]},
            "pending": {"status": "pending", "version": "current", "candidates": [self.item]},
        }
        self.assertEqual(quality.candidates_from_state(state), [item])

    def test_conflicting_same_id_is_quarantined(self):
        state = self.state()
        other = copy.deepcopy(self.item)
        other["game"]["result"] = "*"
        state["records"] = {
            "one": {"status": "complete", "version": "current", "candidates": [self.item]},
            "two": {"status": "complete", "version": "current", "candidates": [other]},
        }
        with self.assertRaisesRegex(ValueError, "QC_INCREMENTAL_DUPLICATE_CONFLICT"):
            quality.candidates_from_state(state)

    def test_no_source_version_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "QC_INCREMENTAL_SOURCE_VERSION_INVALID"):
            quality.candidates_from_state(new_state(0))

    def test_chess_priority_does_not_permanently_exclude_other_candidates(self):
        self.assertLess(quality.gate.deep_priority(record(margin=120)),
                        quality.gate.deep_priority(record(margin=0)))
        self.assertEqual(quality.gate.deep_priority(None)[0], 2)

    def test_invalid_replay_is_recorded_without_hiding_engine_errors(self):
        with patch.object(quality.qc, "review", side_effect=ValueError("QC_GAME_MOVE_ILLEGAL")):
            result = quality.review_or_reject(self.item, [], 1_000_000)
        self.assertEqual((result["grade"], result["reasons"]),
                         ("D", ["QC_GAME_MOVE_ILLEGAL"]))
        with patch.object(quality.qc, "review", side_effect=ValueError("QC_ENGINE_SCORE_MISSING")):
            with self.assertRaisesRegex(ValueError, "QC_ENGINE_SCORE_MISSING"):
                quality.review_or_reject(self.item, [], 1_000_000)

    def test_encrypted_cloud_checkpoint_resumes_without_reanalysis(self):
        key = base64.b64encode(bytes(range(32))).decode()
        client = FakeS3()
        state = self.state()
        state["records"] = {
            "a" * 64: {"status": "complete", "version": "current",
                       "candidates": [self.item]},
        }
        R2Store(client, "chess-data", 0, key, "incremental").save(state)
        with tempfile.TemporaryDirectory() as folder:
            paths = [Path(folder) / name for name in ("sf16", "sf17")]
            for path, body in zip(paths, (b"engine-16", b"engine-17")):
                path.write_bytes(body)
                path.chmod(0o700)
            args = SimpleNamespace(shard=0, engines=paths, seconds=600,
                                   max_new=1, backup_every=1)
            outcome = record()
            outcome["candidateId"] = self.item["id"]
            with patch.dict(os.environ, {"BRILLIANCY_QUEUE_KEY": key}), patch.object(
                    quality, "r2_client", return_value=(client, "chess-data")), patch.object(
                    quality.chess.engine.SimpleEngine, "popen_uci",
                    side_effect=lambda _: MagicMock()), patch.object(
                    quality.qc, "review", return_value=outcome) as review:
                first = quality.run(args)
                self.assertEqual(first["processedThisRun"], {"first": 1})
                self.assertEqual(first["qualityTiers"], {"pending-independent-evidence": 1})
                self.assertNotIn(self.item["id"], json.dumps(first))
                self.assertEqual(review.call_count, 1)
                second = quality.run(args)
                self.assertEqual(second["processedThisRun"], {"deep": 1})
                self.assertEqual(second["qualityTiers"], {"A": 1})
                self.assertEqual(second["certifiedEvidenceTiers"],
                                 {"isolated-invalid-evidence": 1})
                self.assertEqual(review.call_count, 2)
                third = quality.run(args)
                self.assertEqual(third["processedThisRun"], {})
                self.assertEqual(review.call_count, 2)


if __name__ == "__main__":
    unittest.main()
