#!/usr/bin/env python3
"""Engine-annotate a player's games and append phase accuracy to style.json.

For every move the player made we measure centipawn loss versus the engine's
best move, bucketed by game phase. The result drives the mimic engine's
error-injection model (e.g. solid openings, shaky endgames).

  python3 analyze_phases.py --pgn all.pgn --profile profiles/fide-8640980 \
      --stockfish /tmp/stockfish --movetime 60
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re

import chess
import chess.engine
import chess.pgn

BLUNDER_CP = 200
MISTAKE_CP = 100


def normalize_name(value: str) -> str:
    return re.sub(r"[\s,.\-_']+", "", (value or "")).casefold()


def phase_of(board: chess.Board, ply: int) -> str:
    non_pawn = sum(len(board.pieces(pt, c)) * v for c in chess.COLORS
                   for pt, v in ((chess.KNIGHT, 3), (chess.BISHOP, 3), (chess.ROOK, 5), (chess.QUEEN, 9)))
    if ply <= 20:
        return "opening"
    if non_pawn <= 26:
        return "endgame"
    return "middlegame"


def score_cp(info, pov: chess.Color) -> int:
    score = info["score"].pov(pov)
    if score.is_mate():
        return 10000 if score.mate() > 0 else -10000
    return max(-10000, min(10000, score.score()))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pgn", type=pathlib.Path, required=True)
    parser.add_argument("--profile", type=pathlib.Path, required=True)
    parser.add_argument("--stockfish", default="/tmp/stockfish")
    parser.add_argument("--movetime", type=int, default=60, help="milliseconds per evaluation")
    parser.add_argument("--max-games", type=int, default=0)
    args = parser.parse_args()

    style_path = args.profile / "style.json"
    style = json.loads(style_path.read_text(encoding="utf-8"))
    names = {normalize_name(n) for n in style["player"]["names"]}
    fide_id = style["player"]["fideID"]
    limit = chess.engine.Limit(time=args.movetime / 1000)

    buckets = {p: {"moves": 0, "cplSum": 0, "blunders": 0, "mistakes": 0, "top1": 0}
               for p in ("opening", "middlegame", "endgame")}

    engine = chess.engine.SimpleEngine.popen_uci(args.stockfish)
    engine.configure({"Threads": 2, "Hash": 128})
    progress_path = args.profile / "analyze.progress"

    games_done = 0
    with args.pgn.open(encoding="utf-8", errors="replace") as handle:
        while True:
            game = chess.pgn.read_game(handle)
            if game is None:
                break
            headers = game.headers
            color = None
            for side, c in (("White", chess.WHITE), ("Black", chess.BLACK)):
                hf = re.sub(r"\D", "", headers.get(f"{side}FideId", "") or headers.get(f"{side}FideID", ""))
                if (fide_id and hf == fide_id) or normalize_name(headers.get(side, "")) in names:
                    color = c
                    break
            if color is None:
                continue

            board = game.board()
            ply = 0
            for move in game.mainline_moves():
                if board.turn == color and ply >= 4:
                    phase = phase_of(board, ply)
                    info_best = engine.analyse(board, limit)
                    best_cp = score_cp(info_best, color)
                    best_move = info_best.get("pv", [None])[0]
                    if best_move == move:
                        played_cp = best_cp
                    else:
                        board.push(move)
                        info_played = engine.analyse(board, limit)
                        played_cp = score_cp(info_played, color)
                        board.pop()
                    cpl = max(0, min(1000, best_cp - played_cp))
                    b = buckets[phase]
                    b["moves"] += 1
                    b["cplSum"] += cpl
                    b["blunders"] += 1 if cpl >= BLUNDER_CP else 0
                    b["mistakes"] += 1 if cpl >= MISTAKE_CP else 0
                    b["top1"] += 1 if best_move == move else 0
                board.push(move)
                ply += 1

            games_done += 1
            progress_path.write_text(f"{games_done} games analyzed\n")
            if args.max_games and games_done >= args.max_games:
                break

    engine.quit()

    style["engine"] = {
        "analyzer": "stockfish-16", "movetimeMs": args.movetime, "gamesAnalyzed": games_done,
        "phases": {
            name: {
                "moves": b["moves"],
                "acpl": round(b["cplSum"] / b["moves"], 1) if b["moves"] else None,
                "blunderRate": round(b["blunders"] / b["moves"], 4) if b["moves"] else None,
                "mistakeRate": round(b["mistakes"] / b["moves"], 4) if b["moves"] else None,
                "top1Rate": round(b["top1"] / b["moves"], 4) if b["moves"] else None,
            }
            for name, b in buckets.items()
        },
    }
    style_path.write_text(json.dumps(style, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    progress_path.write_text(f"DONE {games_done} games\n")
    print(json.dumps(style["engine"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
