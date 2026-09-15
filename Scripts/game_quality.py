"""Versioned PGN quality contract, shared by every public projection.

Archive identity/counts are independent of replayability. Unfinished legal games
are replayable but never eligible for result statistics. Original bytes stay put.
"""
from __future__ import annotations
import io
from functools import lru_cache
import chess
import chess.pgn

QUALITY_VERSION = 1

class QuietGameBuilder(chess.pgn.GameBuilder):
    def handle_error(self, error):
        self.game.errors.append(error)

@lru_cache(maxsize=4096)
def _inspect(text: str) -> tuple:
    try:
        game = chess.pgn.read_game(io.StringIO(text), Visitor=QuietGameBuilder)
        if game is None:
            return 'empty', 0, '', ()
        plies = sum(1 for _ in game.mainline_moves())
        errors = tuple(str(error) for error in game.errors)
        status = 'invalid' if errors else 'legal' if plies else 'empty'
        return status, plies, game.headers.get('Result', '*'), errors
    except (ValueError, IndexError) as error:
        return 'invalid', 0, '', (str(error),)

def inspect_game(text: str) -> dict:
    status, plies, result, errors = _inspect(text)
    finished = result in {'1-0', '0-1', '1/2-1/2'}
    return {'version': QUALITY_VERSION, 'parser': 'python-chess/' + chess.__version__,
            'parseStatus': status, 'legalPlies': plies,
            'replayable': status == 'legal', 'finished': finished,
            'resultStatus': 'unverified' if finished else 'unfinished',
            'resultStatsEligible': status == 'legal' and finished,
            'errors': list(errors)}

def fact_quality(fact: dict, text: str | None = None) -> dict:
    quality = fact.get('quality')
    if quality is None and text is not None:
        return inspect_game(text)
    if not isinstance(quality, dict) or quality.get('version') != QUALITY_VERSION:
        raise ValueError('GAME_QUALITY_MISSING_OR_VERSION: ' + str(fact.get('id', '')))
    return quality

def replayable(fact: dict, text: str | None = None) -> bool:
    return fact_quality(fact, text)['replayable'] is True
