import hashlib
import json
import pathlib
import tempfile
import unittest
from unittest import mock

from Scripts.local import resolve_release_conflict as successor


class ReleaseConflictSuccessorTests(unittest.TestCase):
    def source(self, root: pathlib.Path, run_id: str, rows: list[tuple[str, dict]]) -> tuple:
        entry = root / run_id
        for path, payload in rows:
            target = entry / "files" / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(payload), encoding="utf-8")
        files = []
        for path, _payload in rows:
            content = (entry / "files" / path).read_bytes()
            files.append({
                "path": path,
                "operation": "upsert",
                "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content),
                "baseBlobOid": None,
                "baseSha256": None,
            })
        manifest = {
            "schemaVersion": 1,
            "runId": run_id,
            "baseCommit": "a" * 40,
            "source": {"source": "Chess-Results", "releasePolicy": "full-data"},
            "files": files,
        }
        return run_id, entry, manifest

    def test_later_source_wins_and_explicit_partial_is_dropped(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            partial = "data/generated/chess-results-event-details/tnr1.json"
            shared = "data/generated/r2-object-receipts/events--chess-results.json"
            first = self.source(root, "20260819-100000-11111111", [
                (partial, {"captureStatus": "partial"}),
                (shared, {"version": 1}),
            ])
            second = self.source(root, "20260819-110000-22222222", [
                (shared, {"version": 2}),
            ])
            selected = successor.select_candidates([first, second], {partial})
            successor.reject_partial_event_candidates(selected)
            self.assertNotIn(partial, selected)
            self.assertEqual(selected[shared][0], second[0])

    def test_unreviewed_partial_candidate_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            path = "data/generated/chess-results-event-details/tnr1.json"
            source = self.source(root, "20260819-100000-11111111", [
                (path, {"captureStatus": "partial"}),
            ])
            selected = successor.select_candidates([source], set())
            with self.assertRaisesRegex(successor.SuccessorError, "PARTIAL_EVENT_CANDIDATE_FORBIDDEN"):
                successor.reject_partial_event_candidates(selected)

    def test_reviewed_partial_can_become_explicit_delete(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            path = "data/generated/chess-results-event-details/tnr1.json"
            source = self.source(root, "20260819-100000-11111111", [
                (path, {"captureStatus": "partial"}),
            ])
            selected = successor.select_candidates([source], set(), {path})
            successor.reject_partial_event_candidates(selected)
            self.assertEqual(selected[path][2]["operation"], "delete")
            self.assertIsNone(successor.candidate_content(selected[path][1], selected[path][2]))

    def test_manifest_records_separate_production_and_shadow_baselines(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            path = "data/generated/chess-results-event-details/tnr2.json"
            source = self.source(root, "20260819-100000-11111111", [
                (path, {"captureStatus": "complete", "players": []}),
            ])
            selected = successor.select_candidates([source], set())
            content = successor.candidate_content(source[1], source[2]["files"][0])
            manifest, contents = successor.build_manifest(
                run_id="20260821-120000-33333333",
                sources=[source],
                selected=selected,
                dropped_paths=set(),
                deleted_paths=set(),
                production_commit="b" * 40,
                production_oids={path: "c" * 40},
                production_contents={path: b"old"},
                shadow_heads={path: {"sha256": "d" * 64, "deleted": 0}},
            )
            item = manifest["files"][0]
            self.assertEqual(item["baseBlobOid"], "c" * 40)
            self.assertEqual(item["baseSha256"], hashlib.sha256(b"old").hexdigest())
            self.assertEqual(item["shadowBaseSha256"], "d" * 64)
            self.assertEqual(contents[path], content)

    def test_completed_shadow_migration_loads_as_baseline(self):
        with tempfile.TemporaryDirectory() as temporary:
            migration = pathlib.Path(temporary)
            (migration / "migration.json").write_text(json.dumps({
                "migrationId": "baseline-test",
                "status": "delivered",
                "entries": [["data/generated/example.json", "a" * 40, "b" * 64, 2, "chess-results"]],
                "packages": [{"status": "complete"}],
                "cleanupPackages": [{"status": "complete"}],
            }), encoding="utf-8")
            migration_id, heads = successor.load_shadow_baseline(migration)
            self.assertEqual(migration_id, "baseline-test")
            self.assertEqual(heads["data/generated/example.json"]["sha256"], "b" * 64)

    def test_github_get_retries_read_failure(self):
        failure = __import__("subprocess").CalledProcessError(1, ["gh"], stderr=b"TLS")
        with (
            mock.patch.object(
                successor.publish_data_via_api,
                "api",
                side_effect=[failure, {"ok": True}],
            ) as api,
            mock.patch.object(successor.time, "sleep") as sleep,
        ):
            self.assertEqual(successor.github_get("owner/repo", "/path"), {"ok": True})
        self.assertEqual(api.call_count, 2)
        sleep.assert_called_once_with(2)


if __name__ == "__main__":
    unittest.main()
