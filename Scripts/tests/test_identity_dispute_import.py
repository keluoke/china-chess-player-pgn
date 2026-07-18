import csv
import importlib.util
import pathlib
import tempfile
import unittest


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "local" / "import_identity_dispute.py"
SPEC = importlib.util.spec_from_file_location("import_identity_dispute", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(module)


class IdentityDisputeImportTest(unittest.TestCase):
    def test_imported_pair_is_readable_by_projector_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "presentation-disputes.csv"
            result = module.import_dispute({
                "type": "identity-dispute",
                "groupID": "pg-example",
                "memberIDs": ["domestic-aaa", "domestic-bbb"],
                "created_at": "2026-07-19T00:00:00Z",
                "notes": "不是同一人",
            }, target)
            self.assertEqual(result["pairsAdded"], 1)
            with target.open(encoding="utf-8") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(row["status"], "disputed")
            self.assertEqual(row["pair_hash"], module.pair_hash("domestic-aaa", "domestic-bbb"))

    def test_large_group_blocks_every_internal_pair_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "presentation-disputes.csv"
            payload = {
                "type": "identity-dispute",
                "groupID": "pg-example",
                "memberIDs": ["domestic-aaa", "domestic-bbb", "domestic-ccc"],
                "scope": "whole-group",
            }
            self.assertEqual(module.import_dispute(payload, target)["pairsAdded"], 3)
            self.assertEqual(module.import_dispute(payload, target)["pairsAdded"], 0)
            with target.open(encoding="utf-8") as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 3)


if __name__ == "__main__":
    unittest.main()
