#!/usr/bin/env python3
"""A UCI engine that imitates a specific player.

Move selection:
  1. If the position is in the player's polyglot book (their own games),
     sample a book move weighted by observed frequency (temperature control).
  2. Otherwise ask a strength-limited backend (Stockfish UCI_Elo) for
     multiPV candidates, re-rank them with the player's style vector
     (capture / check / castling / queen-trade tendencies), then sample.
  3. Inject errors per game phase according to the player's measured
     blunder profile (analyze_phases.py), so e.g. endgames wobble the way
     the real player's endgames wobble.

The Maia integration point is `backend_candidates()`: swap the Stockfish
multiPV call for an lc0+maia policy query and everything else stays.

Run:  python3 mimic_uci.py --profile profiles/fide-8640980 --stockfish /tmp/stockfish
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import random
import sys

import chess
import chess.engine
import chess.polyglot

DEFAULT_PHASE_ERRORS = {  # fallback when style.json lacks engine stats
    "opening": {"blunderRate": 0.02, "mistakeRate": 0.08},
    "middlegame": {"blunderRate": 0.05, "mistakeRate": 0.14},
    "endgame": {"blunderRate": 0.08, "mistakeRate": 0.18},
}


class MimicEngine:
    def __init__(self, profile_dir: pathlib.Path, stockfish_path: str):
        self.profile_dir = profile_dir
        self.style = json.loads((profile_dir / "style.json").read_text(encoding="utf-8"))
        self.books = {
            chess.WHITE: profile_dir / "white.bin",
            chess.BLACK: profile_dir / "black.bin",
        }
        self.stockfish_path = stockfish_path
        self.backend: chess.engine.SimpleEngine | None = None
        self.board = chess.Board()
        self.book_temp = 0.7
        self.elo = 2050
        self.multipv = 4
        self.rng = random.Random()

    # ---- lifecycle -------------------------------------------------------
    def ensure_backend(self) -> None:
        if self.backend is None:
            self.backend = chess.engine.SimpleEngine.popen_uci(self.stockfish_path)
            self.backend.configure({"Threads": 1, "Hash": 64})

    def close(self) -> None:
        if self.backend is not None:
            self.backend.quit()
            self.backend = None

    # ---- book ------------------------------------------------------------
    def book_move(self) -> chess.Move | None:
        path = self.books[self.board.turn]
        if not path.exists():
            return None
        try:
            with chess.polyglot.open_reader(path) as reader:
                entries = list(reader.find_all(self.board))
        except (OSError, ValueError):
            return None
        if not entries:
            return None
        temp = max(0.1, self.book_temp)
        weights = [max(1, e.weight) ** (1.0 / temp) for e in entries]
        move = self.rng.choices([e.move for e in entries], weights=weights, k=1)[0]
        return move if move in self.board.legal_moves else None

    # ---- style -----------------------------------------------------------
    def phase(self) -> str:
        non_pawn = sum(len(self.board.pieces(pt, c)) * v for c in chess.COLORS
                       for pt, v in ((chess.KNIGHT, 3), (chess.BISHOP, 3), (chess.ROOK, 5), (chess.QUEEN, 9)))
        if self.board.ply() <= 20:
            return "opening"
        if non_pawn <= 26:
            return "endgame"
        return "middlegame"

    def phase_errors(self) -> dict:
        engine_stats = (self.style.get("engine") or {}).get("phases") or {}
        stats = engine_stats.get(self.phase()) or {}
        fallback = DEFAULT_PHASE_ERRORS[self.phase()]
        return {
            "blunderRate": stats.get("blunderRate") if stats.get("blunderRate") is not None else fallback["blunderRate"],
            "mistakeRate": stats.get("mistakeRate") if stats.get("mistakeRate") is not None else fallback["mistakeRate"],
        }

    def style_bonus_cp(self, move: chess.Move) -> float:
        s = self.style.get("style", {})
        bonus = 0.0
        capture_delta = s.get("captureRate", 0.2) - s.get("captureRateFaced", 0.2)
        if self.board.is_capture(move):
            bonus += 500 * capture_delta  # trade-happy players get a capture nudge
        board_after = self.board.copy(stack=False)
        board_after.push(move)
        if board_after.is_check():
            bonus += 400 * (s.get("checkRate", 0.04) - 0.04)
        if self.board.is_castling(move):
            castles = s.get("castling", {})
            total = sum(castles.values()) or 1
            queenside_share = castles.get("O-O-O", 0) / total
            if chess.square_file(move.to_square) == 2 and queenside_share > 0.15:
                bonus += 25 * queenside_share  # comfortable castling long
        if s.get("queenTradeGameShare", 0) >= 0.6 and self.board.is_capture(move):
            captured = self.board.piece_at(move.to_square)
            mover = self.board.piece_at(move.from_square)
            if captured and mover and captured.piece_type == chess.QUEEN and mover.piece_type == chess.QUEEN:
                bonus += 12  # willing to simplify into long games
        return bonus

    # ---- backend selection -------------------------------------------------
    def backend_candidates(self, movetime: float) -> list[tuple[chess.Move, int]]:
        """Candidate moves with cp scores. Maia plug-in point: replace this
        with an lc0/maia policy query returning (move, prob→pseudo-cp)."""
        self.ensure_backend()
        self.backend.configure({"UCI_LimitStrength": True, "UCI_Elo": max(1320, min(3190, self.elo))})
        infos = self.backend.analyse(self.board, chess.engine.Limit(time=movetime), multipv=self.multipv)
        candidates = []
        for info in infos:
            pv = info.get("pv")
            if not pv:
                continue
            score = info["score"].pov(self.board.turn)
            cp = 10000 if score.is_mate() and score.mate() > 0 else (-10000 if score.is_mate() else score.score())
            candidates.append((pv[0], cp))
        return candidates

    def pick_move(self, movetime: float) -> chess.Move:
        move = self.book_move()
        if move is not None:
            print("info string book move", file=sys.stderr, flush=True)
            return move

        candidates = self.backend_candidates(movetime)
        if not candidates:
            return next(iter(self.board.legal_moves))

        errors = self.phase_errors()
        roll = self.rng.random()
        if len(candidates) > 1 and roll < errors["blunderRate"]:
            weights = [0.0] + [1.0 / i for i in range(1, len(candidates))]
            return self.rng.choices([c[0] for c in candidates], weights=weights, k=1)[0]

        adjusted = [(mv, cp + self.style_bonus_cp(mv)) for mv, cp in candidates]
        top = max(cp for _, cp in adjusted)
        softmax_t = 45.0 if roll < errors["mistakeRate"] else 18.0
        weights = [math.exp((cp - top) / softmax_t) for _, cp in adjusted]
        return self.rng.choices([mv for mv, _ in adjusted], weights=weights, k=1)[0]

    # ---- UCI loop ----------------------------------------------------------
    def run(self) -> None:
        player = self.style.get("player", {})
        name = (player.get("names") or ["player"])[0]
        for line in sys.stdin:
            parts = line.strip().split()
            if not parts:
                continue
            cmd = parts[0]
            if cmd == "uci":
                print(f"id name Mimic of {name} (FIDE {player.get('fideID','?')})")
                print("id author china-chess-player-pgn mimic prototype")
                print(f"option name MimicElo type spin default {self.elo} min 1320 max 3190")
                print("option name BookTemp type string default 0.7")
                print("uciok", flush=True)
            elif cmd == "isready":
                self.ensure_backend()
                print("readyok", flush=True)
            elif cmd == "setoption":
                try:
                    idx = parts.index("name")
                    vidx = parts.index("value")
                    key = " ".join(parts[idx + 1:vidx]).lower()
                    value = " ".join(parts[vidx + 1:])
                    if key == "mimicelo":
                        self.elo = int(value)
                    elif key == "booktemp":
                        self.book_temp = float(value)
                except ValueError:
                    pass
            elif cmd == "ucinewgame":
                self.board = chess.Board()
            elif cmd == "position":
                self.parse_position(parts)
            elif cmd == "go":
                movetime = 0.3
                if "movetime" in parts:
                    movetime = int(parts[parts.index("movetime") + 1]) / 1000
                move = self.pick_move(movetime)
                print(f"bestmove {move.uci()}", flush=True)
            elif cmd == "quit":
                break
        self.close()

    def parse_position(self, parts: list[str]) -> None:
        if "startpos" in parts:
            self.board = chess.Board()
            moves_idx = parts.index("moves") + 1 if "moves" in parts else len(parts)
        elif "fen" in parts:
            fen_idx = parts.index("fen") + 1
            moves_idx = parts.index("moves") if "moves" in parts else len(parts)
            self.board = chess.Board(" ".join(parts[fen_idx:moves_idx]))
            moves_idx += 1
        else:
            return
        for uci in parts[moves_idx:]:
            try:
                self.board.push_uci(uci)
            except ValueError:
                break


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=pathlib.Path, required=True)
    parser.add_argument("--stockfish", default="/tmp/stockfish")
    args = parser.parse_args()
    MimicEngine(args.profile, args.stockfish).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
