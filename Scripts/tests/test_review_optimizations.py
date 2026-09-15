"""Behavioral regressions for the September review remediations."""
import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'Scripts'))
sys.path.insert(0, str(ROOT / 'Scripts/local'))
import sync_lichess_broadcast_bulk as bulk
import build_static_player_pgn as pgn
import build_person_observations as observations
import sync_domestic_players as domestic
import build_release_snapshot as snapshot
import cloudflare_ingest as shadow
from pgn_matching import GameLookup


class CoverageTests(unittest.TestCase):
    def test_registry_membership_includes_unknown_age_and_transfers(self):
        profiles = {'1': bulk.PlayerProfile(fide_id='1', federation='CHN'),
                    '2': bulk.PlayerProfile(fide_id='2', federation='SGP', birth_year=1990)}
        matches = bulk.youth_matches({'WhiteFideId': '1', 'BlackFideId': '2'}, profiles, {}, 2026)
        self.assertEqual([(r['fideID'],r['stage']) for r in matches], [('1','unknown-age'),('2','adult')])
        self.assertEqual(bulk.youth_matches({'WhiteFideId':'9','White':'Known'},profiles,{'known':'1'},2026),[])
        self.assertEqual(bulk.youth_matches({'WhiteFideId':'1','WhiteFideID':'2'},profiles,{},2026),[])
        self.assertEqual(bulk.youth_matches({'White':'Ambiguous'},profiles,{},2026),[])
        self.assertEqual(bulk.youth_matches({'WhiteFideId':'2'},profiles,{},None)[0]['stage'],'unknown-age')
        self.assertEqual(bulk.stage_for_age(5),'U6')
        for placeholder in ['', '-', '?', '0', '000']:
            self.assertEqual(bulk.youth_matches({'WhiteFideId':placeholder,'White':'Known'},profiles,{'known':'1'},2026)[0]['fideID'],'1')


    def test_lookup_preserves_unique_fallback_but_rejects_ambiguity(self):
        game = '[Event "Test"]\n[Date "2026.01.01"]\n[White "Alice Smith"]\n[Black "Bob Jones"]\n[Result "1-0"]\n\n1. e4 1-0'
        entry = dict(event='Test',date='2026-01-01',white='Alice',black='Bob',result='1-0')
        self.assertEqual(GameLookup([game],pgn).resolve(entry),(0,game))
        self.assertIsNone(GameLookup([game,game.replace('1. e4','1. d4')],pgn).resolve(entry))
        exact = {**entry,'white':'Alice Smith','black':'Bob Jones'}
        self.assertIsNone(GameLookup([game,game.replace('1. e4','1. d4')],pgn).resolve(exact))
        self.assertEqual(GameLookup([game,game],pgn).resolve(exact),(0,game))
        self.assertEqual(GameLookup([game,game.replace("1. e4","1. d4")],pgn).resolve({**exact,"gameSha256":pgn.stable_game_hash(game)}),(0,game))

    def test_ambiguous_legacy_index_does_not_erase_identified_pgn_games(self):
        import build_player_facts as facts
        game = '[Event "Test"]\n[Date "2026.01.01"]\n[White "Alice"]\n[Black "Bob"]\n[Result "1-0"]\n\n1. e4 1-0'
        second = game.replace('1. e4', '1. d4')
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            manifest = root / 'manifest.json'
            index = root / 'games.json'
            asset = root / 'games.pgn'
            manifest.write_text(json.dumps({'stages':[{'id':'adult','indexPath':'games.json','pgnPath':'games.pgn'}]}))
            index.write_text(json.dumps([dict(fideID='1', event='Test', date='2026-01-01', white='Alice', black='Bob', result='1-0')]))
            asset.write_text(game + '\n\n' + second)
            with mock.patch.object(facts, 'BULK_YOUTH_MANIFEST', manifest), mock.patch.object(facts, 'resolve_docs_data_path', side_effect=lambda name: root / name), mock.patch.object(facts, 'repo_path', side_effect=str), mock.patch.object(facts, 'public_data_path', side_effect=str):
                games = {}
                facts.ingest_bulk_youth(games, {'1': {'name':'Alice'}}, {'alice':'1'}, {})
                self.assertEqual(len(games), 2)
                self.assertTrue(all(row['playerFideIDs'] == ['1'] for row in games.values()))
                rejected = {}
                facts.add_game(rejected, game='[WhiteFideId "999"]\n'+game,
                               asset_path=asset, game_index=0, source_kind='canonical-bulk-pgn',
                               source_label='Lichess Broadcasts', registry={'1': {'name':'Alice'}},
                               names={'alice':'1'}, contexts={}, player_hint='1')
                self.assertEqual(rejected, {})


    def test_linked_observation_is_retained_without_duplicate_domestic_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);details=root/'details';details.mkdir()
            (details/'tnr12345.json').write_text(json.dumps({'tournamentID':'12345','players':[{'playerNo':'1','name':'Alice','fideID':'123'}]}))
            with mock.patch.object(observations,'DETAILS',details),mock.patch.object(observations,'COMPLETENESS',root/'missing'),mock.patch.object(observations,'load_event_dates',return_value={}),mock.patch.object(observations,'load_mappings',return_value={}):
                rows=observations.build()
            self.assertEqual(rows[0]['sighting_id'],'obs-cr-tnr12345-p1')
            self.assertEqual(rows[0]['fide_id'],'123')
            import csv
            output=root/'obs.csv'
            with output.open('w') as f:
                writer=csv.DictWriter(f,fieldnames=observations.COLUMNS);writer.writeheader();writer.writerows(rows)
            output.with_name("obs.meta.json").write_text(json.dumps({"schemaVersion":3,"sha256":hashlib.sha256(output.read_bytes()).hexdigest()}))
            domestic.validate_observation_manifest(output)
            self.assertEqual(domestic.read_sightings(output),[])


