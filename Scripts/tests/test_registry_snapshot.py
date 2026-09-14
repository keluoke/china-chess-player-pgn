import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import validate_registry_snapshot as gate
import validate_registry_authority as authority
import reconcile_pgn_sources as reconcile


class RegistrySnapshotTests(unittest.TestCase):
    def test_audit_profiles_cannot_restore_stale_ratings_or_departed_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.json"
            registry.write_text(json.dumps([{"fideID": "1", "standard": 2553}]))
            youth = root / "youth.json"
            youth.write_text(json.dumps({"players": [
                {"fideID": "1", "standard": 2560, "rapid": 2400},
                {"fideID": "2", "standard": 2000}]}))
            static = root / "static"
            static.mkdir()
            (static / "fide-1.json").write_text(json.dumps({"fideID": "1", "standard": 2500}))
            with mock.patch.multiple(reconcile, REGISTRY_PLAYERS_JSON=registry,
                                     YOUTH_JSON=youth, STATIC_PLAYER_ROOT=static):
                profiles = reconcile.load_profiles()
            self.assertEqual(set(profiles), {"1"})
            self.assertEqual(profiles["1"].standard, 2553)
            self.assertFalse(profiles["1"].rapid)

    def test_new_month_cannot_ship_with_old_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            outputs = []
            for relative in gate.REGISTRY_PATHS:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'{"standard":2560}')
                outputs.append({"path": relative, "present": True,
                                "bytes": path.stat().st_size,
                                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            snapshot = root / "docs/data/snapshot.json"
            snapshot.write_text(json.dumps({"outputs": outputs}))
            self.assertEqual(gate.validate(root), [])
            (root / gate.REGISTRY_PATHS[0]).write_bytes(b'{"standard":2553}')
            self.assertIn("changed since", gate.validate(root)[0])
            snapshot.write_text(json.dumps({"outputs": []}))
            self.assertEqual(len(gate.validate(root)), 2)

    def test_retired_index_is_excluded_but_live_ratings_remain_authoritative(self):
        root = Path(__file__).resolve().parents[2]
        action = (root / ".github/actions/prepare-static-site/action.yml").read_text()
        self.assertIn("--exclude 'data/index/players.json'", action)
        self.assertNotIn(authority.ROOT / "docs/data/index/players.json", authority.TARGETS)
        self.assertTrue(authority.validate_document(
            [{"fideID": "8603006", "standard": 2560}],
            {"8603006": {"standard": 2553}}, "live"))


if __name__ == "__main__":
    unittest.main()
