// docs/brilliancies.js - 实战妙手互动列表与棋盘逻辑

(function () {
  "use strict";

  const PIECE_SYMBOLS = {
    P: "♙", N: "♘", B: "♗", R: "♖", Q: "♕", K: "♔",
    p: "♟", n: "♞", b: "♝", r: "♜", q: "♛", k: "♚"
  };

  const state = {
    items: [],
    filteredItems: [],
    currentItem: null,
    currentDetail: null,
    selectionSerial: 0,
    moveHistory: [],
    historyIndex: 0,
    mode: "watch", // "watch" | "guess"
    selectedSquare: null,
    themeFilter: "",
    searchQuery: "",
    sortBy: "featured",
    activeTab: "continuation", // "continuation" | "variation"
    isFullGameMode: false
  };

  const $ = (sel) => document.querySelector(sel);

  function escapeHtml(str) {
    if (str === null || str === undefined) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  // Parse FEN into 8x8 board representation
  function parseFen(fen) {
    const parts = (fen || "").trim().split(" ");
    const boardPart = parts[0] || "8/8/8/8/8/8/8/8";
    const rows = boardPart.split("/");
    const board = [];
    for (let r = 0; r < 8; r++) {
      const row = [];
      const fenRow = rows[r] || "8";
      for (const ch of fenRow) {
        if (ch >= "1" && ch <= "8") {
          const emptyCount = parseInt(ch, 10);
          for (let e = 0; e < emptyCount; e++) row.push(null);
        } else {
          row.push(ch);
        }
      }
      board.push(row);
    }
    return board;
  }

  // Pure chess state transition: handles piece moves, castling, en-passant, promotions
  function applyMove(fen, uci) {
    if (!fen || !uci || uci.length < 4) return fen;
    const parts = fen.trim().split(" ");
    const board = parseFen(fen);
    const turn = parts[1] || "w";
    let castling = parts[2] || "-";
    let ep = parts[3] || "-";
    let halfmove = parseInt(parts[4] || "0", 10);
    let fullmove = parseInt(parts[5] || "1", 10);

    const from = uci.slice(0, 2);
    const to = uci.slice(2, 4);
    const promo = uci[4]?.toLowerCase();

    const fFrom = from.charCodeAt(0) - 97;
    const rFrom = 8 - parseInt(from[1], 10);
    const fTo = to.charCodeAt(0) - 97;
    const rTo = 8 - parseInt(to[1], 10);

    if (rFrom < 0 || rFrom > 7 || fFrom < 0 || fFrom > 7) return fen;
    if (rTo < 0 || rTo > 7 || fTo < 0 || fTo > 7) return fen;

    const piece = board[rFrom]?.[fFrom];
    if (!piece) return fen;

    const isWhite = piece === piece.toUpperCase();
    const pieceType = piece.toLowerCase();

    // Capture detection (must be checked before moving the piece)
    const capturedPiece = board[rTo]?.[fTo];
    const isEnPassantCapture = pieceType === "p" && to === ep;
    const isCapture = Boolean(capturedPiece) || isEnPassantCapture;

    // Reset en-passant square
    let nextEp = "-";

    // En passant capture
    if (isEnPassantCapture) {
      if (isWhite) {
        board[rTo + 1][fTo] = null;
      } else {
        board[rTo - 1][fTo] = null;
      }
    }

    // Pawn double push -> set en-passant square
    if (pieceType === "p" && Math.abs(rTo - rFrom) === 2) {
      const epRank = isWhite ? 3 : 6;
      nextEp = from[0] + epRank;
    }

    // Castling move
    if (pieceType === "k" && Math.abs(fTo - fFrom) === 2) {
      if (fTo === 6) {
        // Kingside castling
        board[rTo][5] = board[rTo][7];
        board[rTo][7] = null;
      } else if (fTo === 2) {
        // Queenside castling
        board[rTo][3] = board[rTo][0];
        board[rTo][0] = null;
      }
    }

    // Move the piece
    board[rFrom][fFrom] = null;
    let finalPiece = piece;
    if (pieceType === "p" && (rTo === 0 || rTo === 7) && promo) {
      finalPiece = isWhite ? promo.toUpperCase() : promo.toLowerCase();
    }
    board[rTo][fTo] = finalPiece;

    // Update castling rights
    // 1. Moving king or rooks
    if (piece === "K") {
      castling = castling.replace(/[KQ]/g, "");
    } else if (piece === "k") {
      castling = castling.replace(/[kq]/g, "");
    } else if (piece === "R") {
      if (from === "a1") castling = castling.replace("Q", "");
      if (from === "h1") castling = castling.replace("K", "");
    } else if (piece === "r") {
      if (from === "a8") castling = castling.replace("q", "");
      if (from === "h8") castling = castling.replace("k", "");
    }
    // 2. Capturing opponent corner rooks
    if (to === "a1") castling = castling.replace("Q", "");
    if (to === "h1") castling = castling.replace("K", "");
    if (to === "a8") castling = castling.replace("q", "");
    if (to === "h8") castling = castling.replace("k", "");

    if (!castling) castling = "-";

    const nextTurn = turn === "w" ? "b" : "w";
    if (turn === "b") fullmove++;
    if (pieceType === "p" || isCapture) halfmove = 0;
    else halfmove++;

    const rows = [];
    for (let r = 0; r < 8; r++) {
      let empty = 0;
      let rowStr = "";
      for (let f = 0; f < 8; f++) {
        if (!board[r][f]) {
          empty++;
        } else {
          if (empty > 0) {
            rowStr += empty;
            empty = 0;
          }
          rowStr += board[r][f];
        }
      }
      if (empty > 0) rowStr += empty;
      rows.push(rowStr);
    }
    return `${rows.join("/")} ${nextTurn} ${castling} ${nextEp} ${halfmove} ${fullmove}`;
  }

  // Draw board on SVG
  function renderBoard(fen, highlights = {}) {
    const svg = $("#chessBoardSvg");
    if (!svg) return;
    svg.innerHTML = "";

    const board = parseFen(fen);
    const sqSize = 50;

    for (let r = 0; r < 8; r++) {
      for (let f = 0; f < 8; f++) {
        const isLight = (r + f) % 2 === 0;
        const squareName = String.fromCharCode(97 + f) + (8 - r);
        const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
        rect.setAttribute("x", f * sqSize);
        rect.setAttribute("y", r * sqSize);
        rect.setAttribute("width", sqSize);
        rect.setAttribute("height", sqSize);

        let fillColor = isLight ? "#f0d9b5" : "#b58863";
        if (highlights.from === squareName || highlights.to === squareName) {
          fillColor = isLight ? "#f7ec7d" : "#dac352";
        } else if (highlights.selected === squareName) {
          fillColor = isLight ? "#baca44" : "#8ca83b";
        }
        rect.setAttribute("fill", fillColor);
        rect.setAttribute("data-square", squareName);
        rect.style.cursor = state.mode === "guess" ? "pointer" : "default";

        rect.addEventListener("click", () => handleSquareClick(squareName));
        svg.appendChild(rect);

        // Piece
        const pieceChar = board[r][f];
        if (pieceChar) {
          const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
          text.setAttribute("x", f * sqSize + sqSize / 2);
          text.setAttribute("y", r * sqSize + sqSize / 2 + 15);
          text.setAttribute("text-anchor", "middle");
          text.setAttribute("font-size", "38");
          text.setAttribute("font-family", "serif");
          text.setAttribute("data-square", squareName);
          text.style.pointerEvents = "none";
          text.textContent = PIECE_SYMBOLS[pieceChar] || pieceChar;
          svg.appendChild(text);
        }
      }
    }
  }

  function buildMoveHistory(item) {
    const history = [];
    const baseFen = item.position.fenBefore;
    history.push({
      fen: baseFen,
      moveUci: null,
      san: "初始局面",
      from: null,
      to: null,
    });

    // Move 1: The Brilliancy move
    const brilliancyUci = item.move.uci;
    const fenAfterBrilliancy = applyMove(baseFen, brilliancyUci);
    history.push({
      fen: fenAfterBrilliancy,
      moveUci: brilliancyUci,
      san: `${item.move.san} !!`,
      from: brilliancyUci.slice(0, 2),
      to: brilliancyUci.slice(2, 4),
    });

    // Subsequent moves
    let prevFen = fenAfterBrilliancy;
    const actualUcis = item.actualContinuationUci || [];
    const actualSans = item.actualContinuationSan || [];
    for (let i = 0; i < actualUcis.length; i++) {
      const u = actualUcis[i];
      const nextFen = applyMove(prevFen, u);
      history.push({
        fen: nextFen,
        moveUci: u,
        san: actualSans[i] || u,
        from: u.slice(0, 2),
        to: u.slice(2, 4),
      });
      prevFen = nextFen;
    }
    return history;
  }

  function buildFullGameHistory(item) {
    const history = [];
    const initialFen = item.game.initialFen || "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
    history.push({
      fen: initialFen,
      moveUci: null,
      san: "开局",
      from: null,
      to: null,
      isBrilliancy: false,
    });

    const movesUci = item.game?.movesUci || [];
    const movesSan = item.game?.movesSan || [];
    const brilliancyPly = item.position?.ply;

    let curFen = initialFen;
    for (let i = 0; i < movesUci.length; i++) {
      const u = movesUci[i];
      const san = movesSan[i] || u;
      curFen = applyMove(curFen, u);
      const isBrilliancy = (i === brilliancyPly);
      history.push({
        fen: curFen,
        moveUci: u,
        san: isBrilliancy ? `${san} !!` : san,
        from: u.slice(0, 2),
        to: u.slice(2, 4),
        isBrilliancy,
        ply: i
      });
    }
    return history;
  }

  function renderCurrentStep() {
    if (!state.moveHistory.length) return;
    const current = state.moveHistory[state.historyIndex];
    if (!current) return;

    const highlights = {};
    if (current.from && current.to) {
      highlights.from = current.from;
      highlights.to = current.to;
    }
    renderBoard(current.fen, highlights);
  }

  function handleSquareClick(square) {
    if (state.mode !== "guess" || !state.currentItem) return;

    const board = parseFen(state.moveHistory[0]?.fen || state.currentItem.position.fenBefore);
    const f = square.charCodeAt(0) - 97;
    const r = 8 - parseInt(square[1], 10);
    const piece = board[r]?.[f];
    const sideToMove = state.currentItem.position.sideToMove || "white";
    const isMoverPiece = piece && (sideToMove === "white" ? piece === piece.toUpperCase() : piece === piece.toLowerCase());

    if (!state.selectedSquare) {
      // First click: Must select a piece of the mover
      if (!isMoverPiece) {
        return; // Ignore clicks on empty squares or enemy pieces
      }
      state.selectedSquare = square;
      renderBoard(state.moveHistory[0].fen, { selected: square });
    } else {
      // Second click: Target square
      const from = state.selectedSquare;
      const to = square;
      state.selectedSquare = null;

      if (from === to) {
        renderBoard(state.moveHistory[0].fen);
        return;
      }

      const guessedUci = from + to;
      const actualUci = state.currentItem.move.uci.toLowerCase();

      const guessMsg = $("#guessMsg");
      if (guessedUci === actualUci || guessedUci === actualUci.slice(0, 4)) {
        guessMsg.className = "br-guess-msg";
        guessMsg.style.display = "block";
        guessMsg.innerHTML = "🎉 <strong>太棒了，完全正确！</strong> 这正是大师实战中走出的精妙一步！";

        revealAnswer();
        state.historyIndex = 1;
        renderCurrentStep();
      } else {
        guessMsg.className = "br-guess-msg error";
        guessMsg.style.display = "block";
        guessMsg.innerHTML = "这不是本局实战招法。再试一次，或点击下方查看答案。";
        renderBoard(state.moveHistory[0].fen);
      }
    }
  }

  function revealAnswer() {
    const item = state.currentItem;
    if (!item) return;
    $("#moveBadge").textContent = `${item.move.san} !!`;
    $("#brTitle").textContent = item.title || `${item.move.san} 实战妙手`;
    $("#brSummary").textContent = item.summary || "";
    updateTabContent();
  }

  async function loadDetail(id, snapshot) {
    try {
      const bucket = id.replace("br-", "")[0].toLowerCase();
      const resp = await fetch(`./data/brilliancies/shards/${bucket}.json`);
      if (!resp.ok) return null;
      const shard = await resp.json();
      if (snapshot && shard.snapshotId !== snapshot) return null;
      const item = shard.items?.[id];
      return item?.status === "published" ? item : null;
    } catch {
      return null;
    }
  }

  async function selectBrilliancy(item) {
    const selection = ++state.selectionSerial;
    state.currentItem = item;
    state.currentDetail = null;
    state.selectedSquare = null;

    // Highlight card in list
    document.querySelectorAll(".br-card").forEach((card) => {
      card.classList.toggle("active", card.getAttribute("data-id") === item.id);
    });

    // Build move history
    state.moveHistory = buildMoveHistory(item);

    // Fetch full detail for variations
    const detail = await loadDetail(item.id, item.snapshotId);
    if (selection !== state.selectionSerial) return;
    if (!detail) {
      $("#brTitle").textContent = "详情暂不可用，请刷新后重试";
      return;
    }
    state.currentDetail = detail;
    state.moveHistory = buildMoveHistory(detail);
    const verification = detail.verification || {};
    $("#metaEngine").textContent = verification.engine || "未记录";
    $("#metaBudget").textContent = `搜索 ${Number(verification.nodes || 0).toLocaleString()} 节点，深度 ${verification.depth || "未记录"}`;
    $("#metaRights").textContent = (detail.rights || []).map(r => `${r.attribution} · ${r.license}`).join("；");

    // Harmonize links safely: directly open matching game in player PGN archive
    const whiteFide = String(item.white?.playerId || "").replace("fide-", "");
    const blackFide = String(item.black?.playerId || "").replace("fide-", "");
    const targetFide = whiteFide || blackFide;
    const gid = item.game?.id || "";
    const ply = item.position?.ply ?? 0;

    const fullGameBtn = $("#fullGameLink");
    if (fullGameBtn) {
      if (targetFide && gid) {
        fullGameBtn.href = `./?fideID=${encodeURIComponent(targetFide)}&game=${encodeURIComponent(gid)}&ply=${encodeURIComponent(ply)}`;
        fullGameBtn.textContent = "在主库中打开原局 ↗";
      } else if (targetFide) {
        fullGameBtn.href = `./?fideID=${encodeURIComponent(targetFide)}`;
        fullGameBtn.textContent = "查看棋手档案 ↗";
      } else {
        fullGameBtn.href = "./";
        fullGameBtn.textContent = "返回首页 ↗";
      }
    }

    state.isFullGameMode = false;
    const toggleBtn = $("#toggleFullGameBtn");
    if (toggleBtn) {
      toggleBtn.textContent = "完整原局复盘";
      toggleBtn.classList.remove("primary");
    }

    const downloadLink = $("#downloadPgnLink");
    if (downloadLink) {
      downloadLink.href = `./api/v1/brilliancies/${item.id}.pgn`;
    }

    $("#matchSummary").textContent = `${item.white.displayName} vs ${item.black.displayName} (${item.event?.name || ""})`;

    if (state.mode === "watch") {
      $("#guessMsg").style.display = "none";
      $("#moveBadge").textContent = `${item.move.san} !!`;
      $("#brTitle").textContent = item.title || `${item.move.san} 实战妙手`;
      $("#brSummary").textContent = item.summary || "";
      state.historyIndex = 1;
      renderCurrentStep();
      updateTabContent();
    } else {
      setupGuessMode();
    }
  }

  function setupGuessMode() {
    const item = state.currentItem;
    if (!item) return;

    $("#moveBadge").textContent = "先猜一手 (??)";
    const sideCn = item.position.sideToMove === "white" ? "白方" : "黑方";
    $("#brTitle").textContent = `走法竞猜：轮到${sideCn}行棋`;
    $("#brSummary").textContent = "请在棋盘上选择己方棋子并移动到目标格。找出大师在实战中走出的突破一步！";

    const guessMsg = $("#guessMsg");
    guessMsg.className = "br-guess-msg";
    guessMsg.style.display = "block";
    guessMsg.innerHTML = `💡 <strong>先猜一手：</strong> 轮到${sideCn}走棋。点击你想移动的棋子，再点击目标格。<button type="button" id="revealAnswerBtn" style="margin-left:12px;padding:3px 8px;font-size:0.78rem;border:1px solid #177a3d;background:#fff;border-radius:4px;cursor:pointer;">查看答案</button>`;

    $("#revealAnswerBtn")?.addEventListener("click", () => {
      revealAnswer();
      state.historyIndex = 1;
      renderCurrentStep();
      guessMsg.innerHTML = "💡 答案已揭晓，可自由复盘或切换至欣赏模式。";
    });

    state.historyIndex = 0;
    renderCurrentStep();

    const container = $("#tabContent");
    if (container) {
      container.innerHTML = "<p style='color:var(--muted);'>猜出妙手或点击“查看答案”后解锁实战后续与深度变化。</p>";
    }
  }

  function updateTabContent() {
    const container = $("#tabContent");
    if (!container || !state.currentDetail) return;

    if (state.mode === "guess" && $("#moveBadge")?.textContent.includes("??")) {
      container.innerHTML = "<p style='color:var(--muted);'>猜出妙手或点击“查看答案”后解锁实战后续与深度变化。</p>";
      return;
    }

    if (state.isFullGameMode) {
      const moves = state.currentItem?.game?.movesSan || [];
      const brilliancyPly = state.currentItem?.position?.ply;
      let movesHtml = "";
      for (let i = 0; i < moves.length; i += 2) {
        const moveNum = Math.floor(i / 2) + 1;
        const wSan = moves[i];
        const bSan = moves[i + 1] || "";
        const wStep = i + 1;
        const bStep = i + 2;
        const isWBr = (i === brilliancyPly);
        const isBBr = (i + 1 === brilliancyPly);
        movesHtml += ` <span style="color:var(--muted);font-size:0.85rem;">${moveNum}.</span> `;
        movesHtml += `<span class="br-step-link ${isWBr ? 'brilliancy-move' : ''}" data-step="${wStep}" style="cursor:pointer;padding:2px 4px;border-radius:3px;${isWBr ? 'background:#177a3d;color:#fff;font-weight:700;' : 'background:var(--panel-soft);'}">${escapeHtml(wSan)}${isWBr ? ' !!' : ''}</span>`;
        if (bSan) {
          movesHtml += ` <span class="br-step-link ${isBBr ? 'brilliancy-move' : ''}" data-step="${bStep}" style="cursor:pointer;padding:2px 4px;border-radius:3px;${isBBr ? 'background:#177a3d;color:#fff;font-weight:700;' : 'background:var(--panel-soft);'}">${escapeHtml(bSan)}${isBBr ? ' !!' : ''}</span>`;
        }
      }
      container.innerHTML = `<div style="max-height:160px;overflow-y:auto;line-height:1.8;padding:4px;"><p style="margin:0 0 6px;"><strong>整局走法记录（点击任意步跳转棋盘）：</strong></p>${movesHtml}</div>`;
      container.querySelectorAll(".br-step-link").forEach((el) => {
        el.addEventListener("click", () => {
          const step = parseInt(el.getAttribute("data-step"), 10);
          if (step >= 0 && step < state.moveHistory.length) {
            state.historyIndex = step;
            renderCurrentStep();
          }
        });
      });
      return;
    }

    if (state.activeTab === "continuation") {
      const actualSans = state.currentDetail.actualContinuationSan || [];
      if (actualSans.length === 0) {
        container.innerHTML = "<p style='color:var(--muted);'>实战对局在此结束，未记录后续走法。</p>";
      } else {
        const movesHtml = actualSans
          .map((s, idx) => `<span class="br-step-link" data-step="${idx + 2}" style="cursor:pointer;padding:2px 4px;border-radius:3px;margin:0 2px;background:var(--panel-soft);">${escapeHtml(s)}</span>`)
          .join(" ");
        container.innerHTML = `<p><strong>实战后续着法：</strong> ${movesHtml}</p>`;
        container.querySelectorAll(".br-step-link").forEach((el) => {
          el.addEventListener("click", () => {
            const step = parseInt(el.getAttribute("data-step"), 10);
            if (step >= 0 && step < state.moveHistory.length) {
              state.historyIndex = step;
              renderCurrentStep();
            }
          });
        });
      }
    } else {
      const lines = state.currentDetail.analysisLines || [];
      if (lines.length === 0) {
        container.innerHTML = "<p style='color:var(--muted);'>暂无备选分支。</p>";
      } else {
        container.innerHTML = lines.map((l) => `
          <div style="margin-bottom:8px;">
            <strong>${escapeHtml(l.title || "变化")}：</strong> <code>${escapeHtml((l.san || []).join(" "))}</code>
          </div>
        `).join("");
      }
    }
  }

  function renderList() {
    const listEl = $("#brListContainer");
    if (!listEl) return;
    listEl.innerHTML = "";

    const query = state.searchQuery.trim().toLowerCase();
    const theme = state.themeFilter;

    state.filteredItems = state.items.filter((item) => {
      if (theme && !(item.themes || []).includes(theme)) return false;
      if (query) {
        const text = `${item.title} ${item.summary} ${item.white.displayName} ${item.black.displayName} ${item.event?.name || ""}`.toLowerCase();
        if (!text.includes(query)) return false;
      }
      return true;
    });

    if (state.sortBy === "newest") {
      state.filteredItems.sort((a, b) => String(b.event?.date || "").localeCompare(String(a.event?.date || "")));
    }

    $("#resultCount").textContent = `共 ${state.filteredItems.length} 条妙手`;

    if (state.filteredItems.length === 0) {
      listEl.innerHTML = "<p style='padding:30px; text-align:center; color:var(--muted);'>未找到符合条件的实战妙手。</p>";
      return;
    }

    state.filteredItems.forEach((item) => {
      const card = document.createElement("div");
      card.className = "br-card";
      card.setAttribute("data-id", item.id);
      if (state.currentItem && state.currentItem.id === item.id) {
        card.classList.add("active");
      }

      const tagsHtml = (item.themes || [])
        .map((t) => `<span class="br-tag">${escapeHtml(t)}</span>`)
        .join("");

      card.innerHTML = `
        <div class="br-card-head">
          <div class="br-card-move">${escapeHtml(item.move.san)} !!</div>
          <div class="br-card-players">${escapeHtml(item.white.displayName)} vs ${escapeHtml(item.black.displayName)}</div>
        </div>
        <h3 class="br-card-title">${escapeHtml(item.title)}</h3>
        <p class="br-card-summary">${escapeHtml(item.summary)}</p>
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <div class="br-card-tags">${tagsHtml}</div>
          <small style="color:var(--muted); font-size:0.78rem;">${escapeHtml(item.event?.name || "")} ${escapeHtml(item.event?.date || "")}</small>
        </div>
      `;

      card.addEventListener("click", () => selectBrilliancy(item));
      listEl.appendChild(card);
    });

    if (!state.currentItem && state.filteredItems.length > 0) {
      selectBrilliancy(state.filteredItems[0]);
    }
  }

  // Initialize
  async function init() {
    try {
      const resp = await fetch("./data/brilliancies/items.json");
      if (!resp.ok) {
        $("#brTitle").textContent = "妙手数据加载失败";
        return;
      }
      state.items = await resp.json();
      renderList();

      const urlParams = new URLSearchParams(window.location.search);
      const requestedId = urlParams.get("id") || window.location.hash.replace("#", "");
      if (requestedId) {
        const found = state.items.find((i) => i.id === requestedId);
        if (found) selectBrilliancy(found);
      }
    } catch (err) {
      console.error(err);
      $("#brTitle").textContent = "妙手数据加载错误";
    }

    // Event listeners
    $("#themeFilter")?.addEventListener("change", (e) => {
      state.themeFilter = e.target.value;
      renderList();
    });

    $("#sortFilter")?.addEventListener("change", (e) => {
      state.sortBy = e.target.value;
      renderList();
    });

    $("#searchInput")?.addEventListener("input", (e) => {
      state.searchQuery = e.target.value;
      renderList();
    });

    $("#modeWatchBtn")?.addEventListener("click", () => {
      state.mode = "watch";
      document.body.classList.remove("br-guess-mode");
      $("#modeWatchBtn").classList.add("active");
      $("#modeGuessBtn").classList.remove("active");
      if (state.currentItem) selectBrilliancy(state.currentItem);
    });

    $("#modeGuessBtn")?.addEventListener("click", () => {
      state.mode = "guess";
      document.body.classList.add("br-guess-mode");
      $("#modeGuessBtn").classList.add("active");
      $("#modeWatchBtn").classList.remove("active");
      setupGuessMode();
    });

    $("#prevBtn")?.addEventListener("click", () => {
      if (state.historyIndex > 0) {
        state.historyIndex--;
        renderCurrentStep();
      }
    });

    $("#nextBtn")?.addEventListener("click", () => {
      if (state.historyIndex < state.moveHistory.length - 1) {
        state.historyIndex++;
        renderCurrentStep();
      }
    });

    $("#toggleFullGameBtn")?.addEventListener("click", () => {
      const item = state.currentItem;
      if (!item) return;
      state.isFullGameMode = !state.isFullGameMode;
      const btn = $("#toggleFullGameBtn");
      if (state.isFullGameMode) {
        btn.textContent = "⚡ 返回妙手片段";
        btn.classList.add("primary");
        state.moveHistory = buildFullGameHistory(item);
        const targetStep = (item.position?.ply ?? 0) + 1;
        state.historyIndex = Math.min(targetStep, state.moveHistory.length - 1);
      } else {
        btn.textContent = "完整原局复盘";
        btn.classList.remove("primary");
        state.moveHistory = buildMoveHistory(item);
        state.historyIndex = 1;
      }
      renderCurrentStep();
      updateTabContent();
    });

    $("#playBtn")?.addEventListener("click", () => {
      if (state.isFullGameMode && state.currentItem) {
        const targetStep = (state.currentItem.position?.ply ?? 0) + 1;
        state.historyIndex = Math.min(targetStep, state.moveHistory.length - 1);
      } else {
        state.historyIndex = 1;
      }
      renderCurrentStep();
    });

    $("#resetBtn")?.addEventListener("click", () => {
      if (state.isFullGameMode && state.currentItem) {
        const targetStep = state.currentItem.position?.ply ?? 0;
        state.historyIndex = Math.min(targetStep, state.moveHistory.length - 1);
      } else {
        state.historyIndex = 0;
      }
      renderCurrentStep();
    });

    $("#tabContinuation")?.addEventListener("click", () => {
      state.activeTab = "continuation";
      $("#tabContinuation").classList.add("active");
      $("#tabVariation").classList.remove("active");
      updateTabContent();
    });

    $("#tabVariation")?.addEventListener("click", () => {
      state.activeTab = "variation";
      $("#tabVariation").classList.add("active");
      $("#tabContinuation").classList.remove("active");
      updateTabContent();
    });

    $("#shareBtn")?.addEventListener("click", () => {
      if (!state.currentItem) return;
      const shareUrl = `${window.location.origin}/brilliancies.html?id=${state.currentItem.id}`;
      navigator.clipboard?.writeText(shareUrl).then(() => {
        alert("已复制妙手分享链接到剪贴板！\n" + shareUrl);
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