class BoundaryTests(unittest.TestCase):
    def test_event_manifest_only_promotes_published_receipted_bytes(self):
        import event_pgn_objects as events
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);source=root/'data/generated/chess-results-event-pgn';source.mkdir(parents=True)
            details=root/'docs/data/index/event-details';details.mkdir(parents=True)
            (details/'tnr12345.json').write_text('{}')
            body=b'[Event "Fixture"]\n\n1. e4 *\n';(source/'tnr12345.pgn').write_bytes(body)
            receipt=root/'data/generated/r2-object-receipts/events--chess-results.json';receipt.parent.mkdir()
            receipt.write_text(json.dumps({'objects':[{'key':'events/chess-results/tnr12345.pgn','sha256':hashlib.sha256(body).hexdigest(),'bytes':len(body)}, {'key':'events/chess-results/tnr54321.pgn','sha256':'a'*64,'bytes':1}]}))
            with mock.patch.object(events,'ROOT',root),mock.patch.object(events,'SOURCE',source),mock.patch.object(events,'OBJECT_SOURCE',root/'objects'),mock.patch.object(events,'LIBRARY_INDEX',root/'library.json'),mock.patch.object(events,'MANIFEST',root/'manifest.json'),mock.patch.object(events,'snapshot_id',return_value='test'):
                rows=events.build()
                self.assertEqual(list(rows),['12345'])
                self.assertIn('/objects/sha256/',rows['12345']['publicURL'])
                (source/'tnr12345.pgn').write_bytes(b'wrong')
                with self.assertRaisesRegex(ValueError,'EVENT_PGN_INPUT_RECEIPT_MISMATCH'):events.build()

    def test_event_library_object_hash_is_checked_before_publication(self):
        import event_pgn_objects as events
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);objects=root/'objects';objects.mkdir()
            receipt=root/'data/generated/r2-object-receipts/events--chess-results.json';receipt.parent.mkdir(parents=True)
            receipt.write_text(json.dumps({'objects':[]}))
            ident='event-'+'c'*24;body=b'[Event "Example"]\n\n1. e4 *\n'
            (objects/(ident+'.pgn')).write_bytes(body)
            library=root/'library.json';library.write_text(json.dumps({'snapshotId':'test','packages':{ident:{'fileName':ident+'.pgn','sha256':hashlib.sha256(body).hexdigest(),'bytes':len(body)}}}))
            with mock.patch.object(events,'ROOT',root),mock.patch.object(events,'SOURCE',root/'source'),mock.patch.object(events,'OBJECT_SOURCE',objects),mock.patch.object(events,'LIBRARY_INDEX',library),mock.patch.object(events,'MANIFEST',root/'manifest.json'),mock.patch.object(events,'snapshot_id',return_value='test'):
                self.assertEqual(list(events.build()),[ident])
                (objects/(ident+'.pgn')).write_bytes(b'altered')
                with self.assertRaisesRegex(ValueError,'EVENT_LIBRARY_HASH_MISMATCH'):
                    events.build()

    def test_html_surface_ignores_unlisted_experiment_files(self):
        import public_site_surface as surface
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);(root/'Scripts').mkdir();docs=root/'docs';docs.mkdir()
            (root/'Scripts/public_html_allowlist.txt').write_text('index.html\n')
            (docs/'index.html').write_text('public');(docs/'private-report.html').write_text('private')
            with mock.patch.object(surface,'ROOT',root):
                self.assertEqual(surface.public_html_paths(docs),[docs/'index.html'])

    def test_retired_clis_exit_before_creating_output(self):
        for name in ['pgn_scout','promote_public_pgn','sync_static_pgn']:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                result=subprocess.run([sys.executable,str(ROOT/'Scripts'/f'{name}.py')],cwd=directory,capture_output=True,text=True)
                self.assertNotEqual(result.returncode,0)
                self.assertIn('MIGRATION_ONLY',result.stderr)
                self.assertEqual(list(pathlib.Path(directory).iterdir()),[])

    def test_missing_builder_aborts_before_subprocess(self):
        with mock.patch.object(snapshot,'ROOT',pathlib.Path('/nonexistent')),mock.patch.object(snapshot.subprocess,'run') as run:
            with self.assertRaisesRegex(RuntimeError,'SNAPSHOT_BUILDER_MISSING'):
                snapshot.preflight_builders()
            run.assert_not_called()

    def test_shared_protocol_fixture(self):
        fixture=json.loads((ROOT/'cloudflare/ingest/test/protocol-v1.json').read_text())
        self.assertEqual(fixture['schemaVersion'],1)
        r=fixture['canonicalRequest']
        self.assertEqual(shadow.canonical_request(r['method'],r['path'],r['timestamp'],r['nonce'],r['digest']),fixture['expectedRequest'])
        raw=shadow.chunk_fingerprint_bytes(fixture['files'])
        self.assertEqual(raw.decode(),fixture['expectedChunk'])
        self.assertEqual(hashlib.sha256(raw).hexdigest(),fixture['chunkSha256'])
        limits=fixture['limits']
        self.assertEqual((shadow.MAX_RELEASE_FILES,shadow.MAX_RELEASE_BYTES,shadow.MAX_FILE_BYTES,shadow.MAX_CHUNK_FILES,shadow.MULTIPART_PART_BYTES),(limits['maxFiles'],limits['maxBytes'],limits['maxFileBytes'],limits['chunkFiles'],limits['partBytes']))

if __name__=='__main__':unittest.main()
