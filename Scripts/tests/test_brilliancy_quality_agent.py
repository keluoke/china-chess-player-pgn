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


if __name__ == "__main__":
    unittest.main()
