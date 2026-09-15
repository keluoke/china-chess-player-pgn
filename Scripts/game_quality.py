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


def coverage_contract(report: dict) -> dict:
    """One serialized coverage vocabulary for catalog, details and series UI."""
    status = report.get('pgnIngestStatus', '')
    counts = report.get('counts') or {}
    if report.get('resultsStatus') != 'results-complete' or 'unresolved' in status:
        code, label = 'unknown', '棋谱覆盖待核验'
    elif report.get('playableComplete') and status == 'full-board-complete':
        code, label = 'full', '全台棋谱可复盘'
    elif report.get('playablePublishedComplete') and status == 'source-published-complete':
        code, label = 'live', '公开直播范围可复盘完整（非全台）'
    elif counts.get('excludedArchivedGames', 0):
        code, label = 'partial', '已归档，部分棋谱无法复盘'
    elif status in {'not-published', 'not-applicable'}:
        code, label = 'none', '赛果完整 · 来源未公开棋谱'
    elif counts.get('archivedGames', 0):
        code, label = 'partial', '部分棋谱可复盘 · 覆盖待补齐'
    elif status == 'source-published-missing':
        code, label = 'missing', '公开棋谱待归档'
    else:
        code, label = 'unknown', '棋谱覆盖待核验'
    return {'version': 1, 'code': code, 'label': label}
