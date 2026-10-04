"""Full-history quality launcher stops on conflicts and only completes with R2 proof."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Scripts/local"))
import run_brilliancy_quality_all as agent


class AgentTests(unittest.TestCase):
    def test_hard_conflict_halts_without_retry_loop(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(agent, "PRIVATE", Path(folder)):
                with patch.object(agent.signal, "signal"):
                    with patch.object(agent, "run_batch", return_value=([1, 0], [None, {}],
                                                                          ["QC_BACKUP_REMOTE_CONFLICT", ""])) as run:
                        self.assertEqual(agent.main(), 0)
            self.assertEqual(run.call_count, 1)
            status = json.loads((Path(folder) / "quality-full.json").read_text())
            self.assertEqual(status["status"], "halted")

    def test_completion_requires_both_shards_and_r2_backup(self):
        summaries = [{"shard": 0, "sampleSize": 12, "graded": 12, "r2Backup": "synced"},
                     {"shard": 1, "sampleSize": 13, "graded": 13, "r2Backup": "synced"}]
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(agent, "PRIVATE", Path(folder)):
                with patch.object(agent.signal, "signal"):
                    with patch.object(agent, "run_batch", return_value=([0, 0], summaries, ["", ""])) as run:
                        self.assertEqual(agent.main(), 0)
            self.assertEqual(run.call_count, 1)
            status = json.loads((Path(folder) / "quality-full.json").read_text())
            self.assertEqual((status["status"], status["graded"]), ("complete", 25))

    def test_deep_full_uses_independent_progress_and_does_not_restart_first(self):
        summaries = [{"shard": 0, "sampleSize": 12, "graded": 5, "r2Backup": "synced"},
                     {"shard": 1, "sampleSize": 13, "graded": 6, "r2Backup": "synced"}]
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(agent, "PRIVATE", Path(folder)), patch.object(
                    agent.signal, "signal"), patch.object(
                    agent, "run_batch", return_value=([0, 0], summaries, ["", ""])) as run, patch.object(
                    agent.time, "sleep", side_effect=StopIteration):
                with self.assertRaises(StopIteration):
                    agent.main("deep")
            run.assert_called_once_with("deep")
            status = json.loads((Path(folder) / "quality-deep-full.json").read_text())
            self.assertEqual((status["status"], status["graded"]), ("running", 11))
            self.assertFalse((Path(folder) / "quality-full.json").exists())


if __name__ == "__main__":
    unittest.main()
