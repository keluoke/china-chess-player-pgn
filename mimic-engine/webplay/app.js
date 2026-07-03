/* Browser front-end: board UI + Stockfish worker + mimic move selection. */
(function () {
  "use strict";

  const PROFILE = window.MIMIC_PROFILE;
  MimicCore.initRandom(window.POLYGLOT_RANDOM);

  const UNICODE = { p:"♟", n:"♞", b:"♝", r:"♜", q:"♛", k:"♚" };
  const els = {
    board: document.getElementById("board"),
    status: document.getElementById("status"),
    source: document.getElementById("source"),
    moves: document.getElementById("moves"),
    elo: document.getElementById("elo"),
    undo: document.getElementById("undo"),
  };

  const game = new Chess();
  let humanColor = "w";
  let selected = null;
  let lastMove = null;
  let thinking = false;

  // ---------- Stockfish worker (UCI over postMessage) ----------
  let engine = null;
  let engineReady = false;
  let pendingSearch = null;

  function bootEngine() {
    setTimeout(() => {
      if (!engineReady) setStatus("引擎加载超时——请确认通过 http://localhost 访问（不能直接双击打开文件），并查看浏览器控制台。");
    }, 25000);
    engine = new Worker("vendor/stockfish-nnue-16-single.js");
    engine.onmessage = (event) => {
      const line = typeof event.data === "string" ? event.data : "";
      if (line === "uciok") {
        engine.postMessage("setoption name UCI_LimitStrength value true");
        engine.postMessage("setoption name MultiPV value 4");
        engine.postMessage("isready");
      } else if (line === "readyok") {
        engineReady = true;
        setStatus("引擎就绪。选择执白或执黑开始。");
      } else if (pendingSearch) {
        if (line.startsWith("info ") && line.indexOf(" multipv ") >= 0 && line.indexOf(" pv ") >= 0) {
          const mpv = parseInt(line.match(/ multipv (\d+)/)[1], 10);
          const cpMatch = line.match(/ score cp (-?\d+)/);
          const mateMatch = line.match(/ score mate (-?\d+)/);
          const cp = cpMatch ? parseInt(cpMatch[1], 10)
            : (mateMatch ? (parseInt(mateMatch[1], 10) > 0 ? 10000 : -10000) : 0);
          const uci = line.match(/ pv ([a-h][1-8][a-h][1-8][nbrq]?)/)[1];
          pendingSearch.lines[mpv] = { uci, cp };
        } else if (line.startsWith("bestmove")) {
          const done = pendingSearch;
          pendingSearch = null;
          done.resolve(Object.keys(done.lines).sort((a, b) => a - b).map((k) => done.lines[k]));
        }
      }
    };
    engine.postMessage("uci");
  }

  function searchCandidates(fen, movetimeMs) {
    return new Promise((resolve) => {
      pendingSearch = { lines: {}, resolve };
      engine.postMessage("setoption name UCI_Elo value " + clampElo());
      engine.postMessage("position fen " + fen);
      engine.postMessage("go movetime " + movetimeMs);
    });
  }

  function clampElo() {
    const value = parseInt(els.elo.value, 10) || 2050;
    return Math.max(1320, Math.min(3190, value));
  }

  // ---------- Mimic move choice ----------
  async function mimicMove() {
    thinking = true;
    setStatus("对方思考中…");
    const book = game.turn() === "w" ? PROFILE.bookWhite : PROFILE.bookBlack;
    const bookMove = MimicCore.probeBook(game, book, 0.7);
    if (bookMove) {
      applyMove(bookMove, "背谱（本人实战着法）");
      thinking = false;
      return;
    }
    const lines = await searchCandidates(game.fen(), 600);
    const verboseMoves = game.moves({ verbose: true });
    const candidates = [];
    for (const line of lines) {
      if (!line) continue;
      const verbose = verboseMoves.find((m) => m.from + m.to + (m.promotion || "") === line.uci
        || m.from + m.to === line.uci);
      if (verbose) candidates.push({ move: { from: verbose.from, to: verbose.to, promotion: verbose.promotion }, cp: line.cp, verbose });
    }
    const chosen = MimicCore.pickFromCandidates(game, candidates, PROFILE.style);
    if (chosen) {
      const label = { best: "引擎最佳", styled: "风格偏好着法", blunder: "失误注入（" + MimicCore.phaseOf(game) + "）" }[chosen.kind];
      applyMove(chosen.pick.move, label);
    } else if (verboseMoves.length) {
      applyMove(verboseMoves[0], "兜底");
    }
    thinking = false;
  }

  function applyMove(move, sourceLabel) {
    const made = game.move({ from: move.from, to: move.to, promotion: move.promotion || "q" });
    if (!made) return;
    lastMove = made;
    els.source.textContent = "对方着法来源：" + sourceLabel;
    render();
    checkEnd();
    if (!game.game_over() && game.turn() !== humanColor) setTimeout(mimicMove, 60);
  }

  // ---------- Board UI ----------
  function squareAt(i) {
    // visual index -> square name, honoring orientation
    const row = Math.floor(i / 8), col = i % 8;
    const rank = humanColor === "w" ? 8 - row : row + 1;
    const file = humanColor === "w" ? col : 7 - col;
    return "abcdefgh"[file] + rank;
  }

  function render() {
    els.board.innerHTML = "";
    const legalTargets = selected
      ? game.moves({ square: selected, verbose: true }).map((m) => m.to) : [];
    for (let i = 0; i < 64; i++) {
      const sq = squareAt(i);
      const div = document.createElement("div");
      const fileIdx = sq.charCodeAt(0) - 97, rankIdx = parseInt(sq[1], 10) - 1;
      div.className = "sq " + ((fileIdx + rankIdx) % 2 ? "light" : "dark");
      const piece = game.get(sq);
      if (piece) {
        div.textContent = UNICODE[piece.type];
        div.classList.add(piece.color === "w" ? "white-piece" : "black-piece");
      }
      if (selected === sq) div.classList.add("sel");
      if (legalTargets.indexOf(sq) >= 0) div.classList.add(piece ? "cap" : "dot");
      if (lastMove && (lastMove.from === sq || lastMove.to === sq)) div.classList.add("last");
      div.addEventListener("click", () => onSquareClick(sq));
      els.board.appendChild(div);
    }
    renderMoves();
  }

  function renderMoves() {
    const history = game.history();
    let text = "";
    for (let i = 0; i < history.length; i += 2) {
      text += (i / 2 + 1) + ". " + history[i] + (history[i + 1] ? " " + history[i + 1] : "") + "  ";
    }
    els.moves.textContent = text || "（对局尚未开始）";
    els.moves.scrollTop = els.moves.scrollHeight;
  }

  function onSquareClick(sq) {
    if (thinking || game.game_over() || game.turn() !== humanColor || !engineReady) return;
    const piece = game.get(sq);
    if (selected) {
      const move = game.moves({ square: selected, verbose: true }).find((m) => m.to === sq);
      if (move) {
        selected = null;
        const made = game.move({ from: move.from, to: move.to, promotion: "q" });
        lastMove = made;
        render();
        checkEnd();
        if (!game.game_over()) mimicMove();
        return;
      }
    }
    selected = piece && piece.color === humanColor ? sq : null;
    render();
  }

  function checkEnd() {
    if (!game.game_over()) return;
    let text = "对局结束：";
    if (game.in_checkmate()) text += game.turn() === humanColor ? "你被将杀。" : "你将杀了对方！";
    else if (game.in_stalemate()) text += "逼和。";
    else if (game.in_threefold_repetition()) text += "三次重复，和棋。";
    else if (game.insufficient_material()) text += "子力不足，和棋。";
    else text += "和棋。";
    setStatus(text);
  }

  function setStatus(text) { els.status.textContent = text; }

  function newGame(color) {
    if (!engineReady) return;
    game.reset();
    humanColor = color;
    selected = null;
    lastMove = null;
    els.source.textContent = "";
    setStatus(color === "w" ? "你执白，请走棋。" : "你执黑。");
    render();
    if (game.turn() !== humanColor) mimicMove();
  }

  document.getElementById("playWhite").addEventListener("click", () => newGame("w"));
  document.getElementById("playBlack").addEventListener("click", () => newGame("b"));
  els.undo.addEventListener("click", () => {
    if (thinking) return;
    game.undo(); game.undo();
    selected = null; lastMove = null;
    setStatus("已悔棋。");
    render();
  });

  render();
  bootEngine();
})();
