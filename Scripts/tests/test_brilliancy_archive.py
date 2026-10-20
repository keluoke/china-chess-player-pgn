"""Archive matching cannot depend on raw PGN typography or FIDE metadata."""
import io
from pathlib import Path
import sys
import tempfile
import unittest
import chess.pgn

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'Scripts'))
from brilliancy_archive import matching_games, choose_game
from build_static_player_pgn import game_fingerprint, stable_game_hash


class BrilliancyArchiveTests(unittest.TestCase):
    def test_canonical_moves_match_idless_game_with_different_typography(self):
        raw='[Event "Synthetic"]\n[White "Alpha"]\n[Black "Beta"]\n[Result "*"]\n\n1.e4 {synthetic comment} (1.d4 d5) e5 2.Nf3 Nc6 *'
        game=chess.pgn.read_game(io.StringIO(raw))
        canonical=game.accept(chess.pgn.StringExporter(headers=True,variations=False,comments=False))
        fingerprint=game_fingerprint(canonical)
        self.assertNotEqual(game_fingerprint(raw),fingerprint)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'docs/data/pgn/by-player/fide-1/all.pgn'
            path.parent.mkdir(parents=True);path.write_text(raw)
            result=matching_games(root,{fingerprint})
        self.assertEqual(result,{fingerprint:{stable_game_hash(raw):raw}})
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'docs/data/pgn/by-player/fide-1/all.pgn'
            path.parent.mkdir(parents=True);path.write_text(raw)
            result=matching_games(root,{fingerprint},[{'movesSan':['e4','e5','Nf3','Nc6']}])
        self.assertIn(stable_game_hash(raw),result[fingerprint])

    def test_cosmetic_provider_copies_coalesce_but_different_dates_do_not(self):
        raw='[Event "Synthetic"]\n[Date "2026.01.01"]\n[White "Alpha"]\n[Black "Beta"]\n[Result "*"]\n\n1. e4 e5 *'
        other=raw.replace('Synthetic','Translated synthetic')
        board=chess.Board()
        candidate={'game':{'initialFen':board.fen(),'movesUci':['e2e4','e7e5'],'result':'*'},'event':{}}
        matches={stable_game_hash(x):x for x in (raw,other)}
        chosen=choose_game(candidate,matches)
        self.assertEqual(chosen,choose_game(candidate,dict(reversed(list(matches.items())))))
        different=other.replace('2026.01.01','2026.01.02')
        with self.assertRaisesRegex(ValueError,'NOT_UNIQUE'):
            choose_game(candidate,{stable_game_hash(x):x for x in (raw,different)})


if __name__=='__main__':unittest.main()
