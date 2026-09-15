from __future__ import annotations
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

    def test_unknown_fact_quality_fails_closed(self):
        with self.assertRaisesRegex(ValueError, 'GAME_QUALITY_MISSING'):
            q.replayable({'id':'missing'})

if __name__ == '__main__':
    unittest.main()
