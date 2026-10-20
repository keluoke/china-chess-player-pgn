"""Find exact archived PGNs by game fingerprint without relying on player IDs."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import io
import re
import chess.pgn

import build_static_player_pgn as pgn_helper


def move_signature(value: str) -> str:
    """Cheap prefilter only; every surviving game is parsed and fingerprinted."""
    value = re.sub(r'^\[[^\]]*\]\s*$', '', value, flags=re.MULTILINE)
    result = []; variation = comment = 0; semicolon = False
    for char in value:
        if semicolon:
            if char == '\n': semicolon = False
            continue
        if char == ';' and not comment:
            semicolon = True; continue
        if char == '{': comment += 1; continue
        if char == '}' and comment: comment -= 1; continue
        if comment: continue
        if char == '(': variation += 1; continue
        if char == ')' and variation: variation -= 1; continue
        if not variation: result.append(char)
    value = re.sub(r'\$\d+|\d+\.(\.\.)?|\.\.\.', ' ', ''.join(result))
    value = re.sub(r'[?!+#=]', '', value)
    tokens = []
    for token in value.split():
        if token in ('*', '1-0', '0-1', '1/2-1/2', '½-½', 'e.p.'):
            continue
        token = token.replace('0', 'O')
        squares = re.findall(r'[a-h][1-8]', token)
        if squares:
            piece = token[0] if token[0] in 'KQRBN' else 'P'
            promotion = token[-1] if token[-1] in 'QRBN' else ''
            token = piece + squares[-1] + promotion
        tokens.append(token)
    return '|'.join(tokens)


def matching_games(root: Path, fingerprints: set[str], games=None) -> dict[str, dict[str, str]]:
    """Return only requested games, grouped by fingerprint and exact PGN hash.

    Player archive paths are provenance, never evidence that a PGN name has a
    verified FIDE identity. Multiple copies with the same body are collapsed;
    distinct bodies remain visible to the caller's ambiguity gate.
    """
    matches: dict[str, dict[str, str]] = defaultdict(dict)
    if not fingerprints:
        return matches
    directory = root / "docs/data/pgn/by-player"
    seen = set()
    signatures = ({move_signature(' '.join(game['movesSan'])) for game in games}
                  if games and all(game.get('movesSan') for game in games) else None)
    for path in sorted(directory.glob("fide-*/all.pgn")):
        for raw in pgn_helper.split_pgn_games(path.read_text(encoding="utf-8")):
            game_hash = pgn_helper.stable_game_hash(raw)
            if game_hash in seen:
                continue
            seen.add(game_hash)
            if signatures is not None and move_signature(raw) not in signatures:
                continue
            game = chess.pgn.read_game(io.StringIO(raw))
            if game is None or game.errors:
                continue
            # Queue fingerprints use canonical, comment-free export. Raw PGN
            # spacing/move numbers differ and must not disqualify ID-less games.
            canonical = game.accept(chess.pgn.StringExporter(headers=True, variations=False, comments=False))
            fingerprint = pgn_helper.game_fingerprint(canonical)
            if fingerprint in fingerprints:
                matches[fingerprint].setdefault(game_hash, raw)
    return matches


def choose_game(candidate: dict, matches: dict[str, str]) -> str:
    """Coalesce provider cosmetics only after proving identical full game facts."""
    def date(value):
        if not isinstance(value, str):
            return None
        parts = re.fullmatch(r'(\d{4})[.-](\d{1,2})[.-](\d{1,2})', value)
        return tuple(map(int, parts.groups())) if parts else None
    wanted_date = date(candidate.get('event', {}).get('date'))
    wanted_event = candidate.get('event', {}).get('id')
    wanted_event = str(wanted_event) if wanted_event and wanted_event != 'event-unknown' else None
    facts, choices = {}, []
    for game_hash, raw in matches.items():
        game = chess.pgn.read_game(io.StringIO(raw))
        if game is None or game.errors:
            continue
        headers = game.headers
        observed_date = date(headers.get('Date'))
        event = headers.get('TournamentID') or headers.get('EventID')
        if ((wanted_date and observed_date and wanted_date != observed_date)
                or (wanted_event and event and wanted_event != event)
                or game.board().fen() != candidate['game']['initialFen']
                or [m.uci() for m in game.mainline_moves()] != candidate['game']['movesUci']
                or headers.get('Result') != candidate['game']['result']):
            continue
        values = {'date': observed_date, 'event': event,
                  'white': headers.get('WhiteFideId'), 'black': headers.get('BlackFideId')}
        for field, value in values.items():
            if value and value not in ('?', '0'):
                facts.setdefault(field, set()).add(value)
        completeness = sum(bool(v) for v in values.values())
        choices.append((-completeness, game_hash))
    if not choices or any(len(values) > 1 for values in facts.values()):
        raise ValueError('ORIGINAL_GAME_MATCH_NOT_UNIQUE')
    return min(choices)[1]
