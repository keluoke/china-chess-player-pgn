"""Official-month authority, hosted URL isolation and artifact boundaries."""
import datetime as dt
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import source_policy as policy
import update_fide_monthly as fide
import monthly_release as release


class FideMonthlyTests(unittest.TestCase):
    def test_only_exact_official_downloads_are_unlocked(self):
        with mock.patch.dict(os.environ, {'GITHUB_ACTIONS':'true','CHINA_CHESS_FIDE_MONTHLY_CLOUD':'1'}, clear=True):
            for url in policy.FIDE_MONTHLY_URLS:
                policy.require_fide_monthly_download(url)
            for url in ['https://ratings.fide.com/profile/8602980',
                        fide.EXPORT+'?redirect=x', fide.EXPORT.replace('https:','http:'),
                        'https://ratings.fide.com.evil.example/download/players_list_xml_legacy.zip',
                        'https://chess-results.com/']:
                with self.assertRaises(policy.SourcePolicyError):
                    policy.require_fide_monthly_download(url)
            for provider in ['fide','chess-results','lichess']:
                with self.assertRaises(policy.SourcePolicyError):
                    policy.require_local_collector(provider)
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(policy.SourcePolicyError):
                policy.require_fide_monthly_download(fide.EXPORT)

    def test_month_is_explicit_not_capture_date(self):
        today=dt.date(2026,9,1)
        self.assertEqual(fide.list_month('<h1>Download <b>September</b> 2026 FRL</h1>',today),'2026-09')
        for text in ['Download August 2026 FRL','Download September 2025 FRL','2026-09 download']:
            with self.assertRaises(RuntimeError):fide.list_month(text,today)
        self.assertEqual(fide.list_month('Download January 2027 FRL',dt.date(2027,1,1)),'2027-01')

    def test_month_end_packaging_is_valid_but_old_zip_is_not(self):
        from types import SimpleNamespace
        fide.validate_archive_date([SimpleNamespace(date_time=(2026,8,31,12,0,0))],dt.date(2026,9,1))
        with self.assertRaisesRegex(RuntimeError,'DATE_STALE'):
            fide.validate_archive_date([SimpleNamespace(date_time=(2026,8,1,0,0,0))],dt.date(2026,9,1))

    def test_scheduled_retry_writes_a_real_boolean_output(self):
        import subprocess, textwrap
        workflow=(fide.ROOT/'.github/workflows/update-fide-ratings.yml').read_text()
        script=textwrap.dedent(workflow.split('        run: |\n',1)[1].split('      - uses:',1)[0])
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);receipt=root/fide.RECEIPT;receipt.parent.mkdir(parents=True)
            receipt.write_text(json.dumps({'listMonth':dt.datetime.now(dt.timezone.utc).strftime('%Y-%m')}))
            output=root/'output'
            subprocess.run(['bash','-c',script],cwd=root,check=True,
                           env={**os.environ,'EVENT_NAME':'schedule','GITHUB_OUTPUT':str(output)})
            self.assertEqual(output.read_text(),'skip=true\n')

    def test_artifact_never_allows_raw_exports_or_manual_inputs(self):
        for path in [fide.RECEIPT,'docs/data/registry/players.json','docs/data/registry/shards/fide-prefix-860.json',
                     'data/generated/federation-snapshots/2026-09.json']:
            self.assertTrue(fide.allowed(path))
        for path in ['data/manual/x.json','docs/data/registry/export.zip','docs/data/registry/../x.json',
                     '/docs/data/registry/players.json','docs/data/registry/shards/x.json','Scripts/x.py']:
            self.assertFalse(fide.allowed(path))
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'root';root.mkdir();artifact=Path(temporary)/'artifact'
            path=artifact/fide.RECEIPT;path.parent.mkdir(parents=True);path.write_text('{}')
            row={'path':fide.RECEIPT,'operation':'upsert','sha256':release.digest(path),'bytes':2,'baseSha256':None}
            (artifact/'release.json').write_text(json.dumps({'inputCommit':'base','files':[row]}))
            self.assertEqual(release.validate_release(artifact,'base',root=root,allowed=fide.allowed,receipt=fide.RECEIPT),[row])
            extra=artifact/'raw.zip';extra.write_bytes(b'raw')
            with self.assertRaisesRegex(RuntimeError,'UNLISTED_FILES'):
                release.validate_release(artifact,'base',root=root,allowed=fide.allowed,receipt=fide.RECEIPT)
            extra.unlink()
            path.write_text('changed')
            with self.assertRaisesRegex(RuntimeError,'HASH_MISMATCH'):
                release.validate_release(artifact,'base',root=root,allowed=fide.allowed,receipt=fide.RECEIPT)
