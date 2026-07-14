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
import build_api  # noqa: E402
import build_event_details  # noqa: E402
import build_public_metrics  # noqa: E402
import build_static_player_pgn  # noqa: E402
import source_http  # noqa: E402
import source_policy  # noqa: E402
import stable_json  # noqa: E402
import validate_incoming  # noqa: E402
import validate_registry_authority  # noqa: E402
import sync_chess_results_event  # noqa: E402
import sync_static_pgn  # noqa: E402
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


class OutboxTests(unittest.TestCase):
    """The delivery outbox decouples collection from GitHub delivery."""

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
            {"runId": "outbox-test-run", "command": "registry", "status": "running"},
        )
        self.state_patch = mock.patch.object(
            run_manager, "local_state_root", return_value=self.root / "state"
        )
        self.state_patch.start()

    def tearDown(self) -> None:
        self.state_patch.stop()
        self.temp.cleanup()

    def prepare_commit(self) -> str:
        allow = ["docs/data/registry"]
        run_manager.preflight(self.repo, self.run_dir, allow)
        (self.repo / "docs/data/registry/players.json").write_text('[{"fideID":"1"}]\n')
        run_manager.prepare_release(self.repo, self.run_dir, "registry", allow)
        git(self.repo, "commit", "-qm", "release")
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.repo, text=True).strip()

    def test_save_list_update_round_trip(self) -> None:
        sha = self.prepare_commit()
        saved = run_manager.outbox_save(self.repo, self.run_dir, sha)
        self.assertEqual(saved["runId"], "outbox-test-run")
        entry = pathlib.Path(saved["outbox"])
        self.assertTrue((entry / "manifest.json").is_file())
        self.assertTrue((entry / "files" / "docs/data/registry/players.json").is_file())
        pending = run_manager.outbox_entries("pending")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["commit"], sha)
        updated = run_manager.outbox_update("outbox-test-run", "pushed", sha, "git", None)
        self.assertEqual(updated["status"], "pushed")
        self.assertEqual(run_manager.outbox_entries("pending"), [])

    def test_bundle_content_must_match_manifest_hash(self) -> None:
        sha = self.prepare_commit()
        manifest = json.loads((self.run_dir / "release-manifest.json").read_text())
        manifest["files"][0]["sha256"] = "0" * 64
        run_manager.atomic_json(self.run_dir / "release-manifest.json", manifest)
        with self.assertRaises(run_manager.RunManagerError) as caught:
            run_manager.outbox_save(self.repo, self.run_dir, sha)
        self.assertEqual(caught.exception.code, "RELEASE_HASH_MISMATCH")

    def test_pending_entries_deliver_oldest_first(self) -> None:
        sha = self.prepare_commit()
        run_manager.outbox_save(self.repo, self.run_dir, sha)
        run_manager.atomic_json(
            self.run_dir / "run.json",
            {"runId": "outbox-test-run-2", "command": "registry", "status": "running"},
        )
        manifest = json.loads((self.run_dir / "release-manifest.json").read_text())
        manifest["runId"] = "outbox-test-run-2"
        run_manager.atomic_json(self.run_dir / "release-manifest.json", manifest)
        run_manager.outbox_save(self.repo, self.run_dir, sha)
        pending = run_manager.outbox_entries("pending")
        self.assertEqual([item["runId"] for item in pending], ["outbox-test-run", "outbox-test-run-2"])


class ApiFallbackPolicyTests(unittest.TestCase):
    def test_fails_closed_without_a_validated_bundle(self) -> None:
        import publish_data_via_api

        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.object(run_manager, "local_state_root", return_value=pathlib.Path(temp)):
                with self.assertRaises(SystemExit) as caught:
                    publish_data_via_api.load_bundle(None)
        self.assertIn("API_DELIVERY_BLOCKED", str(caught.exception))

    def test_api_path_shares_the_single_manifest_policy(self) -> None:
        # The API transport validates through run_manager.validate_manifest:
        # manual/community/incoming and raw HTML are rejected identically.
        payload = {
            "schemaVersion": 1,
            "runId": "x",
            "source": {"source": "FIDE Rating List", "releasePolicy": "factual-registry-projection"},
            "files": [{"path": "data/manual/x.csv", "operation": "upsert", "sha256": "0" * 64, "bytes": 1}],
        }
        with self.assertRaises(run_manager.RunManagerError):
            run_manager.validate_manifest(payload)


