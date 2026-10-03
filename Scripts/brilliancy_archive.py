"""Find exact archived PGNs by game fingerprint without relying on player IDs."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import build_static_player_pgn as pgn_helper


def matching_games(root: Path, fingerprints: set[str]) -> dict[str, dict[str, str]]:
    """Return only requested games, grouped by fingerprint and exact PGN hash.

    Player archive paths are provenance, never evidence that a PGN name has a
    verified FIDE identity. Multiple copies with the same body are collapsed;
    distinct bodies remain visible to the caller's ambiguity gate.
    """
    matches: dict[str, dict[str, str]] = defaultdict(dict)
    if not fingerprints:
        return matches
    directory = root / "docs/data/pgn/by-player"
    for path in sorted(directory.glob("fide-*/all.pgn")):
        for raw in pgn_helper.split_pgn_games(path.read_text(encoding="utf-8")):
            fingerprint = pgn_helper.game_fingerprint(raw)
            if fingerprint in fingerprints:
                game_hash = pgn_helper.stable_game_hash(raw)
                matches[fingerprint].setdefault(game_hash, raw)
    return matches
