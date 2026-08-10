from __future__ import annotations

import base64
import contextlib
import gzip
import hashlib
import json
import io
import os
import pathlib
import re
import shlex
import shutil
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
import discover_player_events  # noqa: E402
import event_targeting  # noqa: E402
from sync_chinese_players import (  # noqa: E402
    RegistryPlayer,
    validate_fide_archive,
    validate_registry_population,
)


def git(repo: pathlib.Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


class CiCommitPushTests(unittest.TestCase):
    def test_force_add_publishes_an_explicit_ignored_manifest_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            repo = root / "repo"
            remote = root / "remote.git"
            repo.mkdir()
            git(repo, "init", "-q", "-b", "main")
            git(repo, "config", "user.name", "test")
            git(repo, "config", "user.email", "test@example.com")
            (repo / ".gitignore").write_text("data/generated/ignored/\n", encoding="utf-8")
            git(repo, "add", ".gitignore")
            git(repo, "commit", "-qm", "initial")
            subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
            git(repo, "remote", "add", "origin", str(remote))
            git(repo, "push", "-q", "-u", "origin", "main")

            ignored = repo / "data/generated/ignored/event.json"
            ignored.parent.mkdir(parents=True)
            ignored.write_text('{"ok":true}\n', encoding="utf-8")
            env = os.environ.copy()
            env.update({"CI_COMMIT_FORCE_ADD": "true", "PUSH_BRANCH": "main"})
            subprocess.run(
                [str(SCRIPTS / "ci_commit_push.sh"), "ingest", "data/generated/ignored/event.json"],
                cwd=repo,
                env=env,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            published = subprocess.check_output(
                ["git", "--git-dir", str(remote), "show", "main:data/generated/ignored/event.json"],
                text=True,
            )
            self.assertEqual(published, '{"ok":true}\n')

    def test_rebuild_snapshot_refuses_to_rebase_onto_newer_main(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            repo = root / "repo"
            concurrent = root / "concurrent"
            remote = root / "remote.git"
            repo.mkdir()
            git(repo, "init", "-q", "-b", "main")
            git(repo, "config", "user.name", "test")
            git(repo, "config", "user.email", "test@example.com")
            (repo / "docs/data").mkdir(parents=True)
            (repo / "docs/data/snapshot.json").write_text('{"snapshot":"old"}\n', encoding="utf-8")
            git(repo, "add", "docs/data/snapshot.json")
            git(repo, "commit", "-qm", "initial")
            subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
            git(repo, "remote", "add", "origin", str(remote))
            git(repo, "push", "-q", "-u", "origin", "main")
            subprocess.run(["git", "clone", "-q", "-b", "main", str(remote), str(concurrent)], check=True)
            git(concurrent, "config", "user.name", "other")
            git(concurrent, "config", "user.email", "other@example.com")
            (concurrent / "input.json").write_text('{"new":true}\n', encoding="utf-8")
            git(concurrent, "add", "input.json")
            git(concurrent, "commit", "-qm", "new machine input")
            git(concurrent, "push", "-q", "origin", "main")

            (repo / "docs/data/snapshot.json").write_text('{"snapshot":"stale"}\n', encoding="utf-8")
            env = os.environ.copy()
            env.update({
                "CI_COMMIT_REBASE_ON_CONFLICT": "false",
                "PUSH_BRANCH": "main",
            })
            completed = subprocess.run(
                [str(SCRIPTS / "ci_commit_push.sh"), "rebuild", "docs/data"],
                cwd=repo,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertEqual(completed.returncode, 4)
            self.assertIn("REMOTE_MOVED_DURING_BUILD", completed.stderr)
            published = subprocess.check_output(
                ["git", "--git-dir", str(remote), "show", "main:docs/data/snapshot.json"],
                text=True,
            )
            self.assertEqual(published, '{"snapshot":"old"}\n')


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
    def test_event_release_boundary_excludes_cloud_rebuilt_indexes(self) -> None:
        script = (SCRIPTS / "local" / "refresh.sh").read_text(encoding="utf-8")
        block = re.search(r"EVENT_PATHS=\((.*?)\)\n\nrun_registry", script, re.DOTALL)
        self.assertIsNotNone(block)
        for fact_root in (
            "data/generated/chess-results-event-details",
            "data/generated/chess-results-event-pgn",
            "docs/data/pgn/chess-results",
        ):
            self.assertIn(fact_root, block.group(1))
        for derived in (
            "person-observations.csv",
            "person-observations.meta.json",
            "pgn-collection-status.json",
            "event-completeness-report.json",
            "pgn-supplement-queue.json",
        ):
            self.assertNotIn(derived, block.group(1))

    def test_explicit_tournament_ids_do_not_expand_to_source_table(self) -> None:
        with mock.patch.object(fetch_event_pgn, "tournament_ids_from_sources", return_value=["1110353", "1111367"]):
            selected = fetch_event_pgn.selected_tournament_ids(
                pathlib.Path("ignored.csv"), "", ["tnr87435", "87436", "87435"], 0, 0,
            )
        self.assertEqual(selected, ["87435", "87436"])

    def test_deferred_status_rebuild_leaves_existing_index_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            status = pathlib.Path(temp) / "pgn-collection-status.json"
            status.write_text('{"sentinel":"keep"}\n', encoding="utf-8")
            argv = [
                "fetch_event_pgn.py",
                "--tournament-id", "999001",
                "--defer-status-rebuild",
            ]
            outcome = {
                "status": "ok",
                "games": 1,
                "assigned": 0,
                "unassigned": 1,
                "players": 0,
                "error": "",
            }
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(fetch_event_pgn, "COLLECTION_STATUS", status),
                mock.patch.object(fetch_event_pgn, "load_china_fide_ids", return_value=set()),
                mock.patch.object(fetch_event_pgn, "load_name_index", return_value={}),
                mock.patch.object(fetch_event_pgn, "process_event", return_value=outcome),
            ):
                self.assertEqual(fetch_event_pgn.main(), 0)
            self.assertEqual(status.read_text(encoding="utf-8"), '{"sentinel":"keep"}\n')

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

    def test_fide_pgn_links_reject_non_official_hosts(self) -> None:
        links = fetch_event_pgn.fide_pgn_links(
            '<a href="/download/event.pgn">official</a>'
            '<a href=view_pgn.php?code=abc&download=1>official unquoted</a>'
            '<a href="https://evil.example/event.pgn">wrong</a>',
            "https://ratings.fide.com/tournament_information.phtml?event=490467",
        )
        self.assertEqual(links, [
            "https://ratings.fide.com/download/event.pgn",
            "https://ratings.fide.com/view_pgn.php?code=abc&download=1",
        ])

    def test_private_fide_pgn_preserves_exact_response_and_hash(self) -> None:
        body = b'[Event "Audit"]\n\n1. e4 *\n'
        with tempfile.TemporaryDirectory() as temp:
            private_root = pathlib.Path(temp)
            fetch_event_pgn._save_private_fide_pgn(
                private_root, "490467", body, "https://ratings.fide.com/audit.pgn",
            )
            root = private_root / "raw" / "fide-events" / "event490467"
            self.assertEqual(gzip.decompress((root / "official.pgn.gz").read_bytes()), body)
            meta = json.loads((root / "pgn.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["bytes"], len(body))
            self.assertEqual(meta["sha256"], hashlib.sha256(body).hexdigest())

    def test_fide_supplement_requires_unique_pairing_and_adds_board(self) -> None:
        payload = {"rounds": [{"round": 1, "pairings": [{
            "board": "7", "result": "1 - 0",
            "white": {"playerNo": "1", "name": "Alpha, Test", "fideID": "900001"},
            "black": {"playerNo": "2", "name": "Beta, Test", "fideID": "900002"},
        }]}]}
        pgn = '\n'.join([
            '[Event "Official report"]', '[Round "1"]',
            '[White "Alpha, Test"]', '[Black "Beta, Test"]',
            '[WhiteFideId "900001"]', '[BlackFideId "900002"]', '[Result "1-0"]',
            '', '1. e4 e5 1-0',
        ])
        normalized, coverage = fetch_event_pgn.validate_fide_supplement(payload, pgn)
        self.assertIn('[Board "7"]', normalized)
        self.assertEqual(coverage, {"matched": 1, "played": 1})

    def test_fide_supplement_rejects_result_mismatch(self) -> None:
        payload = {"rounds": [{"round": 1, "pairings": [{
            "board": "7", "result": "1 - 0",
            "white": {"playerNo": "1", "name": "Alpha, Test", "fideID": "900001"},
            "black": {"playerNo": "2", "name": "Beta, Test", "fideID": "900002"},
        }]}]}
        pgn = '\n'.join([
            '[Event "Official report"]', '[Round "1"]',
            '[White "Alpha, Test"]', '[Black "Beta, Test"]', '[Result "0-1"]',
            '', '1. e4 e5 0-1',
        ])
        with self.assertRaisesRegex(ValueError, "FIDE_PGN_RESULT_MISMATCH"):
            fetch_event_pgn.validate_fide_supplement(payload, pgn)

    def test_missing_chess_results_pgn_uses_fide_event_fallback(self) -> None:
        pgn = '\n'.join([
            '[Event "Official report"]', '[Round "1"]', '[Board "1"]',
            '[White "Alpha"]', '[Black "Beta"]', '[Result "1-0"]', '', '1. e4 e5 1-0',
        ])
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            details = root / "details"
            details.mkdir()
            (details / "tnr999001.json").write_text(json.dumps({
                "fideEventID": "490467",
                "rounds": [{"round": 1, "pairings": [{
                    "board": "1", "result": "1-0", "hasPGN": False,
                    "white": {"playerNo": "1", "name": "Alpha"},
                    "black": {"playerNo": "2", "name": "Beta"},
                }]}],
            }), encoding="utf-8")
            archive = root / "archive"
            with (
                mock.patch.object(fetch_event_pgn, "EVENT_DETAILS", details),
                mock.patch.object(fetch_event_pgn, "EVENT_PGN_ARCHIVE", archive),
                mock.patch.object(fetch_event_pgn, "require_chess_results_publication"),
                mock.patch.object(fetch_event_pgn, "download_fide_event_pgn", return_value=pgn) as fide_download,
                mock.patch.object(fetch_event_pgn, "download_chess_results_pgn") as chess_results_download,
            ):
                result = fetch_event_pgn.process_event(
                    "999001", set(), {}, root / "out", False, False, full_archive=True,
                )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["archiveSource"], "fide-event-id")
            fide_download.assert_called_once_with("490467", None)
            chess_results_download.assert_not_called()
            self.assertTrue((archive / "tnr999001.pgn").is_file())

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
    def test_no_rebuild_event_flow_defers_pgn_status_index(self) -> None:
        source = (SCRIPTS / "sync_chess_results_event.py").read_text(encoding="utf-8")
        self.assertRegex(
            source,
            r"if args\.no_rebuild:\s+command\.append\(\"--defer-status-rebuild\"\)",
        )

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
        self.state_patch = mock.patch.object(
            run_manager, "local_state_root", return_value=self.root / "state"
        )
        self.state_patch.start()

    def tearDown(self) -> None:
        self.state_patch.stop()
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
        self.assertRegex(manifest["baseCommit"], r"^[0-9a-f]{40,64}$")
        self.assertRegex(manifest["files"][0]["baseBlobOid"], r"^[0-9a-f]{40,64}$")
        self.assertRegex(manifest["files"][0]["baseSha256"], r"^[0-9a-f]{64}$")

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

    def test_prepare_release_includes_ignored_untracked_machine_output(self) -> None:
        ignored_root = self.repo / "data/generated/chess-results-event-details"
        (self.repo / ".gitignore").write_text("data/generated/chess-results-event-details/\n")
        git(self.repo, "add", ".gitignore")
        git(self.repo, "commit", "-qm", "ignore collector outputs")
        allow = ["data/generated/chess-results-event-details"]
        run_manager.preflight(self.repo, self.run_dir, allow)
        ignored_root.mkdir(parents=True)
        target = ignored_root / "tnr999.json"
        target.write_text('{"tournamentID":"999"}\n')
        result = run_manager.prepare_release(self.repo, self.run_dir, "event-queue", allow)
        self.assertEqual([item["path"] for item in result["files"]], [
            "data/generated/chess-results-event-details/tnr999.json",
        ])
        staged = subprocess.check_output(
            ["git", "diff", "--cached", "--name-only"], cwd=self.repo, text=True
        ).splitlines()
        self.assertIn("data/generated/chess-results-event-details/tnr999.json", staged)

    def test_preflight_rejects_dirty_owned_path(self) -> None:
        (self.repo / "docs/data/registry/players.json").write_text("dirty\n")
        with self.assertRaises(run_manager.RunManagerError) as caught:
            run_manager.preflight(self.repo, self.run_dir, ["docs/data/registry"])
        self.assertEqual(caught.exception.code, "DIRTY_RELEASE_PATH")

    def test_prepare_release_requires_a_successful_preflight_baseline(self) -> None:
        target = self.repo / "docs/data/registry/players.json"
        target.write_text('[{"fideID":"2"}]\n')
        with self.assertRaises(run_manager.RunManagerError) as caught:
            run_manager.prepare_release(
                self.repo, self.run_dir, "registry", ["docs/data/registry"],
            )
        self.assertEqual(caught.exception.code, "RELEASE_BASELINE_MISSING")
        self.assertFalse((self.repo / run_manager.MANIFEST_PATH).exists())
        staged = subprocess.check_output(
            ["git", "diff", "--cached", "--name-only"], cwd=self.repo, text=True,
        )
        self.assertEqual(staged, "")

    def test_explicit_event_targets_are_durable_but_queue_count_is_not_a_tnr(self) -> None:
        self.assertEqual(
            run_manager.requested_event_targets([
                "999001", "tnr999002", "https://chess-results.com/tnr999003.aspx?lan=33",
                "--from-queue", "10", "999001",
            ]),
            ["999001", "999002", "999003"],
        )

    def test_preflight_can_adopt_only_an_exact_verified_machine_output(self) -> None:
        target = self.repo / "docs/data/registry/players.json"
        target.write_text('[{"fideID":"1"}]\n')
        manifest = self.repo / run_manager.MANIFEST_PATH
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text('{"stale":true}\n')
        run_manager.preflight(
            self.repo, self.run_dir, ["docs/data/registry"],
            adopt=["docs/data/registry/players.json", run_manager.MANIFEST_PATH],
        )
        result = run_manager.prepare_release(self.repo, self.run_dir, "registry", ["docs/data/registry"])
        self.assertEqual([item["path"] for item in result["files"]], ["docs/data/registry/players.json"])

    def test_outside_change_during_run_is_diagnostic_not_a_release_blocker(self) -> None:
        run_manager.preflight(self.repo, self.run_dir, ["docs/data/registry"])
        (self.repo / "README.md").write_text("concurrent\n")
        (self.repo / "docs/data/registry/players.json").write_text("changed\n")
        result = run_manager.prepare_release(
            self.repo, self.run_dir, "registry", ["docs/data/registry"],
        )
        self.assertEqual(result["changed"], 1)
        diagnostic = json.loads(
            (self.run_dir / "diagnostics/outside-worktree-changes.json").read_text(),
        )
        self.assertEqual(diagnostic["paths"], ["README.md"])

    def test_preflight_sees_ignored_orphaned_machine_outputs(self) -> None:
        ignored_root = self.repo / "data/generated/chess-results-event-details"
        (self.repo / ".gitignore").write_text("data/generated/chess-results-event-details/\n")
        git(self.repo, "add", ".gitignore")
        git(self.repo, "commit", "-qm", "ignore event details")
        ignored_root.mkdir(parents=True)
        orphan = ignored_root / "tnr999.json"
        orphan.write_text('{"tournamentID":"999"}\n')
        allow = ["data/generated/chess-results-event-details"]
        with self.assertRaises(run_manager.RunManagerError) as caught:
            run_manager.preflight(self.repo, self.run_dir, allow)
        self.assertEqual(caught.exception.code, "DIRTY_RELEASE_PATH")
        recovery = json.loads(
            (self.run_dir / "diagnostics/recovery-candidates.json").read_text(),
        )
        self.assertEqual(recovery["paths"], [str(orphan.relative_to(self.repo))])
        self.assertEqual(run_manager.recovery_candidates(self.repo, allow), recovery["paths"])

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

    def test_current_payload_recovers_dead_delivery_process(self) -> None:
        state_root = self.root / "state"
        run_dir = state_root / "runs" / "dead-delivery"
        run_dir.mkdir(parents=True)
        log_path = run_dir / "run.log"
        log_path.write_text(
            "Git 路线全部失败\n"
            "❌ GIT_PUSH_FAILED：发布包保留在 outbox。\n",
            encoding="utf-8",
        )
        run_manager.atomic_json(
            state_root / "current.json",
            {
                "runId": "dead-delivery",
                "command": "deliver",
                "status": "running",
                "pid": 99999999,
                "logPath": str(log_path),
            },
        )
        with mock.patch.object(run_manager, "process_alive", return_value=False):
            payload = run_manager.current_payload(4096)
        self.assertFalse(payload["running"])
        self.assertTrue(payload["staleState"])
        self.assertEqual(payload["status"], "finished")
        self.assertEqual(payload["result"], "failed")
        self.assertEqual(payload["errorCode"], "GIT_PUSH_FAILED")
        self.assertIn("仍在 outbox", payload["message"])

    def test_current_payload_recovers_completed_discovery_result(self) -> None:
        state_root = self.root / "state"
        run_dir = state_root / "runs" / "dead-discovery"
        run_dir.mkdir(parents=True)
        log_path = run_dir / "run.log"
        log_path.write_text("候选池已更新\n", encoding="utf-8")
        run_manager.atomic_json(
            run_dir / "result.json",
            {
                "schemaVersion": 1,
                "command": "discover-events",
                "status": "ok",
                "playersChecked": 10,
                "candidatesFound": 27,
                "candidateTNRs": ["100001"],
                "failures": [],
                "poolSize": 32,
            },
        )
        run_manager.atomic_json(
            state_root / "current.json",
            {
                "runId": "dead-discovery",
                "runDir": str(run_dir),
                "command": "discover-events",
                "status": "running",
                "pid": 99999999,
                "logPath": str(log_path),
            },
        )
        with mock.patch.object(run_manager, "process_alive", return_value=False):
            payload = run_manager.current_payload(4096)
        self.assertFalse(payload["running"])
        self.assertEqual(payload["result"], "result-preserved")
        self.assertEqual(payload["errorCode"], "FINAL_STATE_WRITE_FAILED")
        self.assertTrue(payload["resultPreserved"])
        self.assertIn("检查 10 名棋手，发现 27 个候选赛事", payload["message"])


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
        (self.run_dir / "result.json").write_text(
            '{"requested":["1"],"summary":{"complete":1}}\n',
            encoding="utf-8",
        )
        sha = self.prepare_commit()
        saved = run_manager.outbox_save(self.repo, self.run_dir, sha)
        self.assertEqual(saved["runId"], "outbox-test-run")
        entry = pathlib.Path(saved["outbox"])
        self.assertTrue((entry / "manifest.json").is_file())
        self.assertTrue((entry / "result.json").is_file())
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

    def test_api_three_way_guard_allows_baseline_or_idempotent_only(self) -> None:
        import publish_data_via_api

        self.assertFalse(publish_data_via_api.release_conflicts("base", "base", "candidate"))
        self.assertFalse(publish_data_via_api.release_conflicts("base", "candidate", "candidate"))
        self.assertTrue(publish_data_via_api.release_conflicts("base", "concurrent", "candidate"))

    def test_api_reads_remote_tree_once(self) -> None:
        import publish_data_via_api

        payload = {
            "truncated": False,
            "tree": [
                {"path": "data/a.json", "type": "blob", "sha": "a" * 40},
                {"path": "data/sub", "type": "tree", "sha": "b" * 40},
            ],
        }
        with mock.patch.object(publish_data_via_api, "api", return_value=payload) as api:
            result = publish_data_via_api.remote_tree_oids("owner/repo", "c" * 40)
        self.assertEqual(result, {"data/a.json": "a" * 40})
        api.assert_called_once()

    def test_api_blob_upload_checkpoint_is_reused(self) -> None:
        import publish_data_via_api

        content = b'{"ok":true}\n'
        oid = publish_data_via_api.git_blob_oid_for_bytes(content)
        prepared = [({
            "path": "data/generated/chess-results-event-details/tnr1.json",
            "operation": "upsert",
        }, content, oid)]
        with tempfile.TemporaryDirectory() as temp:
            entry = pathlib.Path(temp)
            with mock.patch.object(
                publish_data_via_api, "api", return_value={"sha": oid},
            ) as api:
                first = publish_data_via_api.upload_blobs("owner/repo", entry, prepared)
                second = publish_data_via_api.upload_blobs("owner/repo", entry, prepared)
            self.assertEqual(first[oid], oid)
            self.assertEqual(second[oid], oid)
            api.assert_called_once()

    def test_legacy_bundle_recovers_exact_baseline_from_base_commit(self) -> None:
        import publish_data_via_api

        with tempfile.TemporaryDirectory() as temp:
            repo = pathlib.Path(temp)
            git(repo, "init", "-q")
            git(repo, "config", "user.email", "test@example.com")
            git(repo, "config", "user.name", "Test")
            target = repo / "data/generated/chess-results-event-details/tnr1.json"
            target.parent.mkdir(parents=True)
            target.write_text('{"old":true}\n')
            git(repo, "add", ".")
            git(repo, "commit", "-qm", "baseline")
            base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
            manifest = {
                "schemaVersion": 1,
                "runId": "legacy-test",
                "command": "event-queue",
                "baseCommit": base,
                "source": {"source": "Chess-Results", "releasePolicy": "full-data"},
                "files": [{
                    "path": str(target.relative_to(repo)),
                    "operation": "upsert",
                    "sha256": hashlib.sha256(b'{"new":true}\n').hexdigest(),
                    "bytes": len(b'{"new":true}\n'),
                }],
            }
            files = run_manager.validate_manifest(manifest)
            self.assertTrue(publish_data_via_api.hydrate_legacy_baseline(repo, manifest, files))
            expected_oid = subprocess.check_output(
                ["git", "rev-parse", f"{base}:{target.relative_to(repo)}"],
                cwd=repo,
                text=True,
            ).strip()
            self.assertEqual(files[0]["baseBlobOid"], expected_oid)
            self.assertEqual(files[0]["baseSha256"], hashlib.sha256(b'{"old":true}\n').hexdigest())

    def test_legacy_bundle_without_local_base_fails_closed(self) -> None:
        import publish_data_via_api

        manifest = {
            "schemaVersion": 1,
            "runId": "legacy-test",
            "command": "event-queue",
            "baseCommit": "a" * 40,
            "source": {"source": "Chess-Results", "releasePolicy": "full-data"},
            "files": [{
                "path": "data/generated/chess-results-event-details/tnr1.json",
                "operation": "upsert",
                "sha256": hashlib.sha256(b"new").hexdigest(),
                "bytes": 3,
            }],
        }
        files = run_manager.validate_manifest(manifest)
        with tempfile.TemporaryDirectory() as temp, self.assertRaises(SystemExit) as caught:
            publish_data_via_api.hydrate_legacy_baseline(pathlib.Path(temp), manifest, files)
        self.assertIn("API_DELIVERY_BASELINE_MISSING", str(caught.exception))


REFRESH_SH = LOCAL / "refresh.sh"


class EventQueueEntrypointTests(unittest.TestCase):
    def test_dirty_preflight_never_collects_commits_or_creates_outbox(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            repo = root / "repo"
            local = repo / "Scripts/local"
            local.mkdir(parents=True)
            shutil.copy2(REFRESH_SH, local / "refresh.sh")
            shutil.copy2(LOCAL / "run_manager.py", local / "run_manager.py")
            shutil.copy2(SCRIPTS / "source_policy.py", repo / "Scripts/source_policy.py")
            receipt = repo / "data/generated/r2-object-receipts/events--chess-results.json"
            receipt.parent.mkdir(parents=True)
            receipt.write_text('{"verifiedAt":"base"}\n', encoding="utf-8")
            git(repo, "init", "-q", "-b", "main")
            git(repo, "config", "user.name", "test")
            git(repo, "config", "user.email", "test@example.com")
            git(repo, "add", ".")
            git(repo, "commit", "-qm", "base")
            receipt.write_text('{"verifiedAt":"orphaned"}\n', encoding="utf-8")

            state_root = root / "state"
            process = subprocess.run(
                [
                    "bash", str(local / "refresh.sh"), "event-queue", "--no-push", "--",
                    "--from-queue", "10",
                ],
                cwd=repo,
                env={
                    **os.environ,
                    "CHINA_CHESS_LOCAL_ROOT": str(state_root),
                    "CHINA_CHESS_DISABLE_NOTIFICATIONS": "1",
                },
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertNotEqual(process.returncode, 0)
            runs = sorted((state_root / "runs").iterdir())
            self.assertEqual(len(runs), 1)
            state = json.loads((runs[0] / "run.json").read_text(encoding="utf-8"))
            error = json.loads((runs[0] / "error.json").read_text(encoding="utf-8"))
            self.assertEqual(state["errorCode"], "DIRTY_RELEASE_PATH")
            self.assertEqual(state["requestArguments"], ["--from-queue", "10"])
            self.assertEqual(state["requested"], [])
            self.assertEqual(error["code"], "DIRTY_RELEASE_PATH")
            self.assertFalse((runs[0] / "release-manifest.json").exists())
            self.assertFalse((state_root / "outbox").exists())
            self.assertEqual(
                subprocess.check_output(
                    ["git", "rev-list", "--count", "HEAD"], cwd=repo, text=True,
                ).strip(),
                "1",
            )
            self.assertEqual(receipt.read_text(encoding="utf-8"), '{"verifiedAt":"orphaned"}\n')
            self.assertNotIn("CHESS_RESULTS_COLLECTION_FAILED", process.stderr + process.stdout)


class DiscoverEventsEntrypointTests(unittest.TestCase):
    def python_wrapper(self, root: pathlib.Path) -> pathlib.Path:
        wrapper = root / "bin" / "python3"
        wrapper.parent.mkdir()
        real_python = shlex.quote(sys.executable)
        wrapper.write_text(
            "#!/bin/sh\n"
            f"real_python={real_python}\n"
            '[ "$1" = "-u" ] && shift\n'
            'script="$1"\n'
            'if [ "${script##*/}" = "discover_player_events.py" ]; then\n'
            "  shift\n"
            '  private_root=""\n'
            '  while [ "$#" -gt 0 ]; do\n'
            '    if [ "$1" = "--private-root" ]; then private_root="$2"; break; fi\n'
            "    shift\n"
            "  done\n"
            '  "$real_python" -c \'import json,pathlib,sys; p=pathlib.Path(sys.argv[1]); '
            'p.mkdir(parents=True,exist_ok=True); '
            'p.joinpath("result.json").write_text(json.dumps({'
            '"schemaVersion":1,"command":"discover-events","status":"ok",'
            '"playersChecked":1,"candidatesFound":2,"candidateTNRs":["100001","100002"],'
            '"failures":[],"poolSize":2,"completedAt":"2026-07-31T00:00:00+00:00"}),'
            'encoding="utf-8")\' "$private_root"\n'
            '  printf \'{"playersChecked":1,"candidatesFound":2,"failures":[]}\\n\'\n'
            "  exit 0\n"
            "fi\n"
            'if [ "${script##*/}" = "run_manager.py" ] && [ "$2" = "finish" ] '
            '&& [ "${FAIL_FINAL_STATE:-}" = "1" ]; then\n'
            '  echo "simulated final-state write failure" >&2\n'
            "  exit 73\n"
            "fi\n"
            'exec "$real_python" -u "$@"\n',
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        return wrapper

    def run_discovery(self, root: pathlib.Path, *, fail_final_state: bool = False) -> subprocess.CompletedProcess:
        wrapper = self.python_wrapper(root)
        state_root = root / "state"
        env = {
            **os.environ,
            "PATH": f"{wrapper.parent}:{os.environ['PATH']}",
            "CHINA_CHESS_LOCAL_ROOT": str(state_root),
            "FAIL_FINAL_STATE": "1" if fail_final_state else "0",
        }
        return subprocess.run(
            ["bash", str(REFRESH_SH), "discover-events", "--", "8600000"],
            cwd=SCRIPTS.parent,
            env=env,
            capture_output=True,
            text=True,
        )

    def test_refresh_discovery_finishes_with_durable_ok_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            completed = self.run_discovery(root)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            current = json.loads((root / "state/current.json").read_text(encoding="utf-8"))
            self.assertEqual(current["status"], "finished")
            self.assertEqual(current["result"], "ok")
            self.assertFalse((root / "state/active.lock").exists())

    def test_refresh_discovery_retries_final_state_and_reports_preserved_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            completed = self.run_discovery(root, fail_final_state=True)
            self.assertEqual(completed.returncode, 5)
            state_root = root / "state"
            current = json.loads((state_root / "current.json").read_text(encoding="utf-8"))
            run_dir = pathlib.Path(current["runDir"])
            diagnostics = (run_dir / "diagnostics/final-state-error.log").read_text(encoding="utf-8")
            self.assertEqual(diagnostics.count("simulated final-state write failure"), 3)
            with (
                mock.patch.object(run_manager, "local_state_root", return_value=state_root),
                mock.patch.object(run_manager, "process_alive", return_value=False),
            ):
                payload = run_manager.current_payload(4096)
            self.assertEqual(payload["result"], "result-preserved")
            self.assertEqual(payload["errorCode"], "FINAL_STATE_WRITE_FAILED")
            self.assertIn("无需重新查询来源", payload["message"])
            self.assertIn("FINAL_STATE_WRITE_FAILED", (run_dir / "run.log").read_text(encoding="utf-8"))


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

    def test_macos_system_proxy_uses_supported_scutil_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stub = pathlib.Path(temp) / "scutil"
            stub.write_text(
                "#!/bin/sh\n"
                "[ \"$1\" = \"--proxy\" ] || exit 9\n"
                "printf '%s\\n' '  HTTPSEnable : 1' '  HTTPSProxy : 127.0.0.1' '  HTTPSPort : 15236'\n"
            )
            stub.chmod(0o755)
            script = (
                f'eval "$(sed -n "/^system_proxy_candidates()/,/^}}/p" "{REFRESH_SH}")"; '
                "system_proxy_candidates"
            )
            result = subprocess.run(
                ["bash", "-c", script],
                capture_output=True,
                text=True,
                env={**os.environ, "PATH": f"{temp}:{os.environ['PATH']}"},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "http://127.0.0.1:15236")

    def test_delivery_prefers_api_and_collection_keeps_pending_bundle(self) -> None:
        source = REFRESH_SH.read_text(encoding="utf-8")
        block = re.search(r"deliver_outbox\(\) \{(.*?)\n\}", source, re.DOTALL)
        self.assertIsNotNone(block)
        self.assertLess(
            block.group(1).index('api_deliver "$run_id"'),
            block.group(1).index('push_commit_with_routes "$sha" "$run_id"'),
        )
        self.assertIn('API_DELIVERY_CLASS" = "transport"', block.group(1))
        self.assertIn("GIT_FALLBACK_BASE_MISMATCH", source)
        self.assertNotIn("check_receipts.py", block.group(1))
        commit_block = re.search(r"commit_prepared_release\(\) \{(.*?)\n\}", source, re.DOTALL)
        self.assertIsNotNone(commit_block)
        self.assertIn("DELIVERY_PENDING=true", commit_block.group(1))
        self.assertNotIn("deliver_outbox || return 1", commit_block.group(1))


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
                mock.patch.object(sync_chess_results_event, "DISCOVERY_POOL", root / "pool.json"),
                mock.patch.object(local_panel, "DISCOVERY_POOL", root / "pool.json"),
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
                mock.patch.object(local_panel, "DISCOVERY_POOL", root / "pool.json"),
            ):
                entries = local_panel.recent_payload()["entries"]
            self.assertEqual(entries[0]["tournamentID"], "999888")
            self.assertFalse(entries[0]["inQueue"])
            self.assertEqual(entries[0]["status"], "complete")

    def test_events_join_date_name_completeness_and_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            state_root = root / "state"
            capture_state = state_root / "chess-results/capture-state.json"
            capture_state.parent.mkdir(parents=True)
            capture_state.write_text(json.dumps({"events": {
                "100001": {
                    "status": "complete", "players": 8, "rounds": 7,
                    "standings": 8, "updatedAt": "2026-07-28T01:00:00Z",
                },
            }}))
            tournaments = root / "tournaments.json"
            tournaments.write_text(json.dumps([{
                "tournamentID": "100001", "name": "示例赛事", "date": "2026-07-20",
            }]))
            completeness = root / "completeness.json"
            completeness.write_text(json.dumps({"events": [{
                "tournamentID": "100001", "resultsStatus": "results-complete",
                "pgnAvailability": "not-published", "archiveStatus": "missing",
            }]}))
            queue = root / "queue.json"
            queue.write_text('{"targets":[]}')
            outbox = state_root / "outbox/release-1"
            outbox.mkdir(parents=True)
            (outbox / "manifest.json").write_text(json.dumps({
                "command": "event-queue",
                "files": [{
                    "path": "data/generated/chess-results-event-details/tnr100001.json",
                }],
            }))
            (outbox / "delivery.json").write_text(json.dumps({
                "runId": "release-1", "status": "online-verified",
                "createdAt": "2026-07-28T02:00:00Z",
            }))
            with (
                mock.patch.object(local_panel, "STATE_ROOT", state_root),
                mock.patch.object(local_panel, "CAPTURE_STATE_PATH", capture_state),
                mock.patch.object(local_panel, "TOURNAMENTS_PATH", tournaments),
                mock.patch.object(local_panel, "COMPLETENESS_PATH", completeness),
                mock.patch.object(local_panel, "QUEUE_PATH", queue),
                mock.patch.object(local_panel, "EVENT_DETAIL_ROOT", root / "details"),
                mock.patch.object(run_manager, "local_state_root", return_value=state_root),
            ):
                payload = local_panel.events_payload()
            event = payload["entries"][0]
            self.assertEqual(event["name"], "示例赛事")
            self.assertEqual(event["date"], "2026-07-20")
            self.assertEqual(event["resultsStatus"], "results-complete")
            self.assertEqual(event["publication"]["status"], "online-verified")


class PanelBatchResultTests(unittest.TestCase):
    def test_preflight_failure_retains_requested_targets_without_result_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            run_dir = root / "runs" / "preflight-failed"
            run_dir.mkdir(parents=True)
            state = {
                "runDir": str(run_dir),
                "runId": "preflight-failed",
                "command": "event-queue",
                "result": "failed",
                "stage": "preflight",
                "errorCode": "DIRTY_RELEASE_PATH",
                "requested": ["100001", "100002"],
                "requestArguments": ["100001", "100002"],
            }
            with (
                mock.patch.object(local_panel, "STATE_ROOT", root),
                mock.patch.object(local_panel, "durable_state", return_value=state),
            ):
                payload = local_panel.result_payload()
            self.assertEqual(payload["requested"], ["100001", "100002"])
            self.assertEqual(payload["targets"], {})
            self.assertEqual(payload["stage"], "preflight")
            self.assertEqual(payload["publication"]["status"], "no-release")
            self.assertIn("r.result==='failed'?'失败'", local_panel.PAGE)

    def test_result_joins_every_target_with_exact_release_and_delivery_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            run_dir = root / "runs" / "batch-1"
            outbox = root / "outbox" / "batch-1"
            run_dir.mkdir(parents=True)
            outbox.mkdir(parents=True)
            (run_dir / "result.json").write_text(json.dumps({
                "requested": ["100001", "100002"],
                "targets": {
                    "100001": {"status": "complete", "players": 20},
                    "100002": {
                        "status": "partial", "players": 30,
                        "errorCode": "ROUND_COUNT_UNKNOWN",
                    },
                },
                "summary": {"complete": 1, "partial": 1},
            }), encoding="utf-8")
            (run_dir / "release-manifest.json").write_text(json.dumps({"files": [
                {
                    "path": "data/generated/chess-results-event-details/tnr100001.json",
                    "operation": "upsert", "bytes": 1200,
                },
                {
                    "path": "data/generated/chess-results-event-details/tnr100002.json",
                    "operation": "upsert", "bytes": 2300,
                },
                {
                    "path": "docs/data/pgn/chess-results/fide-123.pgn",
                    "operation": "delete", "bytes": 0,
                },
            ]}), encoding="utf-8")
            (outbox / "delivery.json").write_text(json.dumps({
                "status": "ingested-to-main",
                "route": "api",
                "remoteSHA": "a" * 40,
            }), encoding="utf-8")
            state = {
                "runDir": str(run_dir),
                "runId": "batch-1",
                "command": "event-queue",
                "result": "partial",
                "errorCode": "PARTIAL_FAILURE",
            }
            with (
                mock.patch.object(local_panel, "STATE_ROOT", root),
                mock.patch.object(local_panel, "durable_state", return_value=state),
            ):
                payload = local_panel.result_payload()
            self.assertEqual(payload["summary"], {"complete": 1, "partial": 1})
            self.assertEqual(payload["publication"]["changedFiles"], 3)
            self.assertEqual(payload["publication"]["upserts"], 2)
            self.assertEqual(payload["publication"]["deletes"], 1)
            self.assertEqual(payload["publication"]["changedBytes"], 3500)
            self.assertEqual(payload["publication"]["unattributedFiles"], 1)
            self.assertTrue(payload["publication"]["delivered"])
            self.assertFalse(payload["publication"]["onlineVerified"])
            self.assertEqual(payload["targets"]["100001"]["releaseFiles"], 1)
            self.assertEqual(payload["targets"]["100002"]["releaseBytes"], 2300)

    def test_latest_event_result_survives_a_later_receipt_check(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            event_run = root / "runs" / "20260728-event"
            receipt_run = root / "runs" / "20260728-receipt"
            event_run.mkdir(parents=True)
            receipt_run.mkdir(parents=True)
            event_state = {
                "runId": "20260728-event",
                "runDir": str(event_run),
                "command": "event-queue",
                "result": "partial",
                "errorCode": "PARTIAL_FAILURE",
            }
            (event_run / "run.json").write_text(json.dumps(event_state), encoding="utf-8")
            (event_run / "result.json").write_text(json.dumps({
                "requested": ["100001"],
                "targets": {"100001": {"status": "partial"}},
                "summary": {"partial": 1},
            }), encoding="utf-8")
            (receipt_run / "run.json").write_text(json.dumps({
                "runId": "20260728-receipt",
                "runDir": str(receipt_run),
                "command": "receipts",
                "result": "ok",
            }), encoding="utf-8")
            with (
                mock.patch.object(local_panel, "STATE_ROOT", root),
                mock.patch.object(local_panel, "durable_state", return_value={
                    "runId": "20260728-receipt",
                    "runDir": str(receipt_run),
                    "command": "receipts",
                    "result": "ok",
                }),
            ):
                payload = local_panel.result_payload()
            self.assertEqual(payload["runId"], "20260728-event")
            self.assertEqual(payload["summary"], {"partial": 1})
            self.assertFalse(payload["running"])

    def test_event_result_survives_run_pruning_in_outbox(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            outbox = root / "outbox" / "event-release"
            outbox.mkdir(parents=True)
            (outbox / "manifest.json").write_text(json.dumps({
                "command": "event-queue",
                "files": [],
            }))
            (outbox / "result.json").write_text(json.dumps({
                "requested": ["100001"],
                "targets": {"100001": {"status": "complete"}},
                "summary": {"complete": 1},
            }))
            (outbox / "delivery.json").write_text(json.dumps({
                "runId": "event-release",
                "status": "online-verified",
                "createdAt": "2026-07-28T00:00:00Z",
            }))
            with (
                mock.patch.object(local_panel, "STATE_ROOT", root),
                mock.patch.object(local_panel, "durable_state", return_value={
                    "command": "receipts", "result": "ok",
                }),
                mock.patch.object(run_manager, "local_state_root", return_value=root),
            ):
                payload = local_panel.result_payload()
            self.assertEqual(payload["runId"], "event-release")
            self.assertEqual(payload["summary"], {"complete": 1})

    def test_partial_batch_is_a_warning_outcome_not_a_failed_release_claim(self) -> None:
        refresh = (LOCAL / "refresh.sh").read_text(encoding="utf-8")
        self.assertIn('result="partial"', refresh)
        self.assertIn('partial "PARTIAL_FAILURE"', refresh)
        self.assertIn("更新数据文件", local_panel.PAGE)
        self.assertIn("部分完成 / 失败", local_panel.PAGE)
        self.assertNotIn("部分赛事目标失败已隔离；成功赛事已发布", refresh)

    def test_panel_disconnect_stops_running_indicator(self) -> None:
        self.assertIn("dot.className='dot bad'", local_panel.PAGE)
        self.assertIn("这不代表任务仍在运行", local_panel.PAGE)
        self.assertIn("wasRunning=false", local_panel.PAGE)

    def test_panel_automation_never_schedules_source_capture(self) -> None:
        source = (LOCAL / "panel.py").read_text(encoding="utf-8")
        start = source.index("def automation_monitor()")
        end = source.index("\ndef stop_job", start)
        monitor = source[start:end]
        self.assertIn('"deliver" if pending else "receipts"', monitor)
        self.assertNotIn("event-queue", monitor)
        self.assertIn("自动推进发布", local_panel.PAGE)

    def test_panel_automation_quarantines_online_hash_mismatch(self) -> None:
        with (
            mock.patch.object(local_panel, "_read_json_file", return_value={"enabled": True}),
            mock.patch.object(local_panel, "outbox_entries", return_value=[{
                "runId": "stale-online",
                "status": "deployed",
                "receipts": {
                    "online": {
                        "ok": False,
                        "expected": "old",
                        "actual": "new",
                    },
                },
            }]),
        ):
            payload = local_panel.automation_payload()
        self.assertEqual(payload["advancing"], 0)
        self.assertEqual(payload["attention"][0]["errorCode"], "ONLINE_HASH_MISMATCH")


class ReceiptAdvanceTests(unittest.TestCase):
    def test_online_hash_mismatch_is_durable_and_non_retriable(self) -> None:
        import check_receipts

        self.assertEqual(
            check_receipts.online_error_code({
                "online": {"ok": False, "expected": "old", "actual": "new"},
            }),
            "ONLINE_HASH_MISMATCH",
        )
        self.assertIsNone(check_receipts.online_error_code({
            "online": {"ok": False, "error": "temporary network error"},
        }))

    def test_child_rebuild_can_start_before_ingest_finishes(self) -> None:
        import check_receipts

        ingest_started = check_receipts.parse_time("2026-07-27T02:28:11Z")
        rebuild = {
            "status": "completed",
            "conclusion": "success",
            "created_at": "2026-07-27T02:28:23Z",
        }
        self.assertIs(
            check_receipts.successful_run_after([rebuild], ingest_started),
            rebuild,
        )
        self.assertIsNone(
            check_receipts.successful_run_after(
                [rebuild],
                check_receipts.parse_time("2026-07-27T02:28:27Z"),
            )
        )

    def test_online_fetch_uses_system_curl_when_available(self) -> None:
        import check_receipts

        completed = mock.Mock(returncode=0, stdout=b"online bytes", stderr=b"")
        with (
            mock.patch.object(check_receipts.shutil, "which", return_value="/usr/bin/curl"),
            mock.patch.object(check_receipts.subprocess, "run", return_value=completed) as run,
            mock.patch.object(check_receipts.urllib.request, "urlopen") as urlopen,
        ):
            self.assertEqual(
                check_receipts.fetch_online_bytes("https://example.test/data.json"),
                b"online bytes",
            )
        self.assertIn("--fail", run.call_args.args[0])
        urlopen.assert_not_called()

    def test_non_public_release_verifies_the_deployed_snapshot(self) -> None:
        import check_receipts

        snapshot = json.dumps({
            "snapshotId": "snap-verified",
            "inputCommit": "a" * 40,
        }).encode()
        proof = {"ok": True, "releaseRunId": "run-verified", "inputCommit": "a" * 40}
        with mock.patch.object(check_receipts, "fetch_online_bytes", return_value=snapshot):
            result = check_receipts.verify_online_file(
                {"files": [{"path": "data/generated/event.json", "operation": "upsert"}]},
                fallback=("docs/data/snapshot.json", snapshot),
                release_input_proof=proof,
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["verification"], "deployed-snapshot")
        self.assertTrue(result["releaseInputProof"]["ok"])
        self.assertTrue(result["url"].endswith("/data/snapshot.json"))

    def test_event_pgn_release_verifies_the_r2_receipt_object(self) -> None:
        import check_receipts

        body = b'[Event "FIDE Event Report"]\n\n1. e4 e5 *\n'
        digest = hashlib.sha256(body).hexdigest()
        manifest = {"files": [{
            "path": "data/generated/chess-results-event-pgn/tnr1469438.pgn",
            "operation": "upsert",
            "sha256": digest,
        }]}
        with tempfile.TemporaryDirectory() as tmp:
            bundle_root = pathlib.Path(tmp)
            receipt_path = (
                bundle_root / "files" / "data" / "generated" /
                "r2-object-receipts" / "events--chess-results.json"
            )
            receipt_path.parent.mkdir(parents=True)
            receipt_path.write_text(json.dumps({"objects": [{
                "key": "events/chess-results/tnr1469438.pgn",
                "sha256": digest,
                "publicURL": "https://data.example/events/chess-results/tnr1469438.pgn",
            }]}), encoding="utf-8")
            with mock.patch.object(check_receipts, "fetch_online_bytes", return_value=body):
                result = check_receipts.verify_online_file(
                    manifest, bundle_root=bundle_root
                )
        self.assertTrue(result["ok"])
        self.assertEqual(result["verification"], "r2-event-object")
        self.assertEqual(result["actual"], digest)

    def test_pages_trimmed_pgn_uses_snapshot_fallback(self) -> None:
        import check_receipts

        snapshot = b'{"snapshotId":"snap-pgn-trimmed"}\n'
        with mock.patch.object(check_receipts, "fetch_online_bytes", return_value=snapshot):
            result = check_receipts.verify_online_file(
                {"files": [{
                    "path": "docs/data/pgn/chess-results/fide-1-2.pgn",
                    "operation": "upsert",
                    "sha256": "0" * 64,
                }]},
                fallback=("docs/data/snapshot.json", snapshot),
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["verification"], "deployed-snapshot")

    def test_non_public_release_rejects_unreachable_snapshot_input(self) -> None:
        import check_receipts

        snapshot = json.dumps({
            "snapshotId": "stale-snapshot",
            "inputCommit": "a" * 40,
        }).encode()
        proof = {"ok": False, "error": "release is not reachable from snapshot input"}
        with mock.patch.object(check_receipts, "fetch_online_bytes", return_value=snapshot):
            result = check_receipts.verify_online_file(
                {"files": [{"path": "data/generated/event.json", "operation": "upsert"}]},
                fallback=("docs/data/snapshot.json", snapshot),
                release_input_proof=proof,
            )
        self.assertFalse(result["ok"])
        self.assertFalse(result["releaseInputProof"]["ok"])
        self.assertIn("not reachable", result["error"])

    def test_release_reachability_is_anchored_at_snapshot_input_commit(self) -> None:
        import check_receipts

        input_commit = "a" * 40
        ingest_commit = "b" * 40
        snapshot = json.dumps({"inputCommit": input_commit}).encode()
        commits = [{"sha": ingest_commit, "commit": {"message": "Ingest validated local release"}}]
        release_manifest = json.dumps({"runId": "run-123"}).encode()
        with (
            mock.patch.object(check_receipts, "gh_api", return_value=commits) as api,
            mock.patch.object(check_receipts, "remote_file_bytes", return_value=release_manifest),
        ):
            result = check_receipts.verify_release_reachable_from_snapshot(
                "owner/repo", snapshot, "run-123"
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["ingestCommit"], ingest_commit)
        self.assertIn(f"sha={input_commit}", api.call_args.args[0])

    def test_release_reachability_rejects_missing_input_commit(self) -> None:
        import check_receipts

        result = check_receipts.verify_release_reachable_from_snapshot(
            "owner/repo", b'{"snapshotId":"old"}', "run-123"
        )
        self.assertFalse(result["ok"])
        self.assertIn("input commit", result["error"])

    def test_remote_file_bytes_decodes_github_content(self) -> None:
        import check_receipts

        expected = b'{"snapshotId":"snap-verified"}\n'
        payload = {"encoding": "base64", "content": base64.b64encode(expected).decode("ascii")}
        with mock.patch.object(check_receipts, "gh_api", return_value=payload) as api:
            actual = check_receipts.remote_file_bytes("owner/repo", "abc123", "docs/data/snapshot.json")
        self.assertEqual(actual, expected)
        self.assertIn("ref=abc123", api.call_args.args[0])

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

    def test_ingest_uses_blobless_sparse_checkout(self) -> None:
        workflow = (SCRIPTS.parent / ".github" / "workflows" / "ingest-local-data.yml").read_text(encoding="utf-8")
        self.assertIn("fetch-depth: 1", workflow)
        self.assertNotIn("fetch-depth: 0", workflow)
        self.assertIn("!/docs/data/", workflow)
        self.assertIn("!/data/generated/", workflow)
        self.assertIn("!/docs/api/", workflow)

    def test_ingest_dispatches_exact_main_sha_to_rebuild(self) -> None:
        workflow = (SCRIPTS.parent / ".github" / "workflows" / "ingest-local-data.yml").read_text(encoding="utf-8")
        dispatch = (SCRIPTS.parent / ".github" / "actions" / "dispatch-workflow" / "action.yml").read_text(encoding="utf-8")
        self.assertIn('echo "main_sha=$(git rev-parse HEAD)"', workflow)
        self.assertIn('"target_sha":"${{ steps.commit.outputs.main_sha }}"', workflow)
        self.assertIn("workflow_inputs", dispatch)
        self.assertIn('"inputs":inputs', dispatch)

    def test_rebuild_pins_target_sha_and_refuses_stale_rebase(self) -> None:
        workflow = (SCRIPTS.parent / ".github" / "workflows" / "rebuild-indexes.yml").read_text(encoding="utf-8")
        self.assertIn("target_sha:", workflow)
        self.assertIn("ref: ${{ inputs.target_sha || github.sha }}", workflow)
        self.assertIn("REBUILD_BASE_MISMATCH", workflow)
        self.assertIn('CI_COMMIT_REBASE_ON_CONFLICT: "false"', workflow)


class SparseIngestApplyTests(unittest.TestCase):
    def test_partial_clone_prefetch_batches_exact_manifest_blob_oids(self) -> None:
        upserts = [
            {"path": "docs/data/registry/a.json", "operation": "upsert"},
            {"path": "docs/data/registry/b.json", "operation": "upsert"},
        ]
        configured = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"remote.origin.promisor true\n", stderr=b""
        )
        tree = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                b"100644 blob 1111111111111111111111111111111111111111\tdocs/data/registry/a.json\0"
                b"100644 blob 2222222222222222222222222222222222222222\tdocs/data/registry/b.json\0"
            ),
            stderr=b"",
        )
        fetched = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")
        with (
            mock.patch.object(run_manager, "git", side_effect=[configured, tree]),
            mock.patch.object(run_manager.subprocess, "run", return_value=fetched) as fetch_run,
        ):
            run_manager.prefetch_partial_clone_blobs(pathlib.Path("/tmp/repo"), "release", upserts)

        command = fetch_run.call_args.args[0]
        self.assertIn("fetch.negotiationAlgorithm=noop", command)
        self.assertIn("--stdin", command)
        self.assertEqual(
            fetch_run.call_args.kwargs["input"],
            b"1111111111111111111111111111111111111111\n"
            b"2222222222222222222222222222222222222222\n",
        )

    def test_partial_clone_prefetch_retries_then_falls_back_to_lazy_checkout(self) -> None:
        upserts = [{"path": "docs/data/registry/a.json", "operation": "upsert"}]
        configured = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"remote.origin.promisor true\n", stderr=b""
        )
        tree = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=b"100644 blob 1111111111111111111111111111111111111111\tdocs/data/registry/a.json\0",
            stderr=b"",
        )
        failed = subprocess.CompletedProcess(args=[], returncode=128, stdout=b"", stderr=b"connection reset")
        warning = io.StringIO()
        with (
            mock.patch.object(run_manager, "git", side_effect=[configured, tree]),
            mock.patch.object(run_manager.subprocess, "run", return_value=failed) as fetch_run,
            mock.patch.object(run_manager.time, "sleep") as sleep,
            mock.patch("sys.stderr", warning),
        ):
            run_manager.prefetch_partial_clone_blobs(pathlib.Path("/tmp/repo"), "release", upserts)

        self.assertEqual(fetch_run.call_count, run_manager.PREFETCH_ATTEMPTS)
        self.assertEqual(sleep.call_count, run_manager.PREFETCH_ATTEMPTS - 1)
        self.assertIn("falling back to checkout lazy-fetch", warning.getvalue())

    def test_apply_materializes_upsert_and_stages_delete_outside_sparse_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = pathlib.Path(temp) / "repo"
            repo.mkdir()
            git(repo, "init", "-q")
            git(repo, "config", "user.email", "test@example.com")
            git(repo, "config", "user.name", "Test")
            details = repo / "data/generated/chess-results-event-details"
            details.mkdir(parents=True)
            upsert = details / "tnr1.json"
            deleted = details / "tnr2.json"
            upsert.write_text('{"version":1}\n')
            deleted.write_text('{"delete":true}\n')
            (repo / "README.md").write_text("test\n")
            git(repo, "add", ".")
            git(repo, "commit", "-qm", "main")
            main_sha = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=repo, text=True
            ).strip()

            upsert.write_text('{"version":2}\n')
            deleted.unlink()
            digest = hashlib.sha256(upsert.read_bytes()).hexdigest()
            manifest = {
                "schemaVersion": 1,
                "runId": "sparse-apply-test",
                "source": {"source": "Chess-Results", "releasePolicy": "full-data"},
                "files": [
                    {
                        "path": str(upsert.relative_to(repo)),
                        "operation": "upsert",
                        "sha256": digest,
                        "bytes": upsert.stat().st_size,
                    },
                    {
                        "path": str(deleted.relative_to(repo)),
                        "operation": "delete",
                        "sha256": None,
                        "bytes": 0,
                    },
                ],
            }
            manifest_path = repo / run_manager.MANIFEST_PATH
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(json.dumps(manifest) + "\n")
            git(repo, "add", "-A")
            git(repo, "commit", "-qm", "release")
            release_sha = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=repo, text=True
            ).strip()

            git(repo, "checkout", "-q", main_sha)
            git(repo, "sparse-checkout", "set", "--no-cone", "/*", "!/data/generated/")
            self.assertFalse(upsert.exists())
            self.assertFalse(deleted.exists())

            path_list = repo / "applied-paths.txt"
            result = run_manager.apply_release(repo, release_sha, path_list)
            self.assertEqual(result["applied"], 2)
            self.assertEqual(upsert.read_text(), '{"version":2}\n')
            self.assertFalse(deleted.exists())
            applied = path_list.read_text().splitlines()
            stageable = []
            for relative in applied:
                candidate = repo / relative
                tracked_result = subprocess.run(
                    ["git", "ls-files", "--error-unmatch", "--", relative],
                    cwd=repo,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if candidate.exists() or candidate.is_symlink() or tracked_result.returncode == 0:
                    stageable.append(relative)
            add_result = subprocess.run(
                ["git", "add", "--sparse", "-f", "-A", "--", *stageable],
                cwd=repo,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(add_result.returncode, 0, add_result.stderr)
            staged = subprocess.check_output(
                ["git", "diff", "--cached", "--name-status"], cwd=repo, text=True
            )
            self.assertIn("M\tdata/generated/chess-results-event-details/tnr1.json", staged)
            self.assertIn("D\tdata/generated/chess-results-event-details/tnr2.json", staged)
            self.assertIn(f"A\t{run_manager.MANIFEST_PATH}", staged)

    def test_apply_rejects_concurrent_main_change_before_worktree_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = pathlib.Path(temp) / "repo"
            repo.mkdir()
            git(repo, "init", "-q")
            git(repo, "config", "user.email", "test@example.com")
            git(repo, "config", "user.name", "Test")
            target = repo / "docs/data/registry/players.json"
            target.parent.mkdir(parents=True)
            target.write_text('{"version":1}\n')
            git(repo, "add", ".")
            git(repo, "commit", "-qm", "base")
            base_commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=repo, text=True
            ).strip()
            base_oid, base_sha256 = run_manager.git_blob_facts(
                repo, base_commit, "docs/data/registry/players.json"
            )

            target.write_text('{"version":2}\n')
            candidate_sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
            manifest = {
                "schemaVersion": 1,
                "runId": "baseline-conflict",
                "baseCommit": base_commit,
                "source": {
                    "source": "FIDE Rating List",
                    "releasePolicy": "factual-registry-projection",
                },
                "files": [{
                    "path": "docs/data/registry/players.json",
                    "operation": "upsert",
                    "sha256": candidate_sha256,
                    "bytes": target.stat().st_size,
                    "baseBlobOid": base_oid,
                    "baseSha256": base_sha256,
                }],
            }
            manifest_path = repo / run_manager.MANIFEST_PATH
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(json.dumps(manifest) + "\n")
            git(repo, "add", ".")
            git(repo, "commit", "-qm", "release candidate")
            release_commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=repo, text=True
            ).strip()

            git(repo, "checkout", "-q", base_commit)
            target.write_text('{"version":3-concurrent}\n')
            git(repo, "add", ".")
            git(repo, "commit", "-qm", "concurrent main change")
            before = target.read_bytes()
            with self.assertRaises(run_manager.RunManagerError) as caught:
                run_manager.apply_release(repo, release_commit, None)
            self.assertEqual(caught.exception.code, "RELEASE_BASE_CONFLICT")
            self.assertEqual(target.read_bytes(), before)
            self.assertEqual(
                subprocess.check_output(
                    ["git", "status", "--porcelain"], cwd=repo, text=True
                ),
                "",
            )


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

    def test_provider_throttle_sleeps_after_releasing_shared_ledger(self) -> None:
        events = []
        ledger = {
            "schemaVersion": 1,
            "providers": {
                "fide": {
                    # datetime.date.today() observes the patched epoch below.
                    "date": "1970-01-01",
                    "requests": 1,
                    "consecutiveFailures": 0,
                    "lastRequestAt": 99.0,
                    "circuitOpenUntil": 0.0,
                },
            },
        }

        @contextlib.contextmanager
        def locked():
            events.append("lock-enter")
            yield pathlib.Path("unused"), ledger
            events.append("lock-exit")

        with (
            mock.patch.object(source_http, "_locked_ledger", side_effect=locked),
            mock.patch.object(source_http.time, "time", return_value=100.0),
            mock.patch.object(source_http.time, "sleep", side_effect=lambda _wait: events.append("sleep")),
        ):
            source_http._reserve_request("fide")
        self.assertEqual(events, ["lock-enter", "lock-exit", "sleep"])
        self.assertEqual(ledger["providers"]["fide"]["lastRequestAt"], 101.0)


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
                mock.patch.object(sync_chess_results_event, "DISCOVERY_POOL", root / "pool.json"),
            ):
                self.assertEqual(sync_chess_results_event.queue_targets(10, 30), ["234567"])

    def test_player_search_discovery_merges_recent_tnrs_in_private_pool(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            pool_path = root / "event-discovery-pool.json"
            state_path = root / "player-state.json"

            def fake_search(fide_id: str, sink: list[str] | None) -> list[dict]:
                if sink is not None:
                    sink.append(f"<html>private {fide_id}</html>")
                return [
                    {"tnrid": "888888", "tournament": "Recent Event", "end_date": "2026-07-20", "rounds": "9", "participants": "80"},
                    {"tnrid": "1375162", "tournament": "Aggregate", "end_date": "2026-07-19", "rounds": "", "participants": "758"},
                ]

            result = discover_player_events.discover(
                ["8600000", "8600001"],
                private_root=root / "run",
                latest_per_player=5,
                delay=0,
                search=fake_search,
                pool_path=pool_path,
                state_path=state_path,
            )
            pool = json.loads(pool_path.read_text(encoding="utf-8"))
            self.assertEqual(result["candidateTNRs"], ["888888"])
            self.assertEqual(pool["candidates"]["888888"]["fideIDs"], ["8600000", "8600001"])
            self.assertEqual(pool["candidates"]["1375162"]["status"], "suppressed")
            self.assertTrue((root / "run/raw/chess-results/player-search/fide8600000.html.gz").exists())

    def test_discovery_entrypoint_persists_machine_readable_result_before_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = pathlib.Path(temp) / "run"
            expected = {
                "playersChecked": 1,
                "candidatesFound": 2,
                "candidateTNRs": ["100001", "100002"],
                "failures": [],
                "poolSize": 2,
            }
            argv = [
                "discover_player_events.py",
                "8600000",
                "--private-root",
                str(run_dir),
                "--delay",
                "0",
            ]
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(discover_player_events, "discover", return_value=expected.copy()),
            ):
                self.assertEqual(discover_player_events.main(), 0)
            result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(result["command"], "discover-events")
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["candidatesFound"], 2)
            self.assertTrue(result["completedAt"])

    def test_reviewed_monitor_state_wins_over_private_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            queue_path = root / "queue.json"
            pool_path = root / "pool.json"
            queue_path.write_text(json.dumps({"targets": [{
                "tournamentID": "888888", "eventName": "Reviewed", "nextAction": "monitor",
                "priorityScore": 10,
            }]}), encoding="utf-8")
            pool_path.write_text(json.dumps({"candidates": {"888888": {
                "tournamentID": "888888", "eventName": "Raw", "nextAction": "capture-event",
                "priorityScore": 210, "status": "pending", "discoveredBy": ["fide-player-search"],
            }}}), encoding="utf-8")
            target = event_targeting.merged_target_items(
                queue_path=queue_path, pool_path=pool_path,
            )[0]
            self.assertEqual(target["eventName"], "Reviewed")
            self.assertEqual(target["nextAction"], "monitor")
            self.assertIn("fide-player-search", target["discoveredBy"])

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
                mock.patch.object(local_panel, "DISCOVERY_POOL", root / "missing-pool.json"),
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