REFRESH_SH = LOCAL / "refresh.sh"


def bash_snippet(script: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env={**os.environ, **(env or {})}
    )
    return result.stdout.strip()


class GitTransportTests(unittest.TestCase):
    def classify(self, log: str) -> str:
        # eval "$(sed ...)" instead of source <(...): macOS ships bash 3.2
        # where sourcing from process substitution is unreliable.
        script = (
            f'eval "$(sed -n "/^classify_git_error()/,/^}}/p" "{REFRESH_SH}")"; '
            f"classify_git_error \"$1\""
        )
        result = subprocess.run(["bash", "-c", script, "_", log], capture_output=True, text=True)
        return result.stdout.strip()

    def test_error_classification_is_structured(self) -> None:
        cases = {
            "fatal: unable to access: Could not resolve host: github.com": "GIT_DNS_FAILURE",
            "SSL certificate problem: unable to get local issuer": "GIT_TLS_FAILURE",
            "Received HTTP code 407 from proxy after CONNECT": "GIT_PROXY_FAILURE",
            "fatal: Authentication failed for 'https://github.com/x'": "GIT_AUTH_FAILED",
            "! [remote rejected] main -> main (pre-receive hook declined)": "GIT_REMOTE_REJECTED",
            "fatal: unable to access: Connection timed out": "GIT_CONNECT_FAILURE",
            "some other unknown failure": "GIT_PUSH_FAILED",
        }
        for log, expected in cases.items():
            self.assertEqual(self.classify(log), expected, log)

    def probe_with_curl_stub(self, status: str) -> int:
        with tempfile.TemporaryDirectory() as temp:
            stub = pathlib.Path(temp) / "curl"
            stub.write_text(f"#!/bin/sh\nprintf '{status}'\n")
            stub.chmod(0o755)
            script = (
                f'eval "$(sed -n "/^github_ok()/,/^}}/p" "{REFRESH_SH}")"; '
                'github_probe_url() { echo "https://example.invalid/info/refs"; }; '
                'github_ok ""; echo $?'
            )
            result = subprocess.run(
                ["bash", "-c", script],
                capture_output=True, text=True,
                env={**os.environ, "PATH": f"{temp}:{os.environ['PATH']}"},
            )
            return int(result.stdout.strip().splitlines()[-1])

    def test_http_502_gateway_page_is_not_a_usable_route(self) -> None:
        self.assertEqual(self.probe_with_curl_stub("502"), 1)

    def test_smart_http_200_and_auth_401_prove_the_route(self) -> None:
        self.assertEqual(self.probe_with_curl_stub("200"), 0)
        self.assertEqual(self.probe_with_curl_stub("401"), 0)


