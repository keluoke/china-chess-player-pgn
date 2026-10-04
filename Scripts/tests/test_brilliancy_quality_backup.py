"""Quality backup cannot overwrite changed encrypted R2 evidence."""
import base64
from pathlib import Path
import sys
import tempfile
import unittest

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Scripts"))
sys.path.insert(0, str(ROOT / "Scripts/local"))
import brilliancy_quality_backup as backup
import run_brilliancy_quality as runner
from Scripts.tests.test_brilliancy_queue import FakeS3


class BackupTests(unittest.TestCase):
    def test_deep_backup_key_is_isolated_from_first_pass(self):
        encoded = base64.b64encode(bytes(range(32))).decode()
        client = FakeS3()
        first = backup.QualityBackup(client, "chess-data", 0, encoded)
        deep = backup.QualityBackup(client, "chess-data", 0, encoded, "deep")
        self.assertNotEqual(first.store.key, deep.store.key)
        self.assertTrue(deep.store.key.endswith("quality-deep-shard-0.bin"))
        with self.assertRaisesRegex(ValueError, "QC_BACKUP_LANE_INVALID"):
            backup.QualityBackup(client, "chess-data", 0, encoded, "unknown")

    def test_roundtrip_restore_and_refuse_conflicting_local_row(self):
        key = bytes(range(32))
        encoded = base64.b64encode(key).decode()
        client = FakeS3()
        with tempfile.TemporaryDirectory() as folder:
            first = runner.database(Path(folder) / "first.sqlite3")
            second = runner.database(Path(folder) / "second.sqlite3")
            try:
                item = {"id": "br-" + "a" * 64, "position": {"fenBefore": "start"}}
                outcome = {"candidateId": item["id"], "grade": "A", "engines": []}
                runner.save_result(first, AESGCM(key), item, "profile", outcome)
                writer = backup.QualityBackup(client, "chess-data", 0, encoded)
                self.assertEqual(writer.sync(first), 1)
                self.assertEqual(writer.sync(first), 0)
                self.assertEqual(backup.QualityBackup(client, "chess-data", 0, encoded).reconcile(second), 1)
                self.assertEqual(runner.cached_result(second, AESGCM(key), item, "profile"), outcome)
                runner.save_result(second, AESGCM(key), item, "profile",
                                   {"candidateId": item["id"], "grade": "S", "engines": []})
                with self.assertRaisesRegex(ValueError, "QC_BACKUP_REMOTE_CONFLICT"):
                    backup.QualityBackup(client, "chess-data", 0, encoded).sync(second)
            finally:
                first.close()
                second.close()


if __name__ == "__main__":
    unittest.main()
