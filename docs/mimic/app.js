/* Browser front-end: Chessground UI + Stockfish worker + mimic move selection. */
import { Chessground } from "./vendor/chessground/chessground.min.js";

(async function () {
  "use strict";

  let PROFILE = window.MIMIC_PROFILE;
  const ROUTE_TARGET = routeTarget();
  await loadRouteProfile();
  MimicCore.initRandom(window.POLYGLOT_RANDOM);

  const els = {
    board: document.getElementById("board"),
    status: document.getElementById("status"),
    source: document.getElementById("source"),
    moves: document.getElementById("moves"),
    moveCount: document.getElementById("moveCount"),
    elo: document.getElementById("elo"),
    undo: document.getElementById("undo"),
    flipBoard: document.getElementById("flipBoard"),
    prevMove: document.getElementById("prevMove"),
    nextMove: document.getElementById("nextMove"),
    copyPgn: document.getElementById("copyPgn"),
    copyStatus: document.getElementById("copyStatus"),
    playWhite: document.getElementById("playWhite"),
    playBlack: document.getElementById("playBlack"),
    sampleGames: document.getElementById("sampleGames"),
    avgPlies: document.getElementById("avgPlies"),
    queenTrade: document.getElementById("queenTrade"),
    styleNote: document.getElementById("styleNote"),
    openingList: document.getElementById("openingList"),
    explanations: document.getElementById("explanations"),
    explainCount: document.getElementById("explainCount"),
    profileName: document.getElementById("profileName"),
    profileMeta: document.getElementById("profileMeta"),
    routeSummary: document.getElementById("routeSummary"),
    controlMeta: document.getElementById("controlMeta"),
    bookMeta: document.getElementById("bookMeta"),
  };

  const game = new Chess();
  let humanColor = "w";
  let boardOrientation = "white";
  let viewPly = 0;
  let thinking = false;
  let copyTimer = null;
  const engineExplanations = [];

  let engine = null;
  let engineReady = false;
  let pendingSearch = null;

  const ground = Chessground(els.board, {
    fen: game.fen(),
    orientation: boardOrientation,
    coordinates: true,
    coordinatesOnSquares: true,
    ranksPosition: "left",
    autoCastle: true,
    trustAllEvents: true,
    animation: { enabled: true, duration: 180 },
    highlight: { lastMove: true, check: true },
    movable: {
      free: false,
      color: undefined,
      dests: new Map(),
      showDests: true,
      rookCastle: true,
      events: { after: onUserMove },
    },
    premovable: { enabled: false },
    draggable: { enabled: true, showGhost: true },
    drawable: { enabled: true, visible: true },
  });

  setGameHeaders("w");
  renderProfile();
  syncBoard();
  bootEngine();

  function bootEngine() {
    setTimeout(() => {
      if (!engineReady) setStatus("引擎加载超时。请通过 http://localhost 访问，并查看浏览器控制台。");
    }, 25000);

    try {
      engine = new Worker("vendor/stockfish-nnue-16-single.js");
    } catch (error) {
      setStatus("无法创建引擎 Worker：" + error.message);
      return;
    }

    engine.onerror = (event) => {
      setStatus("引擎加载失败：" + (event.message || "未知错误"));
    };

    engine.onmessage = (event) => {
      const line = typeof event.data === "string" ? event.data : "";
      if (line === "uciok") {
        engine.postMessage("setoption name UCI_LimitStrength value true");
        engine.postMessage("setoption name MultiPV value 4");
        engine.postMessage("isready");
      } else if (line === "readyok") {
        engineReady = true;
        setStatus("引擎就绪。选择执白或执黑开始。");
        syncBoard();
      } else if (pendingSearch) {
        collectSearchLine(line);
      }
    };

    engine.postMessage("uci");
  }

  function collectSearchLine(line) {
    if (line.startsWith("info ") && line.indexOf(" multipv ") >= 0 && line.indexOf(" pv ") >= 0) {
      const multipvMatch = line.match(/ multipv (\d+)/);
      const moveMatch = line.match(/ pv ([a-h][1-8][a-h][1-8][nbrq]?)/);
      if (!multipvMatch || !moveMatch) return;
      const cpMatch = line.match(/ score cp (-?\d+)/);
      const mateMatch = line.match(/ score mate (-?\d+)/);
      const mate = mateMatch ? parseInt(mateMatch[1], 10) : null;
      const cp = cpMatch ? parseInt(cpMatch[1], 10) : (mate ? (mate > 0 ? 10000 : -10000) : 0);
      pendingSearch.lines[parseInt(multipvMatch[1], 10)] = { uci: moveMatch[1], cp };
    } else if (line.startsWith("bestmove")) {
      const done = pendingSearch;
      pendingSearch = null;
      done.resolve(Object.keys(done.lines).sort((a, b) => a - b).map((key) => done.lines[key]));
    }
  }

  function searchCandidates(fen, movetimeMs) {
    return new Promise((resolve) => {
      if (!engineReady || !engine) {
        resolve([]);
        return;
      }
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

  async function mimicMove() {
    if (!engineReady || gameOver() || game.turn() === humanColor) return;

    thinking = true;
    setStatus("对方思考中...");
    syncBoard();

    const book = game.turn() === "w" ? PROFILE.bookWhite : PROFILE.bookBlack;
    const bookChoice = probeBookWithExplanation(book, 0.7);
    if (bookChoice) {
      finishMimicMove(bookChoice.move, "背谱（本人实战着法）", {
        source: "开局库",
        detail: bookChoice.explanation,
        candidates: bookChoice.candidates,
      });
      return;
    }

    const phase = MimicCore.phaseOf(game);
    const errors = MimicCore.phaseErrors(PROFILE.style, phase);
    const lines = await searchCandidates(game.fen(), 600);
    const verboseMoves = game.moves({ verbose: true });
    const candidates = [];
    for (const line of lines) {
      if (!line) continue;
      const verbose = verboseMoves.find((move) => moveUci(move) === line.uci || move.from + move.to === line.uci);
      if (verbose) {
        candidates.push({
          move: { from: verbose.from, to: verbose.to, promotion: verbose.promotion },
          cp: line.cp,
          verbose,
        });
      }
    }

    const chosen = MimicCore.pickFromCandidates(game, candidates, PROFILE.style);
    if (chosen) {
      const label = {
        best: "引擎最佳",
        styled: "风格偏好着法",
        blunder: "失误注入（" + phase + "）",
      }[chosen.kind];
      finishMimicMove(chosen.pick.move, label, explainCandidateChoice(chosen, candidates, phase, errors));
    } else if (verboseMoves.length) {
      finishMimicMove(verboseMoves[0], "兜底", {
        source: "兜底",
        detail: "没有收到可用 multiPV 候选，直接使用当前合法着法列表中的第一手。",
        candidates: verboseMoves.slice(0, 4).map((move) => move.san).join("，"),
      });
    } else {
      thinking = false;
      syncBoard();
    }
  }

  function finishMimicMove(move, sourceLabel, explanation) {
    const made = commitMove(move, "对方着法来源：" + sourceLabel, explanation);
    thinking = false;
    if (made && !gameOver()) setStatus("轮到你走棋。");
    syncBoard();
  }

  function commitMove(move, sourceText, explanation) {
    const made = game.move({ from: move.from, to: move.to, promotion: move.promotion || "q" });
    if (!made) {
      syncBoard();
      return null;
    }

    viewPly = game.history().length;
    els.source.textContent = sourceText || "";
    if (explanation) recordEngineExplanation(made, explanation);
    renderMoves();
    checkEnd();
    return made;
  }

  function probeBookWithExplanation(entries, temperature) {
    const key = MimicCore.zobristKey(game).toString(16).padStart(16, "0");
    const hits = entries.filter((entry) => entry.k === key);
    if (!hits.length) return null;

    const legalMoves = game.moves({ verbose: true });
    const legalHits = hits.map((entry) => {
      const move = MimicCore.decodeBookMove(game, entry.m);
      const verbose = legalMoves.find((item) => item.from === move.from && item.to === move.to
        && (!move.promotion || item.promotion === move.promotion));
      return verbose ? { entry, move, verbose } : null;
    }).filter(Boolean);
    if (!legalHits.length) return null;

    const temp = Math.max(0.1, temperature || 0.7);
    const weights = legalHits.map((item) => Math.pow(Math.max(1, item.entry.w), 1 / temp));
    const selected = legalHits[MimicCore.weightedIndex(weights)];
    const candidateText = legalHits
      .slice()
      .sort((a, b) => b.entry.w - a.entry.w)
      .slice(0, 4)
      .map((item) => `${item.verbose.san} 权重 ${item.entry.w}`)
      .join("，");
    return {
      move: selected.move,
      candidates: candidateText,
      explanation: `当前局面命中 ${hits.length} 条本人实战开局记录，其中 ${legalHits.length} 条在当前规则下可走。系统按“频率 × 结果加成 × 近期加成”得到权重，并用 0.7 温度采样，选中 ${selected.verbose.san}。`,
    };
  }

  function explainCandidateChoice(chosen, candidates, phase, errors) {
    const pickedUci = moveUci(chosen.pick.verbose || chosen.pick.move);
    const pickedIndex = Math.max(0, candidates.findIndex((item) => moveUci(item.verbose) === pickedUci));
    const candidateText = candidates.slice(0, 4)
      .map((item, index) => `${index + 1}.${item.verbose.san} ${formatCp(item.cp)}`)
      .join("，");
    const phaseLabel = { opening: "开局", middlegame: "中局", endgame: "残局" }[phase] || phase;
    if (chosen.kind === "best") {
      return {
        source: "引擎最佳",
        detail: `出库后用限强 Stockfish（Elo ${clampElo()}）搜索 600ms。当前处于${phaseLabel}，风格和失误模型没有推翻第一候选，因此选择 ${chosen.pick.verbose.san}。`,
        candidates: candidateText,
      };
    }
    if (chosen.kind === "styled") {
      return {
        source: "风格偏好",
        detail: `限强 Stockfish 先给出候选，再按该棋手的吃子率、将军率、易位和后交换倾向重排。当前${phaseLabel}失误采样率约 ${formatPercent(errors.mistakeRate)}，最终选择第 ${pickedIndex + 1} 候选 ${chosen.pick.verbose.san}。`,
        candidates: candidateText,
      };
    }
    return {
      source: "失误注入",
      detail: `分阶段人类化模型在${phaseLabel}触发失误注入，当前漏着率约 ${formatPercent(errors.blunderRate)}。系统从非首选候选中按权重采样，选出 ${chosen.pick.verbose.san}。`,
      candidates: candidateText,
    };
  }

  function onUserMove(from, to) {
    if (!canHumanMove()) {
      syncBoard();
      return;
    }

    const legalMoves = game.moves({ square: from, verbose: true }).filter((move) => move.to === to);
    const move = legalMoves.find((item) => item.promotion === "q") || legalMoves[0];
    if (!move) {
      syncBoard();
      return;
    }

    const made = commitMove({ from: move.from, to: move.to, promotion: move.promotion || "q" }, "");
    if (!made) return;

    if (!gameOver()) {
      setStatus("对方思考中...");
      syncBoard();
      mimicMove();
    } else {
      syncBoard();
    }
  }

  function syncBoard() {
    const preview = positionAt(viewPly);
    const checkColor = inCheck(preview.game) ? colorName(preview.game.turn()) : false;
    const latest = viewPly === game.history().length;
    ground.set({
      fen: preview.game.fen(),
      orientation: boardOrientation,
      turnColor: colorName(preview.game.turn()),
      lastMove: preview.lastMove ? [preview.lastMove.from, preview.lastMove.to] : undefined,
      check: checkColor,
      movable: {
        free: false,
        color: canHumanMove() ? colorName(humanColor) : undefined,
        dests: canHumanMove() ? legalDests() : new Map(),
        showDests: true,
        rookCastle: true,
        events: { after: onUserMove },
      },
    });
    if (!canHumanMove()) {
      ground.cancelMove();
      clearBoardSelectionDecorations();
      window.requestAnimationFrame(clearBoardSelectionDecorations);
    }
    if (!latest) renderReviewStatus();
    renderMoves();
    renderExplanations();
    renderControls();
  }

  function positionAt(ply) {
    const preview = new Chess();
    const moves = game.history({ verbose: true });
    for (let index = 0; index < Math.min(ply, moves.length); index += 1) {
      const move = moves[index];
      preview.move({ from: move.from, to: move.to, promotion: move.promotion || "q" });
    }
    return { game: preview, lastMove: moves[ply - 1] || null };
  }

  function legalDests() {
    const dests = new Map();
    for (const square of boardSquares()) {
      const moves = game.moves({ square, verbose: true });
      if (moves.length) dests.set(square, moves.map((move) => move.to));
    }
    return dests;
  }

  function clearBoardSelectionDecorations() {
    els.board.querySelectorAll("square.selected, square.move-dest, square.hover").forEach((square) => {
      square.classList.remove("selected", "move-dest", "hover");
    });
  }

  function boardSquares() {
    const squares = [];
    for (const file of "abcdefgh") {
      for (let rank = 1; rank <= 8; rank += 1) squares.push(file + rank);
    }
    return squares;
  }

  function canHumanMove() {
    return engineReady && !thinking && !gameOver() && game.turn() === humanColor && viewPly === game.history().length;
  }

  function colorName(color) {
    return color === "w" ? "white" : "black";
  }

  function gameOver(target = game) {
    return typeof target.isGameOver === "function" ? target.isGameOver() : target.game_over();
  }

  function inCheck(target = game) {
    return typeof target.inCheck === "function" ? target.inCheck() : target.in_check();
  }

  function inCheckmate(target = game) {
    return typeof target.isCheckmate === "function" ? target.isCheckmate() : target.in_checkmate();
  }

  function inStalemate(target = game) {
    return typeof target.isStalemate === "function" ? target.isStalemate() : target.in_stalemate();
  }

  function inThreefoldRepetition(target = game) {
    return typeof target.isThreefoldRepetition === "function"
      ? target.isThreefoldRepetition()
      : target.in_threefold_repetition();
  }

  function insufficientMaterial(target = game) {
    return typeof target.isInsufficientMaterial === "function"
      ? target.isInsufficientMaterial()
      : target.insufficient_material();
  }

  function renderMoves() {
    const history = game.history();
    if (!history.length) {
      els.moves.textContent = "（对局尚未开始）";
      els.moveCount.textContent = "0 手";
      return;
    }
    let html = `<button class="move-token" type="button" data-ply="0" aria-current="${viewPly === 0}">初始局面</button> `;
    for (let index = 0; index < history.length; index += 1) {
      if (index % 2 === 0) html += `<span class="move-number">${index / 2 + 1}.</span> `;
      html += `<button class="move-token" type="button" data-ply="${index + 1}" aria-current="${viewPly === index + 1}">${escapeHTML(history[index])}</button> `;
    }
    els.moves.innerHTML = html;
    els.moveCount.textContent = `${history.length} 手`;
    els.moves.querySelectorAll("[data-ply]").forEach((button) => {
      button.addEventListener("click", () => goToPly(Number(button.dataset.ply)));
    });
    els.moves.scrollTop = els.moves.scrollHeight;
  }

  function renderExplanations() {
    els.explainCount.textContent = `${engineExplanations.length} 条`;
    if (!engineExplanations.length) {
      els.explanations.className = "empty-state";
      els.explanations.textContent = "引擎走棋后会显示每一步的来源、候选和风格采样原因。";
      return;
    }
    els.explanations.className = "explain-list";
    els.explanations.innerHTML = engineExplanations.map((item) => `
      <article class="explain-item" aria-current="${item.ply === viewPly}">
        <div class="explain-title">
          <span>${escapeHTML(item.moveLabel)} ${escapeHTML(item.san)}</span>
          <span class="explain-meta">${escapeHTML(item.source)}</span>
        </div>
        <p class="explain-body">${escapeHTML(item.detail)}</p>
        ${item.candidates ? `<p class="explain-body explain-meta">候选：${escapeHTML(item.candidates)}</p>` : ""}
      </article>
    `).join("");
  }

  function recordEngineExplanation(made, explanation) {
    const ply = game.history().length;
    engineExplanations.push({
      ply,
      san: made.san,
      source: explanation.source,
      detail: explanation.detail,
      candidates: explanation.candidates || "",
      moveLabel: `${Math.ceil(ply / 2)}${made.color === "w" ? "." : "..."}`,
    });
  }

  function renderReviewStatus() {
    const total = game.history().length;
    const active = engineExplanations.find((item) => item.ply === viewPly);
    setStatus(viewPly === 0 ? "正在查看初始局面。" : `正在查看第 ${viewPly} / ${total} 手局面。`);
    els.source.textContent = active ? `${active.source}：${active.detail}` : "回到最新局面后可继续对弈。";
  }

  function goToPly(ply) {
    if (thinking) return;
    const total = game.history().length;
    viewPly = Math.max(0, Math.min(total, ply));
    if (viewPly === total) {
      if (gameOver()) checkEnd();
      else setStatus(canHumanMove() ? "轮到你走棋。" : "最新局面。");
      const active = engineExplanations.find((item) => item.ply === viewPly);
      els.source.textContent = active ? `${active.source}：${active.detail}` : "";
    }
    syncBoard();
  }

  function renderControls() {
    const total = game.history().length;
    els.playWhite.disabled = !engineReady || thinking;
    els.playBlack.disabled = !engineReady || thinking;
    els.undo.disabled = thinking || total === 0 || viewPly !== total;
    els.prevMove.disabled = thinking || viewPly <= 0;
    els.nextMove.disabled = thinking || viewPly >= total;
    els.elo.disabled = thinking;
  }

  function checkEnd() {
    if (!gameOver()) return;
    const result = resultTag();
    game.header("Result", result);
    let text = "对局结束：";
    if (inCheckmate()) text += game.turn() === humanColor ? "你被将杀。" : "你将杀了对方。";
    else if (inStalemate()) text += "逼和。";
    else if (inThreefoldRepetition()) text += "三次重复，和棋。";
    else if (insufficientMaterial()) text += "子力不足，和棋。";
    else text += "和棋。";
    setStatus(text);
  }

  function resultTag() {
    if (inCheckmate()) return game.turn() === "w" ? "0-1" : "1-0";
    if (gameOver()) return "1/2-1/2";
    return "*";
  }

  function setStatus(text) {
    els.status.textContent = text;
  }

  function newGame(color) {
    if (!engineReady || thinking) return;
    game.reset();
    humanColor = color;
    boardOrientation = colorName(color);
    viewPly = 0;
    engineExplanations.length = 0;
    setGameHeaders(color);
    els.source.textContent = "";
    els.copyStatus.textContent = "";
    setStatus(color === "w" ? "你执白，请走棋。" : "你执黑。");
    syncBoard();
    if (game.turn() !== humanColor) mimicMove();
  }

  function setGameHeaders(color) {
    const profile = profileDisplayName();
    const human = "Human";
    game.header(
      "Event", "China Chess Player PGN Mimic Beta",
      "Site", "Local Web",
      "Date", new Date().toISOString().slice(0, 10).replaceAll("-", "."),
      "White", color === "b" ? profile : human,
      "Black", color === "b" ? human : profile,
      "Result", "*"
    );
  }

  function renderProfile() {
    const style = PROFILE.style || {};
    const sample = style.sample || {};
    const book = style.book || {};
    const openings = style.openings || {};
    const styleStats = style.style || {};
    const profileName = profileDisplayName();
    const targetName = ROUTE_TARGET.name || profileName;
    const targetFide = ROUTE_TARGET.fideID || profileFideID();
    const matched = !ROUTE_TARGET.fideID || ROUTE_TARGET.fideID === profileFideID();

    document.title = `模拟对局 - ${targetName}`;
    els.profileName.textContent = matched ? profileName : targetName;
    els.profileMeta.textContent = matched
      ? `FIDE ${profileFideID()} · 标准分 2064 · 2014 年生`
      : `请求棋手 FIDE ${targetFide} · 当前可用画像：${profileName}（FIDE ${profileFideID()}）`;
    els.routeSummary.value = `与 ${targetName} 模拟对局`;
    els.controlMeta.textContent = `FIDE ${targetFide}`;
    els.bookMeta.textContent = `${book.whiteEntries || 0}/${book.blackEntries || 0} 条`;

    els.sampleGames.textContent = formatNumber(sample.games);
    els.avgPlies.textContent = formatNumber(sample.avgPlies, 1);
    els.queenTrade.textContent = formatPercent(styleStats.queenTradeGameShare);

    const whiteFirst = topEntry(openings.firstMovesAsWhite);
    const blackFirst = topEntry(openings.firstRepliesAsBlack);
    els.styleNote.textContent = "白棋高频 " + (whiteFirst?.name || "未知")
      + " · 黑棋常见 ..." + (blackFirst?.name || "未知");

    const rows = [
      ["白棋开局库", book.whiteEntries, "条"],
      ["黑棋开局库", book.blackEntries, "条"],
      ["白棋首着", whiteFirst ? whiteFirst.name + " · " + whiteFirst.count + " 局" : "暂无"],
      ["黑棋首应", blackFirst ? "..." + blackFirst.name + " · " + blackFirst.count + " 局" : "暂无"],
      ["长易位", formatCastleShare(styleStats.castling), ""],
    ];
    els.openingList.innerHTML = rows.map(([label, value, suffix]) => `
      <li>
        <strong>${escapeHTML(label)}</strong>
        <span>${escapeHTML(String(value ?? "-"))}${escapeHTML(suffix || "")}</span>
      </li>
    `).join("");
  }

  function profileDisplayName() {
    return PROFILE.style?.player?.names?.[0] || "Mimic Engine";
  }

  function profileFideID() {
    return String(PROFILE.style?.player?.fideID || "");
  }

  function routeTarget() {
    const params = new URLSearchParams(window.location.search);
    return {
      fideID: String(params.get("fideID") || params.get("fide") || "").replace(/^fide-/i, ""),
      name: params.get("name") || params.get("player") || "",
    };
  }

  async function loadRouteProfile() {
    const fideID = ROUTE_TARGET.fideID;
    if (!fideID || fideID === profileFideID()) return;
    const src = `profiles/fide-${encodeURIComponent(fideID)}/profile.js`;
    if (!(await staticFileExists(src))) return;
    await loadScript(src);
    if (window.MIMIC_PROFILE) PROFILE = window.MIMIC_PROFILE;
  }

  async function staticFileExists(src) {
    try {
      const response = await fetch(src, { method: "HEAD", cache: "no-store" });
      if (response.ok) return true;
      if (response.status !== 405) return false;
      const fallback = await fetch(src, { method: "GET", cache: "no-store" });
      return fallback.ok;
    } catch {
      return false;
    }
  }

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = src;
      script.async = true;
      script.onload = resolve;
      script.onerror = reject;
      document.head.appendChild(script);
    });
  }

  function topEntry(entries) {
    const entry = Array.isArray(entries) && entries.length ? entries[0] : null;
    return entry ? { name: entry[0], count: entry[1] } : null;
  }

  async function copyPGN() {
    const pgn = game.pgn({ max_width: 80, newline_char: "\n" }) || emptyPGN();
    let copied = false;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(pgn);
        copied = true;
      }
    } catch {
      copied = false;
    }
    if (!copied) copied = fallbackCopy(pgn);
    showCopyStatus(copied ? "已复制" : "复制失败");
  }

  function emptyPGN() {
    return [
      `[Event "China Chess Player PGN Mimic Beta"]`,
      `[Site "Local Web"]`,
      `[Date "${new Date().toISOString().slice(0, 10).replaceAll("-", ".")}"]`,
      `[White "${humanColor === "b" ? escapePGN(profileDisplayName()) : "Human"}"]`,
      `[Black "${humanColor === "b" ? "Human" : escapePGN(profileDisplayName())}"]`,
      `[Result "*"]`,
      "",
      "*",
    ].join("\n");
  }

  function fallbackCopy(text) {
    let eventCopied = false;
    const handler = (event) => {
      event.clipboardData?.setData("text/plain", text);
      event.preventDefault();
      eventCopied = true;
    };
    document.addEventListener("copy", handler);
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.top = "0";
    textarea.style.left = "0";
    textarea.style.width = "1px";
    textarea.style.height = "1px";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    textarea.setSelectionRange(0, text.length);
    const copied = document.execCommand("copy");
    textarea.remove();
    document.removeEventListener("copy", handler);
    return eventCopied || copied;
  }

  function showCopyStatus(text) {
    els.copyStatus.textContent = text;
    if (copyTimer) window.clearTimeout(copyTimer);
    copyTimer = window.setTimeout(() => {
      els.copyStatus.textContent = "";
    }, 1600);
  }

  function escapePGN(value) {
    return String(value).replace(/\\/g, "\\\\").replace(/"/g, "\\\"");
  }

  function moveUci(move) {
    return `${move.from}${move.to}${move.promotion || ""}`;
  }

  function formatCp(cp) {
    if (Math.abs(cp) >= 10000) return cp > 0 ? "M+" : "M-";
    return `${cp > 0 ? "+" : ""}${(cp / 100).toFixed(2)}`;
  }

  function formatNumber(value, digits) {
    if (value == null || Number.isNaN(Number(value))) return "-";
    return Number(value).toLocaleString("zh-CN", {
      minimumFractionDigits: digits || 0,
      maximumFractionDigits: digits || 0,
    });
  }

  function formatPercent(value) {
    if (value == null || Number.isNaN(Number(value))) return "-";
    return Math.round(Number(value) * 100) + "%";
  }

  function formatCastleShare(castling) {
    if (!castling) return "-";
    const total = Object.values(castling).reduce((sum, value) => sum + Number(value || 0), 0);
    if (!total) return "-";
    return Math.round(((Number(castling["O-O-O"]) || 0) / total) * 100) + "%";
  }

  function escapeHTML(value) {
    return String(value).replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      "\"": "&quot;",
      "'": "&#39;",
    }[char]));
  }

  els.playWhite.addEventListener("click", () => newGame("w"));
  els.playBlack.addEventListener("click", () => newGame("b"));
  els.flipBoard.addEventListener("click", () => {
    boardOrientation = boardOrientation === "white" ? "black" : "white";
    syncBoard();
  });
  els.prevMove.addEventListener("click", () => goToPly(viewPly - 1));
  els.nextMove.addEventListener("click", () => goToPly(viewPly + 1));
  els.copyPgn.addEventListener("click", copyPGN);
  els.undo.addEventListener("click", () => {
    if (thinking || viewPly !== game.history().length) return;
    game.undo();
    game.undo();
    viewPly = game.history().length;
    while (engineExplanations.length && engineExplanations[engineExplanations.length - 1].ply > viewPly) {
      engineExplanations.pop();
    }
    els.source.textContent = "";
    setStatus("已悔棋。");
    syncBoard();
  });
})();
