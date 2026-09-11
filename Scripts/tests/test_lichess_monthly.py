"""Failure boundaries for the hosted Lichess maintenance transaction."""
import datetime as dt
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import source_policy
import update_lichess_monthly as monthly
import sync_lichess_broadcast_bulk as bulk


class MonthlyTests(unittest.TestCase):
    def test_cloud_capability_does_not_unlock_local_only_sources(self):
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "CHINA_CHESS_LICHESS_CLOUD": "1",
                                          "CHINA_CHESS_MAINTAINER_LOCAL": "1"}, clear=True):
            source_policy.require_local_collector("lichess")
            for provider in ("fide", "chess-results", "other"):
                with self.assertRaisesRegex(source_policy.SourcePolicyError, "CLOUD_SOURCE_FORBIDDEN"):
                    source_policy.require_local_collector(provider)
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}, clear=True):
            with self.assertRaises(source_policy.SourcePolicyError):
                source_policy.require_local_collector("lichess")

    def test_previous_month_including_year_rollover_is_required(self):
        monthly.check_months([SimpleNamespace(month="2025-12")], {}, dt.date(2026, 1, 5))
        with self.assertRaisesRegex(RuntimeError, "NOT_PUBLISHED"):
            monthly.check_months([SimpleNamespace(month="2025-11")], {}, dt.date(2026, 1, 5))
        with self.assertRaisesRegex(RuntimeError, "MONTH_GAP"):
            monthly.check_months([SimpleNamespace(month="2026-06"), SimpleNamespace(month="2026-08")], {}, dt.date(2026, 9, 5))
        with self.assertRaisesRegex(RuntimeError, "CATALOG_REGRESSION"):
            monthly.check_months([SimpleNamespace(month="2026-08")], {"shards": [{"month": "2026-07"}]}, dt.date(2026, 9, 5))

    def test_exact_artifact_rejects_raw_manual_hash_and_baseline_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, release = Path(tmp) / "repo", Path(tmp) / "release"
            root.mkdir(); release.mkdir()
            source = release / monthly.RECEIPT
            source.parent.mkdir(parents=True); source.write_text('{}\n')
            row = {"path": monthly.RECEIPT, "operation": "upsert", "sha256": monthly.digest(source),
                   "bytes": source.stat().st_size, "baseSha256": None}
            manifest = {"inputCommit": "abc", "files": [row]}
            (release / "release.json").write_text(json.dumps(manifest))
            with mock.patch.object(monthly, "ROOT", root):
                self.assertEqual(monthly.validate_release(release, "abc"), [row])
                source.write_text('tampered')
                with self.assertRaisesRegex(RuntimeError, "HASH_MISMATCH"):
                    monthly.validate_release(release, "abc")
                source.write_text('{}\n')
                current = root / monthly.RECEIPT
                current.parent.mkdir(parents=True); current.write_text('racing input')
                with self.assertRaisesRegex(RuntimeError, "BASE_CONFLICT"):
                    monthly.validate_release(release, "abc")
            for path in ('data/manual/x.json', 'docs/data/bulk/lichess-broadcast/shards/x.pgn.zst',
                         'docs/data/bulk/youth/../../x.json', 'Scripts/x.py'):
                self.assertFalse(monthly.allowed(path))

    def test_body_verification_rejects_truncated_s3_stream(self):
        client = mock.Mock()
        client.get_object.return_value = {"Body": io.BytesIO(b'abc'), "ContentLength": 4}
        with self.assertRaisesRegex(RuntimeError, "R2_BODY_TRUNCATED"):
            monthly.stream_object(client, "chess-data", "key")

    def test_stage_labels_roll_over_with_competition_year(self):
        with mock.patch.object(bulk, "COMPETITION_YEAR", 2027):
            self.assertEqual(bulk.stage_rules()["stages"][0]["birthYears"], "2019-2020")
            self.assertEqual(bulk.indexed_stage_list()[-1]["birthYears"], "2008 及更早")

    def test_workflow_separates_source_and_publication(self):
        import yaml
        workflow = yaml.safe_load((monthly.ROOT / '.github/workflows/update-lichess-broadcasts.yml').read_text())
        trigger = workflow.get('on', workflow.get(True))
        self.assertEqual(trigger['schedule'][0]['cron'], '17 3 5 * *')
        self.assertEqual(workflow['jobs']['publish']['needs'], 'collect')
        self.assertNotIn('CHINA_CHESS_MAINTAINER_LOCAL', str(workflow))
        self.assertNotIn('CHINA_CHESS_LICHESS_CLOUD', str(workflow['jobs']['publish']))
        dispatch = next(s for s in workflow['jobs']['publish']['steps'] if 'dispatch-workflow' in s.get('uses', ''))
        self.assertIn('target_sha', dispatch['with']['workflow_inputs'])

    def test_ingest_must_not_rebase_after_three_way_validation(self):
        import yaml
        workflow = yaml.safe_load((monthly.ROOT / '.github/workflows/ingest-local-data.yml').read_text())
        commit = next(s for s in workflow['jobs']['ingest']['steps'] if s.get('id') == 'commit')
        self.assertEqual(commit['env']['CI_COMMIT_REBASE_ON_CONFLICT'], 'false')

    def test_operational_contract_does_not_restore_the_blanket_cloud_ban(self):
        for name in ("AGENTS.md", "README.md", "Scripts/local/README.md", "docs/DEPLOY_UPDATE_GUIDE.md", "PRODUCT_PLAN.md"):
            text = (monthly.ROOT / name).read_text()
            self.assertNotIn("所有网络采集只", text)
            self.assertNotIn("GitHub Actions 不提供任何抓取 workflow", text)
            self.assertNotIn("所有 Chess-Results/FIDE/Lichess 来源访问", text)

    def test_repeated_event_tags_do_not_create_phantom_games(self):
        first = '[Event "Example"]\n[Event "Example"]\n[White "A"]\n[Black "B"]\n[Result "*"]\n\n*'
        second = '[Event "Next"]\n[White "C"]\n[Black "D"]\n[Result "1-0"]\n\n1. e4 1-0'
        for newline in ('\n', '\r\n'):
            body = (first + '\n\n\n' + second).replace('\n', newline).encode()
            class ShortReads(io.BytesIO):
                def read(self, size=-1):
                    return super().read(min(size, 7))
            stream = ShortReads(body)
            with mock.patch.object(bulk, '_open_zst_stream', return_value=(stream, stream, None)):
                games = list(bulk.iter_zst_pgn_games(Path('synthetic.zst')))
            import build_static_player_pgn as static_pgn
            self.assertEqual(static_pgn.split_pgn_games(body.decode()), [game.replace("\r\n", "\n") for game in games])
            self.assertEqual(len(games), 2)
            self.assertEqual(bulk.pgn_headers(games[0])['White'], 'A')
            self.assertEqual(bulk.pgn_headers(games[1])['Result'], '1-0')
