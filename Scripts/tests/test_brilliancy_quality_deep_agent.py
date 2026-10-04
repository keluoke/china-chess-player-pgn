"""Deep calibration jobs keep independent private checkpoints and fixed budgets."""
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Scripts/local"))
import install_brilliancy_quality_deep_agent as installer


class DeepAgentTests(unittest.TestCase):
    def test_two_isolated_fixed_sample_jobs(self):
        first = installer.job(0)
        second = installer.job(1)
        self.assertNotEqual(first["Label"], second["Label"])
        for shard, config in enumerate((first, second)):
            arguments = config["ProgramArguments"]
            self.assertEqual(arguments[arguments.index("--lane") + 1], "deep")
            self.assertEqual(arguments[arguments.index("--shard") + 1], str(shard))
            self.assertEqual(arguments[arguments.index("--sample-size") + 1], "300")
            self.assertEqual(arguments[arguments.index("--nodes") + 1], "5000000")
            self.assertEqual(arguments[arguments.index("--backup-every") + 1], "50")
            self.assertNotIn("KeepAlive", config)


if __name__ == "__main__":
    unittest.main()
