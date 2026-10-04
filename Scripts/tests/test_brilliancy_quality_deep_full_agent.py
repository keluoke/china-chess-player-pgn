"""Deep historical jobs cannot start until the certified first pass finishes."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Scripts/local"))
import install_brilliancy_quality_deep_full_agent as installer


class DeepFullInstallerTests(unittest.TestCase):
    def test_complete_private_first_pass_is_required(self):
        summary = {"status": "complete", "candidatePositions": 4, "graded": 4,
                   "byShard": [{"shard": 0, "graded": 2, "total": 2},
                               {"shard": 1, "graded": 2, "total": 2}],
                   "publicAutoPublish": False}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "quality-full.json"
            path.write_text(json.dumps(summary))
            path.chmod(0o600)
            installer.certified_first_pass(path)
            summary["graded"] = 3
            path.write_text(json.dumps(summary))
            with self.assertRaisesRegex(ValueError, "QC_FIRST_NOT_COMPLETE"):
                installer.certified_first_pass(path)
            path.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "QC_FIRST_SUMMARY_MISSING_OR_EXPOSED"):
                installer.certified_first_pass(path)


if __name__ == "__main__":
    unittest.main()