class SharedStateProjectionTests(unittest.TestCase):
    """Panel display and collector scheduling must agree on 'schedulable'."""

    def test_panel_schedulable_equals_scheduler_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            queue_path = root / "queue.json"
            state_path = root / "capture-state.json"
            queue_path.write_text(json.dumps({"targets": [
                {"nextAction": "capture-event", "tournamentID": "100001", "eventName": "A"},
                {"nextAction": "capture-event", "tournamentID": "100002", "eventName": "B"},
                {"nextAction": "capture-event", "tournamentID": "100003", "eventName": "C"},
                {"nextAction": "monitor", "tournamentID": "100004", "eventName": "D"},
            ]}))
            state_path.write_text(json.dumps({"schemaVersion": 2, "events": {
                "100001": {"status": "complete", "capturedAt": "2999-01-01T00:00:00+00:00"},
                "100002": {
                    "status": "quarantined", "nextRetryAt": "2999-01-01T00:00:00+00:00",
                    "parserVersion": sync_chess_results_event.PARSER_VERSION,
                },
            }}))
            with (
                mock.patch.object(local_panel, "QUEUE_PATH", queue_path),
                mock.patch.object(local_panel, "CAPTURE_STATE_PATH", state_path),
                mock.patch.object(sync_chess_results_event, "EVENT_QUEUE", queue_path),
                mock.patch.object(sync_chess_results_event, "CAPTURE_STATE", state_path),
            ):
                panel_view = local_panel.queue_payload()
                scheduler = sync_chess_results_event.queue_targets(10, local_panel.REFRESH_DAYS)
            schedulable = [t["tournamentID"] for t in panel_view["targets"] if t["schedulable"]]
            self.assertEqual(schedulable, scheduler)
            self.assertEqual(panel_view["summary"]["schedulable"], len(scheduler))
            self.assertEqual(panel_view["upcoming"], scheduler)

    def test_recent_captures_include_off_queue_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            queue_path = root / "queue.json"
            state_path = root / "capture-state.json"
            queue_path.write_text(json.dumps({"targets": [
                {"nextAction": "capture-event", "tournamentID": "100001"},
            ]}))
            state_path.write_text(json.dumps({"schemaVersion": 2, "events": {
                "999888": {  # pasted TNR, not in the static queue
                    "status": "complete", "capturedAt": "2026-07-14T12:00:00+00:00",
                    "updatedAt": "2026-07-14T12:00:00+00:00", "players": 25, "rounds": 9,
                },
            }}))
            with (
                mock.patch.object(local_panel, "QUEUE_PATH", queue_path),
                mock.patch.object(local_panel, "CAPTURE_STATE_PATH", state_path),
            ):
                entries = local_panel.recent_payload()["entries"]
            self.assertEqual(entries[0]["tournamentID"], "999888")
            self.assertFalse(entries[0]["inQueue"])
            self.assertEqual(entries[0]["status"], "complete")


class ReceiptAdvanceTests(unittest.TestCase):
    def test_advance_stops_at_first_unconfirmed_stage(self) -> None:
        import check_receipts

        delivery = {"status": "pushed"}
        self.assertEqual(check_receipts.advance(delivery, {}), "pushed")
        self.assertEqual(
            check_receipts.advance(delivery, {"ingested-to-main": {"ok": True}}),
            "ingested-to-main",
        )
        self.assertEqual(
            check_receipts.advance(delivery, {
                "ingested-to-main": {"ok": True},
                "indexes-rebuilt": {"ok": True},
                "deployed": {"ok": True},
            }),
            "deployed",
        )
        self.assertEqual(
            check_receipts.advance(delivery, {
                "ingested-to-main": {"ok": True},
                "indexes-rebuilt": {"ok": False},
                "deployed": {"ok": True},  # out-of-order confirmations never skip a stage
            }),
            "ingested-to-main",
        )
        self.assertEqual(
            check_receipts.advance({"status": "deployed"}, {"online-verified": {"ok": True}}),
            "online-verified",
        )

    def test_pushed_is_never_displayed_as_published(self) -> None:
        import check_receipts

        self.assertEqual(
            check_receipts.STAGE_ORDER,
            ["pushed", "ingested-to-main", "indexes-rebuilt", "deployed", "online-verified"],
        )
        self.assertEqual(check_receipts.STAGE_ORDER[-1], "online-verified")


