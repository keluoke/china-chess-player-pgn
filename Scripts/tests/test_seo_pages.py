import contextlib
import copy
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import subprocess
import unittest
from unittest.mock import patch
from urllib.parse import urlparse
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import verify_seo_online as online
import build_seo_pages as builder
import validate_seo_pages as gate
from notify_search_engines import changed_urls, submit_urls

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
    def test_online_canary_parses_http_body_and_rejects_wrong_sitemap(self):
        manifest=self.build()
        output=self.docs/'data/seo/output'
        def fetch(url,follow=True):
            u=urlparse(url);path=u.path;headers='';status=200;mime='text/html';body=b'home'
            if u.netloc!='chessdb.aigclabs.cc':
                return {'http_code':308,'content_type':'text/html'},b'', 'location: '+builder.ORIGIN+'/'
            if not follow and u.query:
                key='players' if 'fideID=' in u.query else 'events'
                return {'http_code':308,'content_type':'text/html'},b'', 'location: '+builder.ORIGIN+next(iter(manifest['routes'][key].values()))
            if path=='/__seo_missing_page_probe__':status=404
            elif path in ['/robots.txt','/llms.txt']:mime='text/plain'
            elif path=='/sitemap.xml':mime='application/xml';body=(output/'sitemap.xml').read_bytes()
            elif not u.query:body=(output/builder.route_file(path)).read_bytes()
            if u.query:headers='x-robots-tag: noindex, follow'
            return {'http_code':status,'content_type':mime},body,headers
        with patch.object(online,'fetch',side_effect=fetch):
            self.assertEqual(online.verify(self.root)['pages'],len(manifest['pages']))
            (output/'sitemap.xml').write_text('<urlset/>')
            with self.assertRaisesRegex(ValueError,'SITEMAP_MISMATCH'):online.verify(self.root)
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
    def test_all_registry_players_have_pages_without_api_or_games(self):
        extra={'fideID':'999999','displayName':'合成棋手','name':'Fixture Player','federation':'CHN','standard':None}
        self.write('data/registry/players.json',[self.player,extra]);m=self.build()
        self.assertEqual(len(m['routes']['players']),2)
        text=(self.docs/'data/seo/output/players/fide-999999.html').read_text()
        self.assertIn('本站暂未收录可复盘棋谱',text);self.assertIn('未提供',text)
        directory=(self.docs/'data/seo/output/players.html').read_text();self.assertIn('/players/fide-999999',directory)
        self.write('data/registry/players.json',[self.player])
        with self.assertRaisesRegex(ValueError,'REGISTRY_COVERAGE'):gate.validate(self.root,expected_snapshot=self.sid,check_snapshot=False)
    def test_name_directory_keeps_roster_rows_separate_and_rejects_unpublished(self):
        self.write('data/registry/domestic/manifest.json',{'snapshotId':self.sid})
        sightings=[{'eventID':'chess-results-tnr1234567','playerNo':'1','rank':2,'score':'7'}, {'eventID':'chess-results-tnr1234567','playerNo':'2','rank':3,'score':'6'}, {'eventID':'chess-results-tnr9999','playerNo':'3'}]
        self.write('data/registry/domestic/shards/00.json',[{'displayName':'合成姓名','sightings':sightings},{'displayName':'合成姓名','sightings':sightings[:1]}])
        m=self.build();route=m['routes']['names']['合成姓名'];text=(self.docs/'data/seo/output'/builder.route_file(route)).read_text()
        self.assertIn('2 条同名参赛记录',text);self.assertIn('不合并为个人履历',text);self.assertNotIn('9999',text)
        doc=gate.Document();doc.feed(text);self.assertNotIn('"Person"',json.dumps(doc.ld))
        self.write('data/registry/domestic/manifest.json',{'snapshotId':'wrong'})
        with self.assertRaisesRegex(ValueError,'DOMESTIC_SNAPSHOT'):self.build()
    def test_large_indexnow_submission_batches_and_retries_failed_urls_only(self):
        urls=[builder.ORIGIN+'/players/fide-'+str(i) for i in range(10001)];sizes=[]
        def send(args,**kwargs):
            payload=json.loads(Path(args[args.index('--data-binary')+1][1:]).read_text());sizes.append(len(payload['urlList']))
            return subprocess.CompletedProcess(args,0,stdout='202' if len(sizes)==1 else '500')
        with patch('notify_search_engines.subprocess.run',side_effect=send):result=submit_urls(urls,'fixturekey')
        self.assertEqual(sizes,[10000,1]);self.assertEqual(result['pendingURLs'],urls[-1:]);self.assertEqual(result['status'],'retry-needed')
    def test_dedup_retains_first_release_event_priority(self):
        original=json.loads((self.docs/'data/index/public-events.json').read_text())['events'][0]
        newer={**original,'id':'chess-results:7654321','tournamentID':'7654321','series':'other','date':'2027-01-01'}
        self.write('data/index/public-events.json',{'snapshotId':self.sid,'events':[newer,original]})
        with patch.object(builder,'EVENT_PAGE_LIMIT',1):m=self.build()
        self.assertIn('1234567',m['routes']['events'])
        self.assertNotIn('7654321',m['routes']['events'])
        original['series']='other';newer['series']='chess-association-master'
        self.write('data/index/public-events.json',{'snapshotId':self.sid,'events':[newer,original]})
        with patch.object(builder,'EVENT_PAGE_LIMIT',1):again=self.build()
        self.assertIn('1234567',again['routes']['events'])
    def test_notifications_include_removals(self):
        self.assertEqual(changed_urls({'pages':[{'route':'/gone','contentSha256':'x'}]},{'pages':[]}),[builder.ORIGIN+'/gone'])
if __name__=='__main__':unittest.main()
