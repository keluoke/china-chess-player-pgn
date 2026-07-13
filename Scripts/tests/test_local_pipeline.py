from __future__ import annotations

import json
import io
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
LOCAL = SCRIPTS / "local"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(LOCAL))

import run_manager  # noqa: E402
import panel as local_panel  # noqa: E402
import apply_aliases_to_registry  # noqa: E402
import build_event_details  # noqa: E402
import build_static_player_pgn  # noqa: E402
import source_http  # noqa: E402
import source_policy  # noqa: E402
import stable_json  # noqa: E402
import validate_incoming  # noqa: E402
import validate_registry_authority  # noqa: E402
import sync_chess_results_event  # noqa: E402
import sync_lichess_broadcast_bulk  # noqa: E402
from sync_chinese_players import (  # noqa: E402
    RegistryPlayer,
    validate_fide_archive,
    validate_registry_population,
)


def git(repo: pathlib.Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


class PolicyTests(unittest.TestCase):
    def test_chess_results_is_link_only_by_default(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(source_policy.chess_results_release_policy(), "link-only")
            with self.assertRaises(source_policy.SourcePolicyError) as caught:
                source_policy.require_chess_results_publication()
        self.assertEqual(caught.exception.code, "COMPLIANCE_POLICY_BLOCKED")

    def test_manual_and_raw_paths_are_never_releasable(self) -> None:
        for path in ("data/manual/x.csv", "data/community/x.csv", "data/generated/raw.html.gz"):
            with self.assertRaises(run_manager.RunManagerError):
                run_manager.validate_release_path(path)

    def test_link_only_chess_results_manifest_is_rejected(self) -> None:
        payload = {
            "schemaVersion": 1,
            "source": {"source": "Chess-Results", "releasePolicy": "link-only"},
            "files": [{"path": "docs/data/bulk/x", "operation": "upsert"}],
        }
        with self.assertRaises(run_manager.RunManagerError) as caught:
            run_manager.validate_manifest(payload)
        self.assertEqual(caught.exception.code, "COMPLIANCE_POLICY_BLOCKED")

    def test_even_authorized_chess_results_never_enters_release_manifest(self) -> None:
        payload = {
            "schemaVersion": 1,
            "source": {"source": "Chess-Results", "releasePolicy": "authorized"},
            "files": [{"path": "docs/data/bulk/x", "operation": "delete", "sha256": None}],
        }
        with self.assertRaises(run_manager.RunManagerError) as caught:
            run_manager.validate_manifest(payload)
        self.assertEqual(caught.exception.code, "COMPLIANCE_POLICY_BLOCKED")

    def test_target_submission_detects_scraped_content(self) -> None:
        self.assertTrue(validate_incoming.forbidden_content({"rows": [{"tnr": 1}]}))
        self.assertTrue(validate_incoming.forbidden_content('[Event "Example"]'))
        self.assertFalse(validate_incoming.forbidden_content({"sourceURL": "https://chess-results.com/tnr1.aspx"}))

    def test_target_submission_rejects_unknown_payload_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            submission = pathlib.Path(temp) / "20260713-120000-abcdef"
            submission.mkdir()
            (submission / "manifest.json").write_text(json.dumps({
                "schema": "china-chess-target-submission/v2",
                "targets": [{
                    "type": "event-target",
                    "tournamentID": "123456",
                    "scrapeResult": "hidden parsed content",
                }],
            }))
            validate_incoming.ERRORS.clear()
            validate_incoming.validate_submission(submission)
            self.assertTrue(any("非目标字段" in item for item in validate_incoming.ERRORS))
            validate_incoming.ERRORS.clear()

    def test_derived_identity_cannot_override_registry(self) -> None:
        registry = {"8602980": {
            "fideID": "8602980", "displayName": "侯逸凡", "chineseName": "侯逸凡", "standard": 2600,
        }}
        errors = validate_registry_authority.validate_document(
            {"player": {"fideID": "8602980", "displayName": "居文君", "standard": 2500}},
            registry,
            "test.json",
        )
        self.assertEqual(len(errors), 2)

    def test_offline_alias_rebuild_does_not_read_previous_identity(self) -> None:
        player = {
            "fideID": "8602980",
            "name": "Hou, Yifan",
            "displayName": "居文君",
            "chineseName": "居文君",
            "pinyin": "ju wenjun",
            "aliases": ["居文君", "stale"],
        }
        self.assertTrue(apply_aliases_to_registry.reset_to_source_identity(player))
        self.assertEqual(player["displayName"], "Hou, Yifan")
        self.assertNotIn("居文君", player["aliases"])
        apply_aliases_to_registry.apply_correction(
            player, {"wrong": "居文君", "correct": "侯逸凡"}
        )
        self.assertEqual(player["displayName"], "侯逸凡")

    def test_public_event_person_drops_raw_club(self) -> None:
        person = {"name": "Example", "club": "北京市海淀区示例小学"}
        build_event_details.minimize_public_location(person)
        self.assertNotIn("club", person)
        self.assertEqual(person["publicLocation"], "北京")

        unknown = {"name": "Example", "club": "某某棋院"}
        build_event_details.minimize_public_location(unknown)
        self.assertNotIn("club", unknown)
        self.assertNotIn("publicLocation", unknown)

    def test_player_pgn_semantic_hash_ignores_created_timestamp(self) -> None:
        old = "% Games: 1\n% Created: 2026-01-01T00:00:00+00:00\n\n[Event \"A\"]\n"
        new = "% Games: 1\n% Created: 2026-07-13T00:00:00+00:00\n\n[Event \"A\"]\n"
        changed = new.replace('[Event "A"]', '[Event "B"]')
        self.assertEqual(
            build_static_player_pgn.semantic_package_hash(old),
            build_static_player_pgn.semantic_package_hash(new),
        )
        self.assertNotEqual(
            build_static_player_pgn.semantic_package_hash(old),
            build_static_player_pgn.semantic_package_hash(changed),
        )


class RunManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "test@example.com")
        git(self.repo, "config", "user.name", "Test")
        (self.repo / "docs/data/registry").mkdir(parents=True)
        (self.repo / "data/generated").mkdir(parents=True)
        (self.repo / "docs/data/registry/players.json").write_text("[]\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "base")
        self.run_dir = self.root / "run"
        self.run_dir.mkdir()
        run_manager.atomic_json(
            self.run_dir / "run.json",
            {"runId": "test-run", "command": "registry", "status": "running"},
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_prepare_stages_only_manifest_files(self) -> None:
        allow = ["docs/data/registry"]
        run_manager.preflight(self.repo, self.run_dir, allow)
        target = self.repo / "docs/data/registry/players.json"
        target.write_text('[{"fideID":"1"}]\n')
        result = run_manager.prepare_release(self.repo, self.run_dir, "registry", allow)
        self.assertEqual(result["changed"], 1)
        staged = subprocess.check_output(
            ["git", "diff", "--cached", "--name-only"], cwd=self.repo, text=True
        ).splitlines()
        self.assertEqual(staged, [run_manager.MANIFEST_PATH, "docs/data/registry/players.json"])
        manifest = json.loads((self.repo / run_manager.MANIFEST_PATH).read_text())
        self.assertEqual(manifest["source"]["source"], "FIDE Rating List")
        self.assertEqual(manifest["files"][0]["path"], "docs/data/registry/players.json")

    def test_preflight_rejects_dirty_owned_path(self) -> None:
        (self.repo / "docs/data/registry/players.json").write_text("dirty\n")
        with self.assertRaises(run_manager.RunManagerError) as caught:
            run_manager.preflight(self.repo, self.run_dir, ["docs/data/registry"])
        self.assertEqual(caught.exception.code, "DIRTY_RELEASE_PATH")

    def test_outside_change_during_run_blocks_release(self) -> None:
        run_manager.preflight(self.repo, self.run_dir, ["docs/data/registry"])
        (self.repo / "README.md").write_text("concurrent\n")
        (self.repo / "docs/data/registry/players.json").write_text("changed\n")
        with self.assertRaises(run_manager.RunManagerError) as caught:
            run_manager.prepare_release(self.repo, self.run_dir, "registry", ["docs/data/registry"])
        self.assertEqual(caught.exception.code, "WORKTREE_CHANGED_DURING_RUN")

    def test_source_cannot_cross_release_path_boundary(self) -> None:
        payload = {
            "schemaVersion": 1,
            "source": {
                "source": "Lichess Broadcasts",
                "releasePolicy": "cc-by-sa-4.0",
                "licenseURL": "https://creativecommons.org/licenses/by-sa/4.0/",
                "attributionURL": "https://database.lichess.org/",
            },
            "files": [{"path": "docs/data/registry/players.json", "operation": "upsert"}],
        }
        with self.assertRaises(run_manager.RunManagerError) as caught:
            run_manager.validate_manifest(payload)
        self.assertEqual(caught.exception.code, "RELEASE_SOURCE_PATH_MISMATCH")


class FideArchiveTests(unittest.TestCase):
    def test_truncated_zip_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp) / "players.zip"
            path.write_bytes(b"PK\x03\x04" + b"x" * (1024 * 1024))
            with self.assertRaises(OSError):
                validate_fide_archive(path)

    def test_structurally_valid_large_list_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp) / "players.zip"
            payload = b"<players>" + os.urandom(6 * 1024 * 1024) + b"</players>"
            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
                archive.writestr("players.xml", payload)
            validate_fide_archive(path)

    def test_population_regression_compares_public_previous_registry(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            previous = pathlib.Path(temp)
            (previous / "manifest.json").write_text(json.dumps({
                "totals": {"players": 10000},
            }))
            players = [
                RegistryPlayer(fide_id=str(100000 + index), name=f"Player {index}", federation="CHN")
                for index in range(5000)
            ]
            with self.assertRaises(ValueError):
                validate_registry_population(players, previous)


class SourceHTTPTests(unittest.TestCase):
    class DummyResponse:
        def __init__(self, *, fail_read: bool = False):
            self.fail_read = fail_read
            self.closed = False

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            self.closed = True

        def read(self):
            if self.fail_read:
                raise OSError("stream interrupted")
            return b"complete"

        def close(self):
            self.closed = True

    def test_stream_success_is_recorded_after_context_exit(self) -> None:
        response = self.DummyResponse()
        managed = source_http._ManagedResponse(response, "lichess")
        with mock.patch.object(source_http, "_record_result") as record:
            with managed as stream:
                self.assertEqual(stream.read(), b"complete")
                record.assert_not_called()
            record.assert_called_once_with("lichess", True)

    def test_stream_read_failure_is_recorded_once(self) -> None:
        response = self.DummyResponse(fail_read=True)
        managed = source_http._ManagedResponse(response, "chess-results")
        with mock.patch.object(source_http, "_record_result") as record:
            with self.assertRaises(OSError), managed as stream:
                stream.read()
            record.assert_called_once_with("chess-results", False)


class StableJSONTests(unittest.TestCase):
    def test_generated_at_only_change_keeps_existing_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp) / "derived.json"
            stable_json.write_json(
                path,
                {"generatedAt": "2026-01-01T00:00:00Z", "value": 1},
                indent=2,
            )
            original = path.read_bytes()
            changed = stable_json.write_json(
                path,
                {"generatedAt": "2026-07-13T00:00:00Z", "value": 1},
                indent=2,
            )
            self.assertFalse(changed)
            self.assertEqual(path.read_bytes(), original)


class PrivateCaptureQueueTests(unittest.TestCase):
    def test_recent_private_capture_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            queue_path = root / "queue.json"
            state_path = root / "capture-state.json"
            queue_path.write_text(json.dumps({"targets": [
                {"nextAction": "capture-event", "tournamentID": "123456"},
                {"nextAction": "capture-event", "tournamentID": "234567"},
            ]}))
            state_path.write_text(json.dumps({"events": {
                "123456": {"capturedAt": "2999-01-01T00:00:00+00:00"},
            }}))
            with (
                mock.patch.object(sync_chess_results_event, "EVENT_QUEUE", queue_path),
                mock.patch.object(sync_chess_results_event, "CAPTURE_STATE", state_path),
            ):
                self.assertEqual(sync_chess_results_event.queue_targets(10, 30), ["234567"])

    def test_panel_reports_private_capture_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            queue_path = root / "queue.json"
            state_path = root / "capture-state.json"
            queue_path.write_text(json.dumps({"targets": [{
                "tournamentID": "123456", "eventName": "Example", "priorityScore": 9,
            }]}))
            state_path.write_text(json.dumps({"events": {"123456": {
                "capturedAt": "2026-07-13T00:00:00+00:00", "players": 20, "rounds": 7,
            }}}))
            with (
                mock.patch.object(local_panel, "QUEUE_PATH", queue_path),
                mock.patch.object(local_panel, "CAPTURE_STATE_PATH", state_path),
            ):
                target = local_panel.queue_payload()["targets"][0]
            self.assertEqual(target["status"], "privately-captured")
            self.assertEqual(target["captureStats"]["players"], 20)

    def test_private_root_inside_repository_is_rejected_before_network(self) -> None:
        argv = [
            "sync_chess_results_event.py",
            "12345",
            "--private-root",
            str(sync_chess_results_event.ROOT / ".forbidden-private-capture"),
        ]
        with mock.patch.object(sys, "argv", argv), self.assertRaises(SystemExit) as caught:
            sync_chess_results_event.main()
        self.assertIn("COMPLIANCE_POLICY_BLOCKED", str(caught.exception))


class LichessValidationTests(unittest.TestCase):
    def test_invalid_local_shard_signature_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp) / "broadcast.pgn.zst"
            path.write_bytes(b"not-zstd")
            with self.assertRaises(RuntimeError):
                sync_lichess_broadcast_bulk.validate_local_shard(path, 8)

    def test_text_decoder_recovers_from_invalid_utf8(self) -> None:
        reader = sync_lichess_broadcast_bulk.TextChunkReader(io.BytesIO(b"abc\xffdef"))
        self.assertEqual(reader.read(64) + reader.read(64), "abc\ufffddef")


if __name__ == "__main__":
    unittest.main()
