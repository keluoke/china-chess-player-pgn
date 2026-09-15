import pathlib
import sys
import unittest
from unittest import mock
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from event_identity import classify, describe
from build_event_library import build_editions, playable, validate_catalog
from sync_lichess_broadcast_bulk import compatible_target_broadcast, target_identity_compatible

class EventLibraryTests(unittest.TestCase):
    def test_series_are_distinct_and_history_is_inherited(self):
        self.assertEqual(classify('10th Eastern Asia Youth Chess Championships 2026')[0],'eastern-asian-youth')
        self.assertEqual(classify('Asian Youth Chess Championships 2024')[0],'asian-youth')
        self.assertNotEqual(classify('Asian Schools Chess Championships 2024')[0],'asian-youth')
        self.assertEqual(describe('Chinese Team Chess Championship 2027')['title'],'2027年全国国际象棋团体锦标赛')
        self.assertNotEqual(describe('2026年棋协大师赛（上海站）')['base'],describe('2026年棋协大师赛（杭州站）')['base'])

    def test_editions_deduplicate_games_but_preserve_section_links(self):
        events=[{'id':'a','name':'Chinese Youth Championships 2026 | Boys U8','date':'2026-02-21'},
                {'id':'b','name':'Chinese Youth Championships 2026 | Boys U8','date':'2026-02-22'},
                {'id':'c','name':'Chinese Youth Championships 2026 | Girls U8','date':'2026-02-22'}]
        games={'a':{'1','2'},'b':{'2','3'},'c':{'4'}}
        parents=build_editions(events,games)
        self.assertEqual(len(parents),1)
        self.assertEqual(parents[0]['gameCount'],4)
        self.assertEqual(parents[0]['sectionCount'],2)
        self.assertEqual(games['a'],{'1','2','3'})
        self.assertEqual(events[1]['sectionID'],'a')
        self.assertEqual({e['id'] for e in events},{'a','b','c'})

    def test_reviewed_parent_keeps_undated_sections_and_old_ids(self):
        events=[{'id':'master','tournamentID':'1227491','name':'2025 National Amateur Chess Ma',
                 'chineseName':'2025年全国国际象棋棋协大师赛（上海站·青浦杯）棋协大师组',
                 'station':'上海站·青浦杯','date':'2025-08-02','canonicalEventID':'shanghai-2025'},
                {'id':'candidate','tournamentID':'1227492','name':'2025年全国国际象棋棋协大师赛（上海站·青浦杯）男子候补棋协大师组',
                 'canonicalEventID':'shanghai-2025'}]
        parents=build_editions(events,{'master':{'a'},'candidate':set()})
        self.assertEqual(len(parents),1)
        self.assertEqual(parents[0]['sectionCount'],2)
        self.assertIn('青浦杯',parents[0]['displayName'])
        self.assertEqual(events[0]['editionID'],events[1]['editionID'])
        self.assertEqual(events[1]['sectionID'],'candidate')

    def test_catalog_rejects_dangling_section_link(self):
        catalog={'events':[], 'editions':[{'id':'parent','sections':['missing'],'sectionCount':1}]}
        with self.assertRaisesRegex(ValueError,'EVENT_LIBRARY_PARENT_LINK'):
            validate_catalog(catalog,{})

    def test_repeated_dates_do_not_chain_into_annual_event(self):
        events=[{'id':str(i),'name':'Recurring Open','date':date} for i,date in enumerate(['2026-01-01','2026-02-01','2026-03-01'])]
        self.assertEqual(len(build_editions(events,{e['id']:set() for e in events})),2)

    def test_playable_without_fide_id_and_unfinished_result(self):
        self.assertTrue(playable('[Event "Test"]\n[White "Local A"]\n[Black "Local B"]\n[Result "*"]\n\n1. e4 e5 *'))
        self.assertFalse(playable('[Event "Test"]\n[Result "1-0"]\n\n1-0'))
        self.assertFalse(playable('[Event "Test"]\n\n1. e5 e4 *'))

    def test_replay_only_never_fetches_source_catalog(self):
        import update_lichess_monthly as monthly
        previous={'sources':[{'id':'lichess-broadcast'}], 'shards':[
            {'month':'2026-08','url':'https://example.invalid/archive','fileName':'archive.pgn.zst',
             'sizeBytes':12,'games':2,'sha256':'a'*64}]}
        with mock.patch('sync_lichess_broadcast_bulk.fetch_broadcast_metadata', side_effect=AssertionError('source accessed')):
            shards, meta = monthly.select_shards(previous,replay_only=True)
        self.assertEqual(shards[0].sha256,'a'*64)
        self.assertEqual(shards[0].games,2)

    def test_fact_provenance_and_valid_date_survive_duplicate_sources(self):
        import build_player_facts as facts
        games={}
        game='[Event "Example"]\n[EventDate "????.??.??"]\n[Date "2026.02.22"]\n[White "Alice"]\n[Black "Bob"]\n[Result "*"]\n\n1. e4 *'
        for source in ['Static PGN','Lichess Broadcasts']:
            facts.add_game(games,game=game,asset_path=facts.ROOT/'docs/data/bulk/example.pgn',
                game_index=0,source_kind='verified-event-archive',source_label=source,
                registry={},names={},contexts={})
        self.assertEqual(len(games),1)
        fact=next(iter(games.values()))
        self.assertEqual(fact['date'],'2026-02-22')
        self.assertEqual({o['source'] for o in fact['provenance']},{'Static PGN','Lichess Broadcasts'})

    def test_pairing_identity_does_not_fall_back_past_fide_conflict(self):
        pairing={'white':{'name':'Alice','fideID':'123'}, 'black':{'name':'Bob','fideID':'456'}}
        headers={'White':'Alice','Black':'Bob','WhiteFideId':'999','BlackFideId':'456'}
        self.assertFalse(target_identity_compatible(pairing,headers))
        self.assertTrue(target_identity_compatible(pairing,{**headers,'WhiteFideId':'123'}))
        self.assertFalse(target_identity_compatible(pairing,{'White':'Bob','Black':'Alice'}))
        self.assertTrue(target_identity_compatible(pairing,{'White':'Alice','Black':'Bob'}))

    def test_domestic_broadcast_rejects_wrong_station_group_and_date(self):
        event={'series':'chess-association-master','name':'2026年全国国际象棋棋协大师赛（上海站）男子一级棋士组','date':'2026-05-10'}
        base={'BroadcastName':'2026年全国国际象棋棋协大师赛（上海站）男子一级棋士组','Date':'2026.05.09'}
        self.assertTrue(compatible_target_broadcast(event,base))
        for name in ['2026年全国国际象棋棋协大师赛（杭州站）男子一级棋士组','2026年全国国际象棋棋协大师赛（上海站）女子一级棋士组']:
            self.assertFalse(compatible_target_broadcast(event,{**base,'BroadcastName':name}))
        self.assertFalse(compatible_target_broadcast(event,{**base,'Date':'2026.07.09'}))

if __name__=='__main__':unittest.main()
