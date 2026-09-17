"""Offline regressions for duplicate capture, stale evidence and PGN-only work."""
from __future__ import annotations
import datetime as dt
import gzip
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock
from Scripts.tests.test_chess_results_parser import sce, FakeFetcher

class CacheFreshnessTests(unittest.TestCase):
    def test_valid_gzip_with_modified_body_is_not_trusted(self):
        with tempfile.TemporaryDirectory() as name:
            root = pathlib.Path(name)
            store = sce.PageStore(root, [])
            store.save('100001', 'standings', 'url', 'original')
            (root/'tnr100001/standings.html.gz').write_bytes(gzip.compress(b'changed'))
            self.assertIsNone(store.load('100001', 'standings'))
            with self.assertRaisesRegex(sce.EventCaptureError, '本地缓存哈希'):
                sce.PageStore(root, [], offline=True).load('100001', 'standings')

    def test_copy_does_not_reset_age_and_only_expired_pages_miss(self):
        with tempfile.TemporaryDirectory() as name:
            root = pathlib.Path(name); old = root/'old'; new = root/'new'
            stamp = (dt.datetime.now(dt.timezone.utc)-dt.timedelta(hours=7)).isoformat()
            for kind in ['starting-rank', 'round-2']:
                sce.PageStore(old, []).save('100001', kind, 'url', 'body', request_url='url', fetched_at=stamp)
            store = sce.PageStore(new, [old], max_age_seconds=86400, failed_page='round-2')
            self.assertIsNotNone(store.load('100001', 'starting-rank', 'url'))
            self.assertIsNone(store.load('100001', 'round-2', 'url'))
            self.assertEqual(json.loads((new/'tnr100001/pages.json').read_text())['starting-rank']['fetchedAt'], stamp)
            self.assertIsNotNone(sce.PageStore(None,[old],offline=True,max_age_seconds=0).load('100001','round-2'))

    def test_final_historical_events_skip_until_explicit_refresh_or_parser_change(self):
        entry = {'status':'complete','dateEnd':'2020-01-01','capturedAt':'2020-01-02', 'parserVersion':sce.PARSER_VERSION}
        self.assertEqual(sce.should_skip_target(entry,30),'historical-complete')
        entry['parserVersion']='old'
        self.assertEqual(sce.should_skip_target(entry,30),'')
        entry.update(parserVersion=sce.PARSER_VERSION,dateEnd='2999-01-01')
        self.assertEqual(sce.cache_max_age(entry),86400)

class CaptureModeTests(unittest.TestCase):
    def invoke(self, root, fetcher, run, extra=()):
        argv=['capture','999001','--publish','--no-players','--no-rebuild','--delay','0','--private-root',str(root/run),*extra]
        with (mock.patch.object(sce,'CAPTURE_STATE',root/'capture-state.json'),
              mock.patch.object(sce,'EVENT_QUEUE',root/'queue.json'),
              mock.patch.object(sce,'PUBLIC_OUTPUT',root/'public'),
              mock.patch.object(sce,'fetch_page_body',fetcher),
              mock.patch.object(sce,'require_chess_results_publication'),
              mock.patch.object(sce,'source_explicitly_omits_pgn',return_value=False),
              mock.patch.object(sce,'run_command',return_value=0) as post,
              mock.patch.object(sys,'argv',argv)):
            result=sce.main()
        return result, post

    def test_duplicate_does_not_fetch_and_preserves_raw_pointer(self):
        with tempfile.TemporaryDirectory() as name:
            root=pathlib.Path(name); fetcher=FakeFetcher()
            self.assertEqual(self.invoke(root,fetcher,'one')[0],0)
            count=sum(fetcher.calls.values())
            code,post=self.invoke(root,fetcher,'two')
            self.assertEqual(code,0); self.assertEqual(sum(fetcher.calls.values()),count); post.assert_not_called()
            entry=json.loads((root/'capture-state.json').read_text())['events']['999001']
            self.assertEqual(entry['runPrivateRoot'],str((root/'one').resolve()))
            # A later replay can still find the original evidence, and cannot run PGN I/O.
            with mock.patch.object(sce,'fetch_page_body',side_effect=AssertionError('network')):
                code,post=self.invoke(root,lambda *a,**k: self.fail('network'),'three',['--replay','--overwrite'])
            self.assertEqual(code,0); post.assert_not_called()

    def test_check_updates_fetches_but_pgn_only_uses_complete_results(self):
        with tempfile.TemporaryDirectory() as name:
            root=pathlib.Path(name); fetcher=FakeFetcher()
            self.assertEqual(self.invoke(root,fetcher,'one')[0],0)
            count=sum(fetcher.calls.values())
            self.assertEqual(self.invoke(root,fetcher,'two',['--check-updates'])[0],0)
            self.assertGreater(sum(fetcher.calls.values()),count)
            code,post=self.invoke(root,lambda *a,**k: self.fail('details fetched'),'three',['--pgn-only'])
            self.assertEqual(code,0)
            self.assertEqual(post.call_count,1)
            self.assertIn('Scripts/fetch_event_pgn.py',post.call_args.args[0])

    def test_parser_upgrade_replays_without_source_or_pgn(self):
        with tempfile.TemporaryDirectory() as name:
            root=pathlib.Path(name); fetcher=FakeFetcher()
            self.invoke(root,fetcher,'one')
            with mock.patch.object(sce,'PARSER_VERSION','new-version'):
                code,post=self.invoke(root,lambda *a,**k: self.fail('network'),'two')
            self.assertEqual(code,0); post.assert_not_called()

    def test_pgn_failure_retry_does_not_recapture_event_details(self):
        with tempfile.TemporaryDirectory() as name:
            root=pathlib.Path(name)
            self.invoke(root,FakeFetcher(),'one')
            state=root/'capture-state.json'
            payload=json.loads(state.read_text())
            payload['events']['999001'].update(status='retry-wait',errorCode='PGN_COLLECTION_INCOMPLETE')
            state.write_text(json.dumps(payload))
            code,post=self.invoke(root,lambda *a,**k: self.fail('details fetched'),'two')
            self.assertEqual(code,0)
            self.assertEqual(post.call_count,1)
            self.assertIn('Scripts/fetch_event_pgn.py',post.call_args.args[0])

    def test_pgn_only_without_results_fails_before_network(self):
        with tempfile.TemporaryDirectory() as name:
            code,post=self.invoke(pathlib.Path(name),lambda *a,**k: self.fail('network'),'one',['--pgn-only'])
            self.assertEqual(code,1); post.assert_not_called()

if __name__=='__main__': unittest.main()
