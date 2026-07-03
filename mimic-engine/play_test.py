#!/usr/bin/env python3
"""Smoke-test the mimic engine: book behaviour + a short game vs Stockfish.

  python3 play_test.py --profile profiles/fide-8640980 --stockfish /tmp/stockfish
"""

from __future__ import annotations

import argparse
import pathlib
import sys

import chess
import chess.engine
import chess.pgn


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=pathlib.Path, required=True)
    parser.add_argument("--stockfish", default="/tmp/stockfish")
    parser.add_argument("--games", type=int, default=1)
    parser.add_argument("--max-plies", type=int, default=80)
    args = parser.parse_args()

    mimic_cmd = [sys.executable, str(pathlib.Path(__file__).parent / "mimic_uci.py"),
                 "--profile", str(args.profile), "--stockfish", args.stockfish]

    # 1) Book behaviour spot checks
    mimic = chess.engine.SimpleEngine.popen_uci(mimic_cmd)
    checks = [
        ("as White from startpos", chess.Board(), None),
        ("as Black vs 1.e4", chess.Board(), ["e2e4"]),
        ("as Black vs 1.e4 c5 2.Nf3", chess.Board(), ["e2e4", "c7c5", "g1f3"]),
    ]
    for label, board, setup in checks:
        b = board.copy()
        for uci in setup or []:
            b.push_uci(uci)
        result = mimic.play(b, chess.engine.Limit(time=0.2))
        print(f"[book-check] {label}: {b.san(result.move)}")

    # 2) Short game: mimic (White) vs limited Stockfish (Black)
    opponent = chess.engine.SimpleEngine.popen_uci(args.stockfish)
    opponent.configure({"UCI_LimitStrength": True, "UCI_Elo": 1700, "Threads": 1})

    for g in range(args.games):
        board = chess.Board()
        mimic_is_white = g % 2 == 0
        while not board.is_game_over() and board.ply() < args.max_plies:
            engine = mimic if (board.turn == chess.WHITE) == mimic_is_white else opponent
            result = engine.play(board, chess.engine.Limit(time=0.15))
            board.push(result.move)
        game = chess.pgn.Game.from_board(board)
        game.headers["White"] = "Mimic" if mimic_is_white else "SF-1700"
        game.headers["Black"] = "SF-1700" if mimic_is_white else "Mimic"
        game.headers["Result"] = board.result(claim_draw=True)
        print(f"\n=== game {g+1} (mimic as {'White' if mimic_is_white else 'Black'}) ===")
        print(game)

    mimic.quit()
    opponent.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
