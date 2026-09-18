import contextlib
import copy
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import build_seo_pages as builder
import validate_seo_pages as gate
from notify_search_engines import changed_urls

class SeoReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.docs=self.root/'docs';self.docs.mkdir()
        for name in builder.TEMPLATES+('seo.css','seo.js'):shutil.copy2(builder.ROOT/'docs'/name,self.docs/name)
        self.sid='fixture-one'; self.player={'fideID':'8602980','displayName':'侯逸凡','name':'Hou, Yifan','standard':2596,'rapid':None,'blitz':2521,'federation':'CHN'}
        self.write('data/registry/players.json',[self.player]);self.write('data/registry/manifest.json',{'listDate':'2026-09-01'})
        self.write('data/public-metrics.json',{'snapshotId':self.sid,'totals':{'playersWithGames':1,'playableUniqueGames':2}})
        event={'id':'chess-results:1234567','tournamentID':'1234567','displayName':'合成大师赛 · 公开组','date':'2026-09-01','series':'chess-association-master','detailStatus':'published','detailPath':'data/index/event-details/tnr1234567.json','gameCount':2,'pgnPath':'api/event-pgn?event=example','replayCoverage':{'label':'仅直播台次完整'}}
        self.write('data/index/public-events.json',{'snapshotId':self.sid,'events':[event]})
        self.write('data/index/event-details/tnr1234567.json',{'snapshotId':self.sid,'players':[{'playerNo':'1','fideID':'8602980','name':'错误来源姓名'}],'standings':[{'playerNo':'1','rank':1,'score':'7,5'}]})
        self.write('data/master-series-summary.json',{'snapshotId':self.sid,'years':[{'year':2026,'stations':[{'station':'合成站','groupCount':1,'groups':[{'tournamentID':'1234567','groupLabel':'公开组','playableGames':2}]}]}]})
        self.write('data/leaderboards.json',{'basisYear':2026,'groups':[{'id':'OPEN','label':'成年组','minAge':19,'maxAge':None,'rankings':{'standard':{'all':{'players':[self.player]}}}}]})
        self.write('api/v1/player-buckets/e4.json',{'snapshotId':self.sid,'players':{'8602980':{**self.player,'displayName':'错误 API 姓名','playableGameCount':2,'packages':[{'id':'all','gameCount':2,'publicURL':'https://data.chessdb.aigclabs.cc/example.pgn'}],'events':[]}}})
    def write(self,name,value):
        p=self.docs/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value,ensure_ascii=False))
    def build(self,sid=None,now='2026-09-18T00:00:00+00:00'):
        with contextlib.redirect_stdout(io.StringIO()):return builder.build(self.root,sid or self.sid,now)
    def test_registry_authority_missing_rating_and_scores_survive_projection(self):
        m=self.build();text=(self.docs/'data/seo/output/players/fide-8602980.html').read_text()
        self.assertIn('侯逸凡',text);self.assertNotIn('错误 API 姓名',text);self.assertIn('未提供',text);self.assertIn('2026-09',text)
        event=(self.docs/'data/seo/output/events/1234567.html').read_text();self.assertIn('7,5',event);self.assertIn('侯逸凡',event);self.assertNotIn('错误来源姓名',event)
        self.assertEqual(m['routes']['events']['1234567'],'/events/1234567')
    def test_mixed_snapshot_is_rejected_before_replacing_previous_output(self):
        self.build();p=self.docs/'data/seo/manifest.json';original=p.read_bytes()
        self.write('data/public-metrics.json',{'snapshotId':'wrong'})
        with self.assertRaisesRegex(ValueError,'INPUT_SNAPSHOT'):self.build()
        self.assertEqual(p.read_bytes(),original)
    def test_rebuild_preserves_semantic_lastmod_and_does_not_notify_unchanged(self):
        first=self.build()
        for name in ['data/public-metrics.json','data/index/public-events.json','data/master-series-summary.json','data/index/event-details/tnr1234567.json','api/v1/player-buckets/e4.json']:
            p=self.docs/name;d=json.loads(p.read_text());d['snapshotId']='fixture-two';p.write_text(json.dumps(d))
        second=self.build('fixture-two','2026-09-19T00:00:00+00:00')
        self.assertEqual(changed_urls(first,second),[])
        self.assertEqual([p['lastmod'] for p in first['pages']],[p['lastmod'] for p in second['pages']])
    def test_changed_rating_changes_player_page_and_notification(self):
        first=self.build();self.player['standard']=2600;self.write('data/registry/players.json',[self.player]);second=self.build(now='2026-09-19T00:00:00+00:00')
        self.assertIn(builder.ORIGIN+'/players/fide-8602980',changed_urls(first,second))
    def test_tampered_html_and_changed_templates_fail_deploy(self):
        self.build();p=self.docs/'data/seo/output/index.html';p.write_text(p.read_text()+'tampered')
        with self.assertRaisesRegex(ValueError,'HASH_MISMATCH'):gate.validate(self.root,expected_snapshot=self.sid,check_snapshot=False)
        self.build();(self.docs/'index.html').write_text('different template')
        with self.assertRaisesRegex(ValueError,'TEMPLATE_REBUILD_REQUIRED'):gate.validate(self.root,expected_snapshot=self.sid,check_snapshot=False)
    def test_unsafe_manifest_paths_are_rejected(self):
        for path in ['../index.html','/index.html','private.html','events/../../private.html']:
            with self.subTest(path=path),self.assertRaises(ValueError):gate.safe_file(path)
    def test_hostile_name_is_escaped_and_jsonld_remains_parseable(self):
        self.player['displayName']='合成</script><script>alert(1)</script>';self.write('data/registry/players.json',[self.player]);self.build()
        text=(self.docs/'data/seo/output/players/fide-8602980.html').read_text();self.assertNotIn('<script>alert(1)',text)
        d=gate.Document();d.feed(text);self.assertIn('合成',json.dumps(d.ld,ensure_ascii=False))
    def test_notifications_include_removals(self):
        self.assertEqual(changed_urls({'pages':[{'route':'/gone','contentSha256':'x'}]},{'pages':[]}),[builder.ORIGIN+'/gone'])
if __name__=='__main__':unittest.main()
