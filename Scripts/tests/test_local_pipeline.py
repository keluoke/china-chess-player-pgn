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
import fetch_event_pgn  # noqa: E402
import source_http  # noqa: E402
import source_policy  # noqa: E402
import stable_json  # noqa: E402
import validate_incoming  # noqa: E402
import validate_registry_authority  # noqa: E402
import sync_chess_results_event  # noqa: E402
import sync_static_pgn  # noqa: E402
import sync_lichess_broadcast_bulk  # noqa: E402
import targeted_series_capture as targeted_capture  # noqa: E402
import targeted_capture_panel as targeted_panel  # noqa: E402
import import_event_list  # noqa: E402
from sync_chinese_players import (  # noqa: E402
    RegistryPlayer,
    validate_fide_archive,
    validate_registry_population,
)


def git(repo: pathlib.Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


class TargetedCaptureCheckpointTests(unittest.TestCase):
    def test_csv_import_preserves_event_and_group_titles(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            source = root / "events.csv"
            overrides = root / "task-overrides.json"
            run_state = root / "run-state.json"
            source.write_text(
                "2026年全国国际象棋棋协大师赛（测试站）,\n"
                "公开组,https://chess-results.com/tnr1455665.aspx?lan=33&art=0\n"
                "女子组,https://s2.chess-results.com/tnr1455661.aspx?lan=33&art=0\n",
                encoding="utf-8",
            )
            result = import_event_list.import_targets(source, overrides_path=overrides, run_state_path=run_state)
            payload = json.loads(overrides.read_text(encoding="utf-8"))
            self.assertEqual(result["targets"], 2)
            self.assertEqual(
                payload["additions"]["1455665"]["displayName"],
                "2026年全国国际象棋棋协大师赛（测试站） · 公开组",
            )
            self.assertEqual(json.loads(run_state.read_text(encoding="utf-8"))["status"], "pending")

    def test_manual_only_selection_excludes_catalogue_targets(self) -> None:
        plan = {"targets": [
            {"tournamentID": "100001", "series": "chess-association-master", "future": False, "existingRecord": False},
            {"tournamentID": "100002", "series": "manual-review", "future": False, "existingRecord": False},
            {"tournamentID": "100003", "series": "manual-review", "future": False, "existingRecord": True},
        ]}
        selected = targeted_capture.selected_targets(
            plan, refresh_existing=False, include_future=False, manual_only=True,
        )
        self.assertEqual([row["tournamentID"] for row in selected], ["100002"])

    def test_manual_task_input_accepts_tnr_and_source_link_only(self) -> None:
        self.assertEqual(targeted_panel.normalize_tnr("tnr1383"), "1383")
        self.assertEqual(
            targeted_panel.normalize_tnr("https://chess-results.com/tnr1429695.aspx?lan=1"), "1429695",
        )
        self.assertEqual(targeted_panel.normalize_tnr("https://example.com/tnr1429695.aspx"), "")

    def test_completed_capture_is_not_retried_when_release_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            capture_state = pathlib.Path(temp) / "capture-state.json"
            pgn_archive = pathlib.Path(temp) / "event-pgn"
            pgn_archive.mkdir()
            (pgn_archive / "tnr999001.pgn").write_text('[Event "test"]\n\n1. e4 *\n', encoding="utf-8")
            (pgn_archive / "tnr999002.pgn").write_text('[Event "test"]\n\n1. d4 *\n', encoding="utf-8")
            capture_state.write_text(json.dumps({"events": {
                "999001": {"status": "complete", "runPrivateRoot": "/private/runs/test-run"},
                "999002": {"status": "complete", "runPrivateRoot": "/private/runs/test-run"},
            }}), encoding="utf-8")
            state = {
                "status": "stopped", "nextBatchIndex": 0, "completedBatches": 0,
                "currentTargets": ["999001", "999002"],
                "lastOutcome": {"runId": "test-run", "errorCode": "WORKTREE_CHANGED_DURING_RUN"},
            }
            batches = [[{"tournamentID": "999001"}, {"tournamentID": "999002"}]]
            with mock.patch.object(targeted_capture, "CAPTURE_STATE_PATH", capture_state), \
                 mock.patch.object(targeted_capture, "EVENT_PGN_ARCHIVE", pgn_archive):
                self.assertTrue(targeted_capture.recover_completed_batch(state, batches))
            self.assertEqual(state["nextBatchIndex"], 1)
            self.assertEqual(state["completedBatches"], 1)
            self.assertEqual(state["lastOutcome"]["result"], "capture-complete-release-blocked")

    def test_old_capture_cannot_advance_new_failed_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            capture_state = pathlib.Path(temp) / "capture-state.json"
            capture_state.write_text(json.dumps({"events": {
                "999001": {"status": "complete", "runPrivateRoot": "/private/runs/older-run"},
            }}), encoding="utf-8")
            with mock.patch.object(targeted_capture, "CAPTURE_STATE_PATH", capture_state):
                self.assertFalse(targeted_capture.batch_capture_completed(["999001"], "new-run"))

    def test_missing_pgn_archive_cannot_advance_completed_event_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            capture_state = pathlib.Path(temp) / "capture-state.json"
            capture_state.write_text(json.dumps({"events": {
                "999001": {"status": "complete", "runPrivateRoot": "/private/runs/test-run"},
            }}), encoding="utf-8")
            with mock.patch.object(targeted_capture, "CAPTURE_STATE_PATH", capture_state), \
                 mock.patch.object(targeted_capture, "EVENT_PGN_ARCHIVE", pathlib.Path(temp) / "missing-pgn"):
                self.assertFalse(targeted_capture.batch_capture_completed(["999001"], "test-run"))

    def test_explicit_source_pgn_gap_can_advance_without_an_empty_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            capture_state = root / "capture-state.json"
            details = root / "details"
            details.mkdir()
            capture_state.write_text(json.dumps({"events": {
                "999001": {"status": "complete", "runPrivateRoot": "/private/runs/test-run"},
            }}), encoding="utf-8")
            (details / "tnr999001.json").write_text(json.dumps({"rounds": [{"pairings": [{
                "white": {"playerNo": "1", "name": "White"},
                "black": {"playerNo": "2", "name": "Black"},
                "hasPGN": False, "pgnURL": "",
            }]}]}), encoding="utf-8")
            with mock.patch.object(targeted_capture, "CAPTURE_STATE_PATH", capture_state), \
                 mock.patch.object(targeted_capture, "EVENT_PGN_ARCHIVE", root / "missing-pgn"), \
                 mock.patch.object(targeted_capture, "DETAILS", details):
                self.assertTrue(targeted_capture.batch_capture_completed(["999001"], "test-run"))

    def test_active_source_backoff_blocks_a_batch_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            capture_state = pathlib.Path(temp) / "capture-state.json"
            capture_state.write_text(json.dumps({"events": {
                "999001": {"status": "retry-wait", "nextRetryAt": "2999-01-01T00:00:00+00:00"},
            }}), encoding="utf-8")
            with mock.patch.object(targeted_capture, "CAPTURE_STATE_PATH", capture_state):
                self.assertEqual(targeted_capture.retry_after_for(["999001"]), "2999-01-01T00:00:00+00:00")

    def test_quarantine_review_date_is_not_a_source_backoff(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            capture_state = pathlib.Path(temp) / "capture-state.json"
            capture_state.write_text(json.dumps({"events": {
                "999001": {"status": "quarantined", "nextRetryAt": "2999-01-01T00:00:00+00:00"},
            }}), encoding="utf-8")
            with (
                mock.patch.object(targeted_capture, "CAPTURE_STATE_PATH", capture_state),
                mock.patch.object(targeted_panel, "CAPTURE_STATE_PATH", capture_state),
            ):
                self.assertIsNone(targeted_capture.retry_after_for(["999001"]))
                self.assertIsNone(targeted_panel.active_retry_after({"currentTargets": ["999001"]}))

    def test_elapsed_retry_target_is_prioritized_but_future_backoff_is_not(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            capture_state = pathlib.Path(temp) / "capture-state.json"
            capture_state.write_text(json.dumps({"events": {
                "999001": {"status": "retry-wait", "nextRetryAt": "2000-01-01T00:00:00+00:00"},
                "999002": {"status": "retry-wait", "nextRetryAt": "2999-01-01T00:00:00+00:00"},
                "999003": {"status": "complete"},
            }}), encoding="utf-8")
            with mock.patch.object(targeted_capture, "CAPTURE_STATE_PATH", capture_state):
                self.assertEqual(targeted_capture.retry_ready_targets({"999001", "999002", "999003"}), ["999001"])

    def test_completed_plan_runs_due_retry_without_reopening_finished_batches(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            state = {"status": "completed", "nextBatchIndex": 1, "completedBatches": 1}
            plan = {"targets": [{"tournamentID": "999001", "future": False, "existingRecord": False}]}
            completed = subprocess.CompletedProcess([], 0)
            with (
                mock.patch.object(targeted_capture, "LOG_PATH", root / "capture.log"),
                mock.patch.object(targeted_capture, "RUN_STATE_PATH", root / "run-state.json"),
                mock.patch.object(targeted_capture, "run_state", return_value=(state, 1)),
                mock.patch.object(targeted_capture, "retry_ready_targets", return_value=["999001"]),
                mock.patch.object(targeted_capture.subprocess, "run", side_effect=[completed, completed]) as run,
            ):
                self.assertEqual(targeted_capture.run(plan, refresh_existing=False, include_future=False, manual_only=False, limit=0), 0)
            self.assertEqual(run.call_args_list[0].args[0], [
                "bash", "Scripts/local/refresh.sh", "event-queue", "--no-push", "--", "999001",
            ])
            self.assertEqual(state["status"], "completed")
            self.assertEqual(state["completedBatches"], 1)

    def test_completed_checkpoint_does_not_fall_back_to_legacy_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            targets = [{"tournamentID": "999001"}]
            signature = __import__("hashlib").sha256(b"999001").hexdigest()
            state_path = root / "run-state.json"
            state_path.write_text(json.dumps({
                "status": "completed", "targetSignature": signature,
                "nextBatchIndex": 1, "completedBatches": 1,
            }), encoding="utf-8")
            with (
                mock.patch.object(targeted_capture, "RUN_STATE_PATH", state_path),
                mock.patch.object(targeted_capture, "LOG_PATH", root / "capture.log"),
            ):
                state, start = targeted_capture.run_state(targets, [targets])
            self.assertEqual(start, 1)
            self.assertEqual(state["status"], "completed")

    def test_new_task_signature_does_not_inherit_old_log_batch_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            state_path = root / "run-state.json"
            log_path = root / "capture.log"
            state_path.write_text(json.dumps({
                "status": "completed", "targetSignature": "old-signature",
                "nextBatchIndex": 77, "completedBatches": 77,
            }), encoding="utf-8")
            log_path.write_text("STOP batch 16: historical failure\n", encoding="utf-8")
            targets = [{"tournamentID": "999001"}]
            with (
                mock.patch.object(targeted_capture, "RUN_STATE_PATH", state_path),
                mock.patch.object(targeted_capture, "LOG_PATH", log_path),
            ):
                state, start = targeted_capture.run_state(targets, [targets])
            self.assertEqual(start, 0)
            self.assertEqual(state["status"], "pending")

    def test_source_request_ledger_is_not_a_daily_capture_gate(self) -> None:
        self.assertIsNone(source_http.POLICIES["chess-results"].daily_budget)
        self.assertIsNone(source_http.POLICIES["fide"].daily_budget)

    def test_control_panel_reads_the_same_retry_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            capture_state = pathlib.Path(temp) / "capture-state.json"
            capture_state.write_text(json.dumps({"events": {
                "999001": {"status": "retry-wait", "nextRetryAt": "2999-01-01T00:00:00+00:00"},
            }}), encoding="utf-8")
            with mock.patch.object(targeted_panel, "CAPTURE_STATE_PATH", capture_state):
                self.assertEqual(
                    targeted_panel.active_retry_after({"currentTargets": ["999001"]}),
                    "2999-01-01T00:00:00+00:00",
                )

    def test_imported_task_scope_resumes_manual_campaign_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            collection = root / "collection"
            collection.mkdir()
            state_path = collection / "run-state.json"
            state_path.write_text(json.dumps({"taskScope": "manual-only"}), encoding="utf-8")
            panel = targeted_panel.Panel()
            with (
                mock.patch.object(targeted_panel, "STATE_PATH", state_path),
                mock.patch.object(targeted_panel, "COLLECTION", collection),
                mock.patch.object(targeted_panel, "LOCK_PATH", root / "index.lock"),
                mock.patch.object(targeted_panel, "active_retry_after", return_value=None),
                mock.patch.object(targeted_panel, "pid_alive", return_value=False),
                mock.patch.object(targeted_panel.subprocess, "Popen") as popen,
            ):
                popen.return_value.poll.return_value = None
                ok, _ = panel.start_resume()
            self.assertTrue(ok)
            self.assertEqual(popen.call_args.args[0][-1], "--only-manual")

    def test_panel_inferrs_manual_scope_from_saved_batch(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            collection = root / "collection"
            collection.mkdir()
            state_path = collection / "run-state.json"
            plan_path = collection / "target-plan.json"
            state_path.write_text(json.dumps({"currentTargets": ["100002"]}), encoding="utf-8")
            plan_path.write_text(json.dumps({"targets": [
                {"tournamentID": "100002", "series": "manual-review"},
            ]}), encoding="utf-8")
            panel = targeted_panel.Panel()
            with (
                mock.patch.object(targeted_panel, "STATE_PATH", state_path),
                mock.patch.object(targeted_panel, "PLAN_PATH", plan_path),
                mock.patch.object(targeted_panel, "COLLECTION", collection),
                mock.patch.object(targeted_panel, "LOCK_PATH", root / "index.lock"),
                mock.patch.object(targeted_panel, "active_retry_after", return_value=None),
                mock.patch.object(targeted_panel, "pid_alive", return_value=False),
                mock.patch.object(targeted_panel.subprocess, "Popen") as popen,
            ):
                popen.return_value.poll.return_value = None
                ok, _ = panel.start_resume()
            self.assertTrue(ok)
            self.assertEqual(popen.call_args.args[0][-1], "--only-manual")


class EventPgnSelectionTests(unittest.TestCase):
    def test_explicit_tournament_ids_do_not_expand_to_source_table(self) -> None:
        with mock.patch.object(fetch_event_pgn, "tournament_ids_from_sources", return_value=["1110353", "1111367"]):
            selected = fetch_event_pgn.selected_tournament_ids(
                pathlib.Path("ignored.csv"), "", ["tnr87435", "87436", "87435"], 0, 0,
            )
        self.assertEqual(selected, ["87435", "87436"])

    def test_event_with_explicitly_missing_pgn_links_is_not_downloaded(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            details = pathlib.Path(temp) / "details"
            details.mkdir()
            (details / "tnr999001.json").write_text(json.dumps({"rounds": [{"pairings": [{
                "white": {"playerNo": "1", "name": "White"},
                "black": {"playerNo": "2", "name": "Black"},
                "hasPGN": False, "pgnURL": "",
            }]}]}), encoding="utf-8")
            with mock.patch.object(fetch_event_pgn, "EVENT_DETAILS", details), \
                 mock.patch.object(fetch_event_pgn, "require_chess_results_publication"), \
                 mock.patch.object(fetch_event_pgn, "download_chess_results_pgn") as download:
                result = fetch_event_pgn.process_event("999001", set(), {}, pathlib.Path(temp) / "out", False, False, full_archive=True)
            self.assertEqual(result["status"], "source_unavailable")
            download.assert_not_called()

    def test_full_archive_repairs_event_with_only_player_splits(self) -> None:
        pgn = '\n'.join([
            '[Event "Repair test"]',
            '[White "Player One"]',
            '[Black "Player Two"]',
            '',
            '1. e4 *',
        ])
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            out = root / "out"
            split_dir = out / "tnr999002"
            split_dir.mkdir(parents=True)
            (split_dir / "fide-1-999002.pgn").write_text(pgn, encoding="utf-8")
            archive = root / "archive"
            with (
                mock.patch.object(fetch_event_pgn, "EVENT_PGN_ARCHIVE", archive),
                mock.patch.object(fetch_event_pgn, "require_chess_results_publication"),
                mock.patch.object(fetch_event_pgn, "download_chess_results_pgn", return_value=pgn) as download,
            ):
                result = fetch_event_pgn.process_event(
                    "999002", set(), {}, out, False, False, full_archive=True,
                )
            self.assertEqual(result["status"], "ok")
            download.assert_called_once_with("", "999002")
            self.assertTrue((archive / "tnr999002.pgn").is_file())


class SourceRetryAccountingTests(unittest.TestCase):
    def test_local_step_can_preserve_documented_partial_exit_code(self) -> None:
        completed = subprocess.CompletedProcess(["fetch"], 4)
        with mock.patch.object(sync_chess_results_event.subprocess, "run", return_value=completed):
            self.assertEqual(
                sync_chess_results_event.run_command(["fetch"], allowed_returncodes=(0, 4)),
                4,
            )

    def test_internal_retries_count_as_one_circuit_failure(self) -> None:
        error = source_http.urllib.error.URLError("synthetic network outage")
        with mock.patch.object(source_http, "require_local_collector"), \
             mock.patch.object(source_http, "_reserve_request"), \
             mock.patch.object(source_http.urllib.request, "urlopen", side_effect=error), \
             mock.patch.object(source_http, "_record_result") as record, \
             mock.patch.object(source_http.time, "sleep"):
            with self.assertRaises(source_http.SourceHTTPError) as caught:
                source_http.fetch_bytes("https://chess-results.com/tnr999001.aspx", retries=2)
        self.assertEqual(caught.exception.code, "SOURCE_NETWORK_FAILURE")
        record.assert_called_once_with("chess-results", False, force_circuit=False)


class PolicyTests(unittest.TestCase):
    def test_chess_results_is_full_data_by_default(self) -> None:
        # AGENTS.md contract: event/game completeness is the standard; cleaned
        # structured data publishes by default, raw HTML stays private.
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(source_policy.chess_results_release_policy(), "full-data")
            source_policy.require_chess_results_publication()  # must not raise
        # Legacy alias maps onto the new default.
        with mock.patch.dict(os.environ, {"CHESS_RESULTS_RELEASE_POLICY": "authorized"}, clear=True):
            self.assertEqual(source_policy.chess_results_release_policy(), "full-data")

    def test_explicit_link_only_env_blocks_publication(self) -> None:
        with mock.patch.dict(os.environ, {"CHESS_RESULTS_RELEASE_POLICY": "link-only"}, clear=True):
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

    def test_full_data_chess_results_manifest_is_accepted(self) -> None:
        payload = {
            "schemaVersion": 1,
            "source": {"source": "Chess-Results", "releasePolicy": "full-data"},
            "files": [
                {
                    "path": "data/generated/chess-results-event-details/tnr1.json",
                    "operation": "upsert", "sha256": "a" * 64, "bytes": 10,
                },
                {
                    "path": "data/generated/chess-results-event-pgn/tnr1.pgn",
                    "operation": "upsert", "sha256": "b" * 64, "bytes": 10,
                },
                {
                    "path": "docs/data/pgn/chess-results/tnr1/fide-1-1.pgn",
                    "operation": "upsert", "sha256": "c" * 64, "bytes": 10,
                },
            ],
        }
        self.assertEqual(len(run_manager.validate_manifest(payload)), 3)

    def test_chess_results_manifest_cannot_publish_outside_event_paths(self) -> None:
        payload = {
            "schemaVersion": 1,
            "source": {"source": "Chess-Results", "releasePolicy": "full-data"},
            "files": [{"path": "docs/data/registry/players.json", "operation": "upsert", "sha256": "a" * 64, "bytes": 1}],
        }
        with self.assertRaises(run_manager.RunManagerError) as caught:
            run_manager.validate_manifest(payload)
        self.assertEqual(caught.exception.code, "RELEASE_SOURCE_PATH_MISMATCH")

    def test_event_payload_merge_prefers_completeness_and_skips_unchanged(self) -> None:
        existing = {
            "tournamentID": "1", "fetchedAt": "2026-01-01T00:00:00+00:00",
            "players": [{"no": 1}], "standings": [{"rank": 1}],
            "rounds": [{"round": 1, "pairings": [{"board": 1}]}],
        }
        # Standings-only re-capture must not erase published rounds.
        fresh = {
            "tournamentID": "1", "fetchedAt": "2026-07-15T00:00:00+00:00",
            "players": [{"no": 1}], "standings": [{"rank": 1}], "rounds": [],
        }
        merged, changed = sync_chess_results_event.merge_event_payload(existing, fresh)
        self.assertEqual(merged["rounds"], existing["rounds"])
        self.assertFalse(changed)  # only volatile keys differ → skip publish
        # A real conflict is won by the freshly cleaned local data.
        fresh_conflict = {**fresh, "standings": [{"rank": 1, "score": "7"}]}
        merged, changed = sync_chess_results_event.merge_event_payload(existing, fresh_conflict)
        self.assertTrue(changed)
        self.assertEqual(merged["standings"], [{"rank": 1, "score": "7"}])
        # No published copy → always new.
        merged, changed = sync_chess_results_event.merge_event_payload(None, fresh)
        self.assertTrue(changed)
        self.assertEqual(merged, fresh)

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

    def test_prepare_release_force_adds_an_ignored_tracked_output(self) -> None:
        target = self.repo / "data/generated/chess-results-event-details/tnr1.json"
        target.parent.mkdir(parents=True)
        target.write_text('{"old":true}\n')
        (self.repo / ".gitignore").write_text("data/generated/chess-results-event-details/\n")
        git(self.repo, "add", "-f", "--", str(target.relative_to(self.repo)), ".gitignore")
        git(self.repo, "commit", "-qm", "tracked ignored output")
        allow = ["data/generated/chess-results-event-details"]
        run_manager.preflight(self.repo, self.run_dir, allow)
        target.write_text('{"fresh":true}\n')
        result = run_manager.prepare_release(self.repo, self.run_dir, "event-queue", allow)
        self.assertEqual(result["changed"], 1)
        staged = subprocess.check_output(
            ["git", "diff", "--cached", "--name-only"], cwd=self.repo, text=True
        ).splitlines()
        self.assertEqual(staged, [
            "data/generated/chess-results-event-details/tnr1.json",
            run_manager.MANIFEST_PATH,
        ])

    def test_preflight_rejects_dirty_owned_path(self) -> None:
        (self.repo / "docs/data/registry/players.json").write_text("dirty\n")
        with self.assertRaises(run_manager.RunManagerError) as caught:
            run_manager.preflight(self.repo, self.run_dir, ["docs/data/registry"])
        self.assertEqual(caught.exception.code, "DIRTY_RELEASE_PATH")

    def test_preflight_can_adopt_only_an_exact_verified_machine_output(self) -> None:
        target = self.repo / "docs/data/registry/players.json"
        target.write_text("adopted\n")
        manifest = self.repo / run_manager.MANIFEST_PATH
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text('{"stale":true}\n')
        run_manager.preflight(
            self.repo, self.run_dir, ["docs/data/registry"],
            adopt=["docs/data/registry/players.json", run_manager.MANIFEST_PATH],
        )
        result = run_manager.prepare_release(self.repo, self.run_dir, "registry", ["docs/data/registry"])
        self.assertEqual([item["path"] for item in result["files"]], ["docs/data/registry/players.json"])

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
            api_v2_root = root / "apiv2"
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
                mock.patch.object(build_api, "API_V2_ROOT", api_v2_root),
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