class IngestWorkflowContractTests(unittest.TestCase):
    def test_ingest_applies_the_immutable_event_sha(self) -> None:
        workflow = (SCRIPTS.parent / ".github" / "workflows" / "ingest-local-data.yml").read_text(encoding="utf-8")
        self.assertIn("github.sha", workflow)
        self.assertIn("steps.source.outputs.sha", workflow)
        self.assertNotIn("--source-ref origin/local-data", workflow)
        self.assertIn("ingest receipt", workflow.lower())


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

    def test_api_rebuild_preserves_bytes_and_prunes_stale_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            registry = root / "registry.json"
            by_player = root / "by-player"
            leaderboards = root / "leaderboards.json"
            api_root = root / "api"
            by_player.mkdir()
            registry.write_text(json.dumps([{
                "fideID": "1",
                "displayName": "Player One",
                "name": "Player, One",
                "federation": "CHN",
                "inactive": False,
            }]))
            (by_player / "fide-1.json").write_text(json.dumps({
                "player": {"fideID": "1"},
                "totals": {"games": 1},
                "packages": [],
                "events": [],
            }))
            leaderboards.write_text(json.dumps({"groups": []}))
            metrics = {
                "metricVersion": 1,
                "scope": "test",
                "totals": {
                    "players": 1,
                    "withChineseName": 0,
                    "playersWithGames": 1,
                    "games": 1,
                },
            }
            with (
                mock.patch.object(build_api, "REGISTRY_PLAYERS", registry),
                mock.patch.object(build_api, "BY_PLAYER_INDEX", by_player),
                mock.patch.object(build_api, "LEADERBOARDS", leaderboards),
                mock.patch.object(build_api, "API_ROOT", api_root),
                mock.patch.object(build_api, "REPO_ROOT", root),
                mock.patch.object(build_api, "canonical_public_metrics", return_value=metrics),
            ):
                build_api.main()
                for path in api_root.rglob("*.json"):
                    payload = json.loads(path.read_text())
                    if "generatedAt" in payload:
                        payload["generatedAt"] = "2000-01-01T00:00:00+00:00"
                        path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
                stale = api_root / "players" / "fide-999.json"
                stale.write_text("{}\n")
                expected = {path: path.read_bytes() for path in api_root.rglob("*.json") if path != stale}
                build_api.main()

            self.assertFalse(stale.exists())
            self.assertEqual(
                {path: path.read_bytes() for path in api_root.rglob("*.json")},
                expected,
            )

    def test_source_manifest_uses_pre_enrichment_timestamp(self) -> None:
        source_totals = {"players": 2, "events": 3, "pgnFiles": 4, "games": 5, "bytes": 6}
        previous = {
            "schemaVersion": 1,
            "generatedAt": "2026-01-01T00:00:00+00:00",
            "storage": {"pgnRoot": "data/pgn"},
            "totals": {**source_totals, "players": 9, "games": 10},
            "sources": ["Chess-Results"],
            "sourceTotals": source_totals,
            "metricContract": {"version": 1},
        }
        current = {
            "schemaVersion": 1,
            "generatedAt": "2026-07-13T00:00:00+00:00",
            "storage": {"pgnRoot": "data/pgn"},
            "totals": source_totals,
            "sources": ["Chess-Results"],
        }
        stable = sync_static_pgn.preserve_source_manifest_generated_at(previous, current)
        self.assertEqual(stable["generatedAt"], previous["generatedAt"])

        changed = {**current, "totals": {**source_totals, "games": 6}}
        refreshed = sync_static_pgn.preserve_source_manifest_generated_at(previous, changed)
        self.assertEqual(refreshed["generatedAt"], current["generatedAt"])

    def test_public_metric_enrichment_keeps_original_source_totals(self) -> None:
        source_totals = {"players": 2, "events": 3, "pgnFiles": 4, "games": 5}
        index = {
            "totals": source_totals,
            "generatedAt": "2026-01-01T00:00:00+00:00",
        }
        metrics = {
            "metricVersion": 1,
            "scope": "test",
            "totals": {"playersWithGames": 9, "games": 10},
        }
        first = build_public_metrics.enrich_index_manifest(index, metrics)
        second = build_public_metrics.enrich_index_manifest(first, metrics)
        self.assertEqual(first, second)
        self.assertEqual(second["sourceTotals"], source_totals)
        self.assertEqual(second["totals"]["players"], 9)
        self.assertEqual(second["totals"]["games"], 10)


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
