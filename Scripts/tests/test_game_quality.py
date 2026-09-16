from __future__ import annotations
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import game_quality as q
import build_static_player_pgn as packages
import build_completeness_report as completeness
from Scripts.tests.test_completeness_and_identity import pairing, payload_with_rounds, archive_game

HEADER = '[Event "Regression"]\n[White "Player, A"]\n[Black "Player, B"]\n[Result "*"]\n\n'

class GameQualityTest(unittest.TestCase):
    def test_illegal_san_is_not_a_truncated_playable_game(self):
        result = q.inspect_game(HEADER + '1. e4 e5 2. Kd5 *')
        self.assertFalse(result['replayable'])
        self.assertEqual(result['parseStatus'], 'invalid')
        self.assertTrue(result['errors'])

    def test_unfinished_legal_and_empty_are_distinct(self):
        legal = q.inspect_game(HEADER + '1. e4 e5 *')
        self.assertTrue(legal['replayable'])
        self.assertFalse(legal['resultStatsEligible'])
        self.assertEqual(q.inspect_game(HEADER + '*')['parseStatus'], 'empty')

    def test_verified_archive_does_not_override_bad_mainline(self):
        payload = payload_with_rounds([{'round':'1', 'pairings':[
            pairing(1, 1, 1, 'Player, A', 2, 'Player, B'),
            pairing(1, 2, 3, 'Player, C', 4, 'Player, D')]}])
        games = [archive_game(1, 1, 'Player, A', 'Player, B'), archive_game(1, 2, 'Player, C', 'Player, D')]
        games[0]['quality'] = q.inspect_game(HEADER + '1. e4 e5 *')
        games[1]['quality'] = q.inspect_game(HEADER + '1. e4 e5 2. Kd5 *')
        with mock.patch.object(completeness, 'parse_event_archive', return_value=games):
            report = completeness.event_report(payload, {}, public_archive_verified=True)
        self.assertEqual(report['archiveStatus'], 'matched-full')
        self.assertFalse(report['playableComplete'])
        self.assertEqual(report['counts']['excludedArchivedGames'], 1)
        self.assertEqual(report['counts']['playableMatchedPairings'], 1)

    def test_default_package_excludes_bad_game_but_retains_archive_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            stale = root/'pgn/by-player/fide-1001/U8.pgn'
            stale.parent.mkdir(parents=True)
            stale.write_text('stale invalid-only stage')
            bucket = packages.PlayerBucket(packages.PlayerProfile('1001', name='Player, A'))
            for i, moves in enumerate(('1. e4 e5 *', '1. e4 e5 2. Kd5 *')):
                text = HEADER + moves
                bucket.add(packages.PlayerGame(pgn=text, event='Regression', date='2026-09-16',
                    white='Player, A', black='Player, B', result='*', source='fixture',
                    sha256=str(i), stage='U8' if i else '', quality=q.inspect_game(text)))
            with mock.patch.object(packages, 'OUTPUT_INDEX_ROOT', root/'index/by-player'), \
                 mock.patch.object(packages, 'OUTPUT_BUCKET_ROOT', root/'index/by-player-buckets'), \
                 mock.patch.object(packages, 'OUTPUT_PGN_ROOT', root/'pgn/by-player'), \
                 mock.patch.object(packages, 'DOCS_DATA', root):
                packages.write_outputs({'1001':bucket}, False, {})
            detail=json.loads((root/'index/by-player/fide-1001.json').read_text())
            self.assertFalse(stale.exists())
            self.assertEqual(detail['totals']['games'],2)
            self.assertEqual(detail['totals']['playableGames'],1)
            self.assertEqual(detail['packages'][0]['gameCount'],1)
            self.assertEqual(len(detail['games']),2)
            self.assertNotIn('Kd5',(root/'pgn/by-player/fide-1001/all.pgn').read_text())

    def test_unknown_fact_quality_fails_closed(self):
        with self.assertRaisesRegex(ValueError, 'GAME_QUALITY_MISSING'):
            q.replayable({'id':'missing'})

if __name__ == '__main__':
    unittest.main()
