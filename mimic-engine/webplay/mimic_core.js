// Core mimic logic shared by the browser page and node tests:
// polyglot zobrist hashing / book probing / style re-ranking / error injection.
// Works on a chess.js game instance.
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.MimicCore = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  let RANDOM = null; // BigInt[781]

  function initRandom(hexArray) {
    RANDOM = hexArray.map((h) => BigInt("0x" + h));
  }

  const PIECE_INDEX = { p: 0, n: 1, b: 2, r: 3, q: 4, k: 5 };

  // chess.js game -> polyglot zobrist key (BigInt)
  function zobristKey(game) {
    if (!RANDOM) throw new Error("initRandom first");
    let h = 0n;
    const rows = game.board(); // [0]=rank8
    for (let i = 0; i < 8; i++) {
      for (let j = 0; j < 8; j++) {
        const piece = rows[i][j];
        if (!piece) continue;
        const square = (7 - i) * 8 + j;
        const pieceIndex = PIECE_INDEX[piece.type] * 2 + (piece.color === "w" ? 1 : 0);
        h ^= RANDOM[64 * pieceIndex + square];
      }
    }
    const fen = game.fen().split(" ");
    const castling = fen[2];
    if (castling.indexOf("K") >= 0) h ^= RANDOM[768];
    if (castling.indexOf("Q") >= 0) h ^= RANDOM[769];
    if (castling.indexOf("k") >= 0) h ^= RANDOM[770];
    if (castling.indexOf("q") >= 0) h ^= RANDOM[771];
    const turn = fen[1];
    const ep = fen[3];
    if (ep !== "-") {
      const epFile = ep.charCodeAt(0) - 97;
      const pawnRankIdx = turn === "w" ? 3 : 4; // board() row index of capturing pawns
      for (const df of [-1, 1]) {
        const f = epFile + df;
        if (f < 0 || f > 7) continue;
        const piece = rows[pawnRankIdx][f];
        if (piece && piece.type === "p" && piece.color === turn) {
          h ^= RANDOM[772 + epFile];
          break;
        }
      }
    }
    if (turn === "w") h ^= RANDOM[780];
    return h;
  }

  function squareName(rank, file) {
    return "abcdefgh"[file] + (rank + 1);
  }

  // Decode a polyglot move int into a chess.js move object for this position.
  function decodeBookMove(game, moveInt) {
    const toFile = moveInt & 7, toRank = (moveInt >> 3) & 7;
    const fromFile = (moveInt >> 6) & 7, fromRank = (moveInt >> 9) & 7;
    const promoCode = (moveInt >> 12) & 7;
    let from = squareName(fromRank, fromFile);
    let to = squareName(toRank, toFile);
    const promotion = [null, "n", "b", "r", "q"][promoCode] || undefined;
    const piece = game.get(from);
    if (piece && piece.type === "k") {
      if (from === "e1" && to === "h1") to = "g1";
      else if (from === "e1" && to === "a1") to = "c1";
      else if (from === "e8" && to === "h8") to = "g8";
      else if (from === "e8" && to === "a8") to = "c8";
    }
    return { from, to, promotion: promotion || undefined };
  }

  function probeBook(game, entries, temperature, rng) {
    const key = zobristKey(game).toString(16).padStart(16, "0");
    const hits = entries.filter((e) => e.k === key);
    if (!hits.length) return null;
    const temp = Math.max(0.1, temperature || 0.7);
    const weights = hits.map((e) => Math.pow(Math.max(1, e.w), 1 / temp));
    const move = decodeBookMove(game, hits[weightedIndex(weights, rng)].m);
    const legal = game.moves({ verbose: true }).some((m) => m.from === move.from && m.to === move.to);
    return legal ? move : null;
  }

  function weightedIndex(weights, rng) {
    const total = weights.reduce((a, b) => a + b, 0);
    let roll = (rng || Math.random)() * total;
    for (let i = 0; i < weights.length; i++) {
      roll -= weights[i];
      if (roll <= 0) return i;
    }
    return weights.length - 1;
  }

  function phaseOf(game) {
    const ply = game.history().length;
    if (ply <= 20) return "opening";
    let nonPawn = 0;
    const values = { n: 3, b: 3, r: 5, q: 9 };
    for (const row of game.board()) {
      for (const piece of row) {
        if (piece && values[piece.type]) nonPawn += values[piece.type];
      }
    }
    return nonPawn <= 26 ? "endgame" : "middlegame";
  }

  const DEFAULT_PHASE_ERRORS = {
    opening: { blunderRate: 0.02, mistakeRate: 0.08 },
    middlegame: { blunderRate: 0.05, mistakeRate: 0.14 },
    endgame: { blunderRate: 0.08, mistakeRate: 0.18 },
  };

  function phaseErrors(style, phase) {
    const stats = ((style.engine || {}).phases || {})[phase] || {};
    const fallback = DEFAULT_PHASE_ERRORS[phase];
    return {
      blunderRate: stats.blunderRate != null ? stats.blunderRate : fallback.blunderRate,
      mistakeRate: stats.mistakeRate != null ? stats.mistakeRate : fallback.mistakeRate,
    };
  }

  function styleBonusCp(game, verboseMove, style) {
    const s = style.style || {};
    let bonus = 0;
    const isCapture = verboseMove.flags.indexOf("c") >= 0 || verboseMove.flags.indexOf("e") >= 0;
    if (isCapture) bonus += 500 * ((s.captureRate || 0.2) - (s.captureRateFaced || 0.2));
    if (verboseMove.san.indexOf("+") >= 0 || verboseMove.san.indexOf("#") >= 0) {
      bonus += 400 * ((s.checkRate || 0.04) - 0.04);
    }
    if (verboseMove.flags.indexOf("q") >= 0) {
      const castles = s.castling || {};
      const total = Object.values(castles).reduce((a, b) => a + b, 0) || 1;
      const share = (castles["O-O-O"] || 0) / total;
      if (share > 0.15) bonus += 25 * share;
    }
    if ((s.queenTradeGameShare || 0) >= 0.6 && isCapture && verboseMove.piece === "q" && verboseMove.captured === "q") {
      bonus += 12;
    }
    return bonus;
  }

  // candidates: [{move:{from,to,promotion}, cp:int, verbose:chess.js verbose move}]
  function pickFromCandidates(game, candidates, style, rng) {
    if (!candidates.length) return null;
    const errors = phaseErrors(style, phaseOf(game));
    const rand = rng || Math.random;
    const roll = rand();
    if (candidates.length > 1 && roll < errors.blunderRate) {
      const weights = candidates.map((_, i) => (i === 0 ? 0 : 1 / i));
      return { pick: candidates[weightedIndex(weights, rand)], kind: "blunder" };
    }
    const adjusted = candidates.map((c) => ({ c, score: c.cp + styleBonusCp(game, c.verbose, style) }));
    const top = Math.max(...adjusted.map((a) => a.score));
    const temp = roll < errors.mistakeRate ? 45.0 : 18.0;
    const weights = adjusted.map((a) => Math.exp((a.score - top) / temp));
    const idx = weightedIndex(weights, rand);
    return { pick: adjusted[idx].c, kind: idx === 0 ? "best" : "styled" };
  }

  return { initRandom, zobristKey, decodeBookMove, probeBook, phaseOf, phaseErrors, pickFromCandidates, weightedIndex };
});
