#!/usr/bin/env python3
"""Build a player-mimic profile from a per-player PGN collection.

Outputs, into --out-dir:
  white.bin / black.bin   polyglot opening books built only from the player's
                          own moves (weighted by result and recency)
  style.json              non-engine style statistics; engine-based phase
                          accuracy is appended separately by analyze_phases.py

Usage:
  python3 build_player_profile.py --pgn all.pgn --player "Zhang, Hongya" \
      --fide-id 8640980 --out-dir profiles/fide-8640980
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import struct
from collections import Counter, defaultdict

import chess
import chess.pgn
import chess.polyglot

BOOK_MAX_PLY = 30
PROMO_CODE = {None: 0, chess.KNIGHT: 1, chess.BISHOP: 2, chess.ROOK: 3, chess.QUEEN: 4}


def normalize_name(value: str) -> str:
    return re.sub(r"[\s,.\-_']+", "", (value or "")).casefold()


def player_side(game: chess.pgn.Game, names: set[str], fide_id: str) -> chess.Color | None:
    headers = game.headers
    for side, color in (("White", chess.WHITE), ("Black", chess.BLACK)):
        header_fide = re.sub(r"\D", "", headers.get(f"{side}FideId", "") or headers.get(f"{side}FideID", ""))
        if fide_id and header_fide == fide_id:
            return color
        if normalize_name(headers.get(side, "")) in names:
            return color
    return None


def polyglot_move_int(board: chess.Board, move: chess.Move) -> int:
    """Encode a move in polyglot book format (castling = king takes rook)."""
    to_sq = move.to_square
    if board.is_castling(move):
        to_sq = chess.H1 if move.to_square == chess.G1 else to_sq
        to_sq = chess.A1 if move.to_square == chess.C1 else to_sq
        if board.turn == chess.BLACK:
            to_sq = chess.H8 if move.to_square == chess.G8 else to_sq
            to_sq = chess.A8 if move.to_square == chess.C8 else to_sq
    return (
        chess.square_file(to_sq)
        | (chess.square_rank(to_sq) << 3)
        | (chess.square_file(move.from_square) << 6)
        | (chess.square_rank(move.from_square) << 9)
        | (PROMO_CODE[move.promotion] << 12)
    )


def result_weight(result: str, color: chess.Color) -> float:
    if result == "1/2-1/2":
        return 1.0
    won = (result == "1-0" and color == chess.WHITE) or (result == "0-1" and color == chess.BLACK)
    if won:
        return 1.6
    if result in {"1-0", "0-1"}:
        return 0.6
    return 1.0


def recency_weight(date_text: str, newest_year: int) -> float:
    match = re.match(r"(\d{4})", date_text or "")
    if not match:
        return 1.0
    age = max(0, newest_year - int(match.group(1)))
    return max(0.5, 1.5 - 0.25 * age)


def write_polyglot(path: pathlib.Path, entries: dict[tuple[int, int], float]) -> int:
    max_weight = max(entries.values(), default=1.0)
    scale = 60000.0 / max_weight if max_weight > 0 else 1.0
    packed = []
    for (key, move_int), weight in entries.items():
        w = max(1, min(65535, int(round(weight * scale))))
        packed.append(struct.pack(">QHHI", key, move_int, w, 0))
    packed.sort()
    path.write_bytes(b"".join(packed))
    return len(packed)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build mimic profile (books + style) from player PGN.")
    parser.add_argument("--pgn", type=pathlib.Path, required=True)
    parser.add_argument("--player", action="append", required=True, help="player name (repeatable for aliases)")
    parser.add_argument("--fide-id", default="")
    parser.add_argument("--out-dir", type=pathlib.Path, required=True)
    parser.add_argument("--book-max-ply", type=int, default=BOOK_MAX_PLY)
    args = parser.parse_args()

    names = {normalize_name(n) for n in args.player}
    books: dict[chess.Color, dict[tuple[int, int], float]] = {chess.WHITE: defaultdict(float), chess.BLACK: defaultdict(float)}

    games_meta = []
    stats = {
        "games": 0, "asWhite": 0, "asBlack": 0, "wins": 0, "draws": 0, "losses": 0,
        "plies": [], "castle": Counter(), "castlePly": [],
        "capturesMade": 0, "capturesFaced": 0, "movesTotal": 0,
        "queenTradePly": [], "queenTradeGames": 0,
        "promotions": 0, "checksGiven": 0,
        "firstMovesWhite": Counter(), "repliesBlack": Counter(),
        "ecoWhite": Counter(), "ecoBlack": Counter(),
        "materialAtMove30": [],
    }
    newest_year = 0
    raw_games = []

    with args.pgn.open(encoding="utf-8", errors="replace") as handle:
        while True:
            game = chess.pgn.read_game(handle)
            if game is None:
                break
            color = player_side(game, names, args.fide_id)
            if color is None:
                continue
            raw_games.append((game, color))
            match = re.match(r"(\d{4})", game.headers.get("Date", ""))
            if match:
                newest_year = max(newest_year, int(match.group(1)))

    for game, color in raw_games:
        headers = game.headers
        result = headers.get("Result", "*")
        g_weight = result_weight(result, color) * recency_weight(headers.get("Date", ""), newest_year)

        stats["games"] += 1
        stats["asWhite" if color == chess.WHITE else "asBlack"] += 1
        won = (result == "1-0") == (color == chess.WHITE) and result in {"1-0", "0-1"}
        if result == "1/2-1/2":
            stats["draws"] += 1
        elif result in {"1-0", "0-1"}:
            stats["wins" if won else "losses"] += 1

        eco = headers.get("ECO", "")
        if eco:
            stats["ecoWhite" if color == chess.WHITE else "ecoBlack"][eco] += 1

        board = game.board()
        ply = 0
        castled = None
        queens_gone_ply = None
        for move in game.mainline_moves():
            is_player_move = board.turn == color
            if is_player_move:
                stats["movesTotal"] += 1
                if board.is_capture(move):
                    stats["capturesMade"] += 1
                if move.promotion:
                    stats["promotions"] += 1
                if board.is_castling(move):
                    castled = "O-O" if chess.square_file(move.to_square) == 6 else "O-O-O"
                    stats["castlePly"].append(ply + 1)
                if ply < args.book_max_ply:
                    key = chess.polyglot.zobrist_hash(board)
                    books[color][(key, polyglot_move_int(board, move))] += g_weight
                if ply == 0 and color == chess.WHITE:
                    stats["firstMovesWhite"][board.san(move)] += 1
                if ply == 1 and color == chess.BLACK:
                    stats["repliesBlack"][board.san(move)] += 1
            else:
                if board.is_capture(move):
                    stats["capturesFaced"] += 1
            board.push(move)
            ply += 1
            if board.is_check() and is_player_move:
                stats["checksGiven"] += 1
            if queens_gone_ply is None and not any(board.pieces(chess.QUEEN, c) for c in chess.COLORS):
                queens_gone_ply = ply
            if ply == 60:
                material = sum(len(board.pieces(pt, c)) * v for c in chess.COLORS
                               for pt, v in ((chess.PAWN, 1), (chess.KNIGHT, 3), (chess.BISHOP, 3), (chess.ROOK, 5), (chess.QUEEN, 9)))
                stats["materialAtMove30"].append(material)

        stats["plies"].append(ply)
        stats["castle"][castled or "none"] += 1
        if queens_gone_ply is not None:
            stats["queenTradeGames"] += 1
            stats["queenTradePly"].append(queens_gone_ply)
        games_meta.append({
            "event": headers.get("Event", ""), "date": headers.get("Date", ""),
            "color": "white" if color == chess.WHITE else "black",
            "result": result, "plies": ply,
        })

    args.out_dir.mkdir(parents=True, exist_ok=True)
    white_entries = write_polyglot(args.out_dir / "white.bin", books[chess.WHITE])
    black_entries = write_polyglot(args.out_dir / "black.bin", books[chess.BLACK])

    def avg(values):
        return round(sum(values) / len(values), 2) if values else None

    style = {
        "schemaVersion": 1,
        "generatedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "player": {"names": args.player, "fideID": args.fide_id},
        "sample": {"games": stats["games"], "asWhite": stats["asWhite"], "asBlack": stats["asBlack"],
                   "wins": stats["wins"], "draws": stats["draws"], "losses": stats["losses"],
                   "avgPlies": avg(stats["plies"])},
        "book": {"whiteEntries": white_entries, "blackEntries": black_entries, "maxPly": args.book_max_ply},
        "openings": {
            "firstMovesAsWhite": stats["firstMovesWhite"].most_common(5),
            "firstRepliesAsBlack": stats["repliesBlack"].most_common(5),
            "topECOAsWhite": stats["ecoWhite"].most_common(8),
            "topECOAsBlack": stats["ecoBlack"].most_common(8),
        },
        "style": {
            "captureRate": round(stats["capturesMade"] / max(1, stats["movesTotal"]), 4),
            "captureRateFaced": round(stats["capturesFaced"] / max(1, stats["movesTotal"]), 4),
            "checkRate": round(stats["checksGiven"] / max(1, stats["movesTotal"]), 4),
            "promotionsPerGame": round(stats["promotions"] / max(1, stats["games"]), 3),
            "castling": dict(stats["castle"]),
            "avgCastlePly": avg(stats["castlePly"]),
            "queenTradeGameShare": round(stats["queenTradeGames"] / max(1, stats["games"]), 3),
            "avgQueenTradePly": avg(stats["queenTradePly"]),
            "avgMaterialAtMove30": avg(stats["materialAtMove30"]),
        },
        "gamesMeta": games_meta,
    }
    (args.out_dir / "style.json").write_text(json.dumps(style, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"games": stats["games"], "whiteBookEntries": white_entries, "blackBookEntries": black_entries,
                      "out": str(args.out_dir)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
