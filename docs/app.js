import LichessPgnViewer from "./vendor/lichess-pgn-viewer/lichess-pgn-viewer.min.js";

const state = {
  activeStage: "ALL",
  selectedFideID: null,
  selectedEventIDs: new Set(),
  downloadStatus: "",
  query: "",
  viewer: {
    fideID: "",
    pgnPath: "",
    status: "idle",
    gameIndex: 0,
    orientation: "",
    error: ""
  }
};

const els = {
  ageRuleText: document.querySelector("#ageRuleText"),
  stageTabs: document.querySelector("#stageTabs"),
  leaderboardGrid: document.querySelector("#leaderboardGrid"),
  detailPane: document.querySelector("#detailPane"),
  searchInput: document.querySelector("#searchInput"),
  searchResultsSection: document.querySelector("#searchResultsSection"),
  searchResults: document.querySelector("#searchResults"),
  searchCount: document.querySelector("#searchCount"),
  rankingMeta: document.querySelector("#rankingMeta")
};

const data = await loadData();
const stages = data.ageRule.stages;
const players = data.players.map(preparePlayer);
const detailCache = new Map();
const detailRequests = new Map();
const staticPlayerCache = new Map();
const staticPlayerRequests = new Map();
const bulkStageIndexCache = new Map();
const bulkPlayerCache = new Map();
const bulkPlayerRequests = new Map();
const pgnViewerCache = new Map();
const pgnViewerRequests = new Map();

initialize();

async function loadData() {
  try {
    const youth = await fetchJSON("./data/youth-leaderboards.json", true);
    const [
      manifest,
      indexedPlayers,
      registryManifest,
      registryPlayers,
      bulkManifest,
      bulkYouthManifest,
      byPlayerManifest,
      byPlayerPlayers
    ] = await Promise.all([
      fetchJSON("./data/index/manifest.json", false),
      fetchJSON("./data/index/players.json", false),
      fetchJSON("./data/registry/manifest.json", false),
      fetchJSON("./data/registry/players.json", false),
      fetchJSON("./data/bulk/manifest.json", false),
      fetchJSON("./data/bulk/youth/manifest.json", false),
      fetchJSON("./data/index/by-player/manifest.json", false),
      fetchJSON("./data/index/by-player/players.json", false)
    ]);
    return {
      ...youth,
      manifest,
      registryManifest,
      bulkManifest,
      bulkYouthManifest,
      byPlayerManifest,
      players: mergePlayers(youth.players ?? [], indexedPlayers ?? [], registryPlayers ?? [], byPlayerPlayers ?? [])
    };
  } catch (error) {
    document.body.innerHTML = `<main class="empty-state">无法加载静态数据：${escapeHTML(error.message)}</main>`;
    throw error;
  }
}

async function fetchJSON(path, required) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) {
    if (!required && response.status === 404) return null;
    throw new Error(`${path} HTTP ${response.status}`);
  }
  return response.json();
}

function mergePlayers(leaderboardPlayers, indexedPlayers, registryPlayers, byPlayerPlayers) {
  const byFide = new Map();
  registryPlayers.forEach(player => byFide.set(String(player.fideID), { ...player }));

  indexedPlayers.forEach(indexed => {
    const fideID = String(indexed.fideID);
    const current = byFide.get(fideID) ?? {};
    byFide.set(fideID, {
      ...current,
      ...indexed,
      aliases: [...(current.aliases ?? []), ...(indexed.aliases ?? [])],
      detailPath: indexed.detailPath ?? current.detailPath,
      eventCount: indexed.eventCount ?? current.eventCount,
      pgnCount: indexed.pgnCount ?? current.pgnCount,
      gameCount: indexed.gameCount ?? current.gameCount,
      displayName: indexed.displayName ?? current.displayName,
      name: indexed.name ?? current.name ?? indexed.displayName ?? current.displayName ?? `FIDE ${fideID}`,
      chineseName: indexed.chineseName ?? current.chineseName,
      pinyin: indexed.pinyin ?? current.pinyin
    });
  });

  leaderboardPlayers.forEach(player => {
    const fideID = String(player.fideID);
    const current = byFide.get(fideID) ?? {};
    byFide.set(fideID, {
      ...current,
      ...player,
      aliases: [...(current.aliases ?? []), ...(player.aliases ?? [])],
      detailPath: current.detailPath ?? player.detailPath,
      eventCount: current.eventCount ?? player.eventCount,
      pgnCount: current.pgnCount ?? player.pgnCount,
      gameCount: current.gameCount ?? player.gameCount,
      standard: current.standard ?? player.standard,
      rapid: current.rapid ?? player.rapid,
      blitz: current.blitz ?? player.blitz,
      birthYear: current.birthYear ?? player.birthYear,
      displayName: current.displayName ?? player.displayName,
      name: current.name ?? player.name ?? player.displayName ?? `FIDE ${fideID}`,
      chineseName: current.chineseName ?? player.chineseName,
      pinyin: current.pinyin ?? player.pinyin
    });
  });

  byPlayerPlayers.forEach(player => {
    const fideID = String(player.fideID);
    const current = byFide.get(fideID) ?? {};
    byFide.set(fideID, {
      ...current,
      ...player,
      aliases: [...(current.aliases ?? []), ...(player.aliases ?? [])],
      eventCount: Math.max(Number(current.eventCount) || 0, Number(player.eventCount) || 0),
      gameCount: Math.max(Number(current.gameCount) || 0, Number(player.gameCount) || 0),
      playerPgnPath: player.playerPgnPath ?? current.playerPgnPath,
      playerPgnGameCount: player.playerPgnGameCount ?? current.playerPgnGameCount,
      playerIndexPath: player.playerIndexPath ?? current.playerIndexPath,
      stages: { ...(current.stages ?? {}), ...(player.stages ?? {}) },
      sources: [...(current.sources ?? []), ...(player.sources ?? [])],
      displayName: current.displayName ?? player.displayName,
      name: current.name ?? player.name ?? current.displayName ?? player.displayName ?? `FIDE ${fideID}`,
      chineseName: current.chineseName ?? player.chineseName,
      pinyin: current.pinyin ?? player.pinyin
    });
  });

  return [...byFide.values()];
}

function initialize() {
  state.selectedFideID = rankingsForStage("ALL")[0]?.fideID ?? players[0]?.fideID ?? null;
  resetSelectedEvents();
  els.searchInput.addEventListener("input", event => {
    state.query = event.target.value.trim();
    renderSearch();
  });

  render();
}

function render() {
  els.ageRuleText.textContent = ageRuleText();
  renderLeaderboardArea();
  renderSearch();
  renderDetail();
}

function renderLeaderboardArea() {
  els.rankingMeta.textContent = `${state.activeStage === "ALL" ? "U8-U18" : state.activeStage} · Top 20`;
  renderTabs();
  renderLeaderboards();
}

function renderTabs() {
  const tabs = [{ id: "ALL", label: "U8-U18" }, ...stages.map(stage => ({ id: stage.id, label: stage.id }))];
  els.stageTabs.innerHTML = tabs.map(tab => `
    <button type="button" role="tab" aria-selected="${state.activeStage === tab.id}" data-stage="${tab.id}">
      ${escapeHTML(tab.label)}
    </button>
  `).join("");

  els.stageTabs.querySelectorAll("button").forEach(button => {
    button.addEventListener("click", () => {
      state.activeStage = button.dataset.stage;
      renderLeaderboardArea();
    });
  });
}

function renderLeaderboards() {
  els.leaderboardGrid.innerHTML = leaderboardCard(state.activeStage);
  els.leaderboardGrid.querySelectorAll("[data-fide]").forEach(row => {
    row.addEventListener("click", () => selectPlayer(row.dataset.fide));
    row.addEventListener("keydown", event => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectPlayer(row.dataset.fide);
      }
    });
  });
}

function leaderboardCard(stageID) {
  const entries = rankingsForStage(stageID);
  const stage = stages.find(item => item.id === stageID);
  const title = stage ? stage.id : "U8-U18";
  const subtitle = stage
    ? `${stage.birthYears} 出生 · ${stage.lowerAge}-${stage.upperAge} 岁`
    : `${data.competitionYear} 年李成智杯自然年龄组口径`;
  const maxRating = Math.max(...entries.map(entry => entry.rating.value), 1);
  const rows = entries.map((entry, index) => {
    const player = entry.player;
    const playerStage = stageForPlayer(player);
    const note = liChengzhiNote(player, playerStage?.id ?? stageID);
    const width = Math.max(6, Math.round((entry.rating.value / maxRating) * 100));
    return `
      <tr data-fide="${escapeAttribute(player.fideID)}" role="button" tabindex="0">
        <td class="rank-cell"><span class="rank-badge">${index + 1}</span></td>
        <td>
          <div class="player-name">${escapeHTML(displayName(player))}</div>
          <div class="player-meta">${escapeHTML(playerStage?.id ?? "-")} · FIDE ${escapeHTML(player.fideID)} · ${player.birthYear} 出生</div>
          ${note ? `<span class="note-pill">${escapeHTML(note)}</span>` : ""}
          <div class="bar-track" aria-hidden="true"><div class="bar-fill" style="--bar-width: ${width}%"></div></div>
        </td>
        <td class="rating-cell">
          <div class="rating-value">${entry.rating.value}</div>
          <div class="rating-kind">${entry.rating.kind}</div>
        </td>
      </tr>
    `;
  }).join("");

  return `
    <article class="leaderboard-card">
      <div class="card-head">
        <div>
          <h2 class="stage-title">${escapeHTML(title)}</h2>
          <div class="stage-range">${escapeHTML(subtitle)}</div>
        </div>
        <span class="stage-chip">FIDE</span>
      </div>
      <table class="leaderboard-table">
        <tbody>${rows || `<tr><td colspan="3"><div class="empty-state compact">暂无排行数据</div></td></tr>`}</tbody>
      </table>
    </article>
  `;
}

function renderSearch() {
  const matches = searchPlayers(state.query);
  els.searchResultsSection.hidden = state.query.length === 0;
  els.searchCount.textContent = `${matches.length} 名`;
  els.searchResults.innerHTML = matches.map(player => {
    const stage = stageForPlayer(player);
    const rating = ratingForPlayer(player);
    return `
      <button class="result-button" type="button" data-fide="${escapeAttribute(player.fideID)}">
        <div class="player-name">${escapeHTML(displayName(player))}</div>
        <div class="player-meta">${stage?.id ?? "-"} · FIDE ${escapeHTML(player.fideID)} · ${rating?.value ?? "-"} ${rating?.kind ?? ""}</div>
      </button>
    `;
  }).join("");

  els.searchResults.querySelectorAll("button").forEach(button => {
    button.addEventListener("click", () => selectPlayer(button.dataset.fide));
  });
}

function renderDetail() {
  const player = selectedPlayer();
  if (!player) {
    els.detailPane.innerHTML = `<div class="empty-state">请选择棋手</div>`;
    return;
  }
  requestPlayerDetail(player);
  requestStaticPlayerDetail(player);

  const stage = stageForPlayer(player);
  const note = stage ? liChengzhiNote(player, stage.id) : null;
  const staticInfo = staticPlayerInfo(player);
  const eventSource = staticInfo?.events?.length ? staticInfo.events : (player.events ?? []);
  const events = [...eventSource].sort((a, b) => (b.date ?? "").localeCompare(a.date ?? ""));
  const pgnEvents = events.filter(event => event.pgnPath);
  const selectedEvents = pgnEvents.filter(event => state.selectedEventIDs.has(eventKey(event)));
  const totalGames = staticInfo?.gameCount || pgnEvents.reduce((sum, event) => sum + (Number(event.gameCount) || 0), 0);
  const staticGames = staticInfo?.gameCount ?? 0;
  if (!staticGames) requestBulkPlayerDetail(player);
  const bulkInfo = bulkPlayerCache.get(String(player.fideID));
  const fallbackBulkGames = staticGames ? 0 : (bulkInfo?.totalGames ?? 0);
  const unifiedGames = staticGames || fallbackBulkGames;
  const eventMetric = staticInfo?.eventCount || events.length;
  const packageMetric = staticInfo?.packageCount || pgnEvents.length;
  if (staticInfo?.pgnPath) requestPGNViewer(player, staticInfo);

  els.detailPane.innerHTML = `
    <div class="detail-title">
      <div>
        <h2>${escapeHTML(displayName(player))}</h2>
        ${detailChineseNameLine(player)}
        <p>FIDE ${escapeHTML(player.fideID)} · ${player.birthYear} 出生 · ${stage?.id ?? "未到 U8"}</p>
      </div>
      <div class="detail-title-actions">
        <span class="stage-chip">${escapeHTML(stage?.id ?? "-")}</span>
        <a class="action-link" href="https://ratings.fide.com/profile/${encodeURIComponent(player.fideID)}" target="_blank" rel="noreferrer">FIDE 主页</a>
      </div>
    </div>

    ${note ? `<span class="note-pill">${escapeHTML(note)}</span>` : ""}

    <div class="rating-grid">
      ${ratingCard("STD", player.standard)}
      ${ratingCard("RAP", player.rapid)}
      ${ratingCard("BLZ", player.blitz)}
    </div>

    <div class="dashboard-grid">
      ${metricTile("赛事", eventMetric)}
      ${metricTile("PGN包", packageMetric)}
      ${metricTile("棋局", totalGames)}
    </div>

    ${staticInfo?.gameCount ? staticPlayerHitBlock(staticInfo) : ""}
    ${!staticInfo?.gameCount && bulkInfo?.totalGames ? bulkPlayerHitBlock(bulkInfo) : ""}

    <div class="stage-strip">
      ${stages.map(stageItem => stageTile(player, stageItem, staticInfo)).join("")}
    </div>

    <div class="detail-actions">
      <button class="primary-action" type="button" id="downloadSelectedPGN" ${selectedEvents.length ? "" : "disabled"}>↓ 下载选中 PGN</button>
      <button class="tool-button" type="button" id="downloadStaticPlayerPGN" ${unifiedGames ? "" : "disabled"}>下载全部棋局 PGN</button>
      <button class="tool-button" type="button" id="selectAllPGN" ${pgnEvents.length ? "" : "disabled"}>全选 PGN</button>
      <button class="tool-button" type="button" id="clearPGNSelection" ${selectedEvents.length ? "" : "disabled"}>清空</button>
    </div>

    <div class="download-status" aria-live="polite">${escapeHTML(downloadLine(selectedEvents, pgnEvents, unifiedGames))}</div>

    ${pgnViewerBlock(player, staticInfo)}

    <div class="event-list">
      ${events.length ? events.map(eventRow).join("") : `<div class="event-row"><strong>暂无本地赛事种子</strong><span>macOS 版可继续联网补齐 Chess-Results 和 PGN 缓存。</span></div>`}
    </div>
  `;

  wireDetailActions(player, pgnEvents);
  wirePGNViewerActions(player);
  mountLichessViewer(player);
}

function selectedPlayer() {
  const fideID = state.selectedFideID;
  return detailCache.get(fideID) ?? players.find(item => item.fideID === fideID);
}

function requestPlayerDetail(player) {
  if (!player?.detailPath || detailCache.has(player.fideID) || detailRequests.has(player.fideID)) return;

  const request = fetch(player.detailPath, { cache: "no-store" })
    .then(response => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    })
    .then(detail => {
      const prepared = preparePlayer({ ...player, ...detail });
      detailCache.set(prepared.fideID, prepared);
      const index = players.findIndex(item => item.fideID === prepared.fideID);
      if (index >= 0) {
        players[index] = prepared;
      } else {
        players.push(prepared);
      }
      if (state.selectedFideID === prepared.fideID) {
        resetSelectedEvents();
        render();
      }
    })
    .catch(error => {
      state.downloadStatus = `棋手明细加载失败：${error.message}`;
      renderDetail();
    })
    .finally(() => {
      detailRequests.delete(player.fideID);
    });

  detailRequests.set(player.fideID, request);
}

function requestStaticPlayerDetail(player) {
  const fideID = String(player?.fideID ?? "");
  if (!fideID || !player.playerIndexPath || staticPlayerCache.has(fideID) || staticPlayerRequests.has(fideID)) return;

  const request = fetch(player.playerIndexPath, { cache: "no-store" })
    .then(response => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    })
    .then(detail => {
      staticPlayerCache.set(fideID, detail);
      if (state.selectedFideID === fideID) renderDetail();
    })
    .catch(error => {
      state.downloadStatus = `全部棋局 PGN 索引加载失败：${error.message}`;
      if (state.selectedFideID === fideID) renderDetail();
    })
    .finally(() => {
      staticPlayerRequests.delete(fideID);
    });

  staticPlayerRequests.set(fideID, request);
}

function staticPlayerInfo(player) {
  const fideID = String(player?.fideID ?? "");
  const detail = staticPlayerCache.get(fideID);
  if (detail) {
    const allPackage = (detail.packages ?? []).find(item => item.id === "all") ?? detail.packages?.[0];
    return {
      gameCount: detail.totals?.games ?? allPackage?.gameCount ?? 0,
      eventCount: detail.totals?.events ?? detail.events?.length ?? 0,
      packageCount: detail.totals?.packages ?? detail.packages?.length ?? 0,
      pgnPath: allPackage?.pgnPath,
      packages: detail.packages ?? [],
      events: detail.events ?? [],
      stages: detail.totals?.stages ?? {},
      sources: allPackage?.sources ?? detail.sources ?? []
    };
  }
  if (player?.playerPgnPath) {
    return {
      gameCount: Number(player.playerPgnGameCount ?? player.gameCount ?? 0),
      eventCount: Number(player.eventCount ?? player.events?.length ?? 0),
      packageCount: Number(player.packageCount ?? 1),
      pgnPath: player.playerPgnPath,
      packages: [
        {
          id: "all",
          label: "全部棋局 PGN",
          pgnPath: player.playerPgnPath,
          gameCount: Number(player.playerPgnGameCount ?? player.gameCount ?? 0),
          stages: player.stages ?? {},
          sources: player.sources ?? []
        }
      ],
      events: player.events ?? [],
      stages: player.stages ?? {},
      sources: player.sources ?? []
    };
  }
  return null;
}

function requestBulkPlayerDetail(player) {
  const fideID = String(player?.fideID ?? "");
  const manifest = data.bulkYouthManifest;
  if (!fideID || !manifest?.stages?.length || bulkPlayerCache.has(fideID) || bulkPlayerRequests.has(fideID)) return;

  const request = Promise.all(manifest.stages.map(async stage => {
    const index = await loadBulkStageIndex(stage);
    const games = index.filter(game => String(game.fideID) === fideID);
    return {
      id: stage.id,
      games,
      count: games.length,
      pgnPath: stage.pgnPath,
      indexPath: stage.indexPath
    };
  }))
    .then(stageHits => {
      const hits = stageHits.filter(stage => stage.count > 0);
      bulkPlayerCache.set(fideID, {
        fideID,
        totalGames: hits.reduce((sum, stage) => sum + stage.count, 0),
        stages: hits
      });
      if (state.selectedFideID === fideID) renderDetail();
    })
    .catch(error => {
      state.downloadStatus = `bulk 青少年索引加载失败：${error.message}`;
      if (state.selectedFideID === fideID) renderDetail();
    })
    .finally(() => {
      bulkPlayerRequests.delete(fideID);
    });

  bulkPlayerRequests.set(fideID, request);
}

async function loadBulkStageIndex(stage) {
  if (bulkStageIndexCache.has(stage.id)) return bulkStageIndexCache.get(stage.id);
  const response = await fetch(stage.indexPath, { cache: "no-store" });
  if (!response.ok) throw new Error(`${stage.id} HTTP ${response.status}`);
  const index = await response.json();
  bulkStageIndexCache.set(stage.id, index);
  return index;
}

function bulkPlayerHitBlock(info) {
  return `
    <div class="bulk-player-hit">
      <strong>本地青少年 bulk 命中 ${compactNumber(info.totalGames)} 盘</strong>
      <span>${info.stages.map(stage => `${stage.id} ${stage.count} 盘`).join(" · ")}</span>
    </div>
  `;
}

function staticPlayerHitBlock(info) {
  const stageLine = Object.entries(info.stages ?? {})
    .map(([stage, count]) => `${stage} ${count} 盘`)
    .join(" · ");
  const packageLinks = (info.packages ?? [])
    .filter(item => item.pgnPath)
    .map(item => `<a href="${escapeAttribute(item.pgnPath)}" download>${escapeHTML(packageDisplayLabel(item))} ${compactNumber(item.gameCount)} 盘</a>`)
    .join("");
  return `
    <div class="static-player-hit">
      <div>
        <strong>查询到该棋手在中国青少年棋手库中棋局数量：${compactNumber(info.gameCount)} 盘</strong>
        <span>${escapeHTML(stageLine || (info.sources ?? []).join(" · ") || "按棋手聚合静态包")}</span>
      </div>
      <div class="static-package-links">${packageLinks}</div>
    </div>
  `;
}

function packageDisplayLabel(item) {
  const label = item?.label ?? item?.id ?? "PGN";
  if (item?.id === "all" || normalize(label) === "全部pgn") return "全部棋局 PGN";
  return label;
}

function requestPGNViewer(player, info) {
  const fideID = String(player?.fideID ?? "");
  const pgnPath = info?.pgnPath;
  if (!fideID || !pgnPath) return;

  if (state.viewer.fideID !== fideID || state.viewer.pgnPath !== pgnPath) {
    state.viewer = {
      fideID,
      pgnPath,
      status: pgnViewerCache.has(pgnPath) ? "loaded" : "idle",
      gameIndex: 0,
      orientation: "",
      error: ""
    };
  }

  const cached = pgnViewerCache.get(pgnPath);
  if (cached) {
    state.viewer.status = "loaded";
    state.viewer.gameIndex = clampInt(state.viewer.gameIndex, 0, Math.max(cached.games.length - 1, 0));
    const game = cached.games[state.viewer.gameIndex];
    state.viewer.orientation = state.viewer.orientation || preferredBoardOrientation(player, game);
    return;
  }

  if (pgnViewerRequests.has(pgnPath)) return;

  state.viewer.status = "loading";
  state.viewer.error = "";
  const request = fetch(pgnPath, { cache: "no-store" })
    .then(response => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.text();
    })
    .then(text => {
      const games = splitPGNGames(text).map((pgn, index) => ({
        index,
        pgn,
        headers: parsePGNHeaders(pgn),
        parsed: null
      }));
      if (!games.length) throw new Error("PGN 中没有可解析对局");
      pgnViewerCache.set(pgnPath, {
        pgnPath,
        games,
        gameCount: games.length,
        bytes: text.length
      });
      if (state.selectedFideID === fideID && state.viewer.pgnPath === pgnPath) {
        state.viewer.status = "loaded";
        state.viewer.gameIndex = 0;
        state.viewer.orientation = preferredBoardOrientation(player, games[0]);
        renderDetail();
      }
    })
    .catch(error => {
      if (state.selectedFideID === fideID && state.viewer.pgnPath === pgnPath) {
        state.viewer.status = "error";
        state.viewer.error = error.message;
        renderDetail();
      }
    })
    .finally(() => {
      pgnViewerRequests.delete(pgnPath);
    });

  pgnViewerRequests.set(pgnPath, request);
}

function pgnViewerBlock(player, info) {
  if (!info?.pgnPath) {
    return `
      <section class="pgn-viewer is-empty" aria-label="Lichess 在线棋盘">
        <div class="pgn-viewer-head">
          <div>
            <h3>Lichess 在线棋盘</h3>
            <span>暂无可播放 PGN</span>
          </div>
        </div>
      </section>
    `;
  }

  const viewer = state.viewer;
  const cached = pgnViewerCache.get(info.pgnPath);
  if (viewer.status === "error") {
    return `
      <section class="pgn-viewer is-empty" aria-label="Lichess 在线棋盘">
        <div class="pgn-viewer-head">
          <div>
            <h3>Lichess 在线棋盘</h3>
            <span>${escapeHTML(viewer.error || "PGN 加载失败")}</span>
          </div>
        </div>
      </section>
    `;
  }

  if (!cached || viewer.status === "loading") {
    return `
      <section class="pgn-viewer is-loading" aria-label="Lichess 在线棋盘">
        <div class="pgn-viewer-head">
          <div>
            <h3>Lichess 在线棋盘</h3>
            <span>正在载入 ${compactNumber(info.gameCount ?? 0)} 盘棋</span>
          </div>
          <div class="viewer-pulse" aria-hidden="true"></div>
        </div>
      </section>
    `;
  }

  const games = cached.games;
  const gameIndex = clampInt(viewer.gameIndex, 0, games.length - 1);
  const game = games[gameIndex];
  const selectOptions = games.map((item, index) => `
    <option value="${index}" ${index === gameIndex ? "selected" : ""}>${escapeHTML(viewerGameTitle(item, index))}</option>
  `).join("");
  const white = game.headers.White ?? "白方";
  const black = game.headers.Black ?? "黑方";
  const result = game.headers.Result ?? "*";

  return `
    <section class="pgn-viewer" aria-label="Lichess 在线棋盘">
      <div class="pgn-viewer-head">
        <div>
          <h3>Lichess 在线棋盘</h3>
          <span>${compactNumber(games.length)} 盘 · ${escapeHTML(white)} - ${escapeHTML(black)} · ${escapeHTML(result)}</span>
        </div>
        <button class="tool-button viewer-flip" type="button" id="viewerFlip" title="翻转棋盘" aria-label="翻转棋盘">↕</button>
      </div>

      <label class="viewer-select">
        <span>对局</span>
        <select id="viewerGameSelect">${selectOptions}</select>
      </label>

      <div class="lichess-viewer-shell">
        <div id="lichessPgnViewer" class="lichess-viewer-host" data-viewer-ready="false"></div>
      </div>
    </section>
  `;
}

function wirePGNViewerActions(player) {
  const viewer = state.viewer;
  const cached = pgnViewerCache.get(viewer.pgnPath);
  if (!cached) return;

  document.querySelector("#viewerGameSelect")?.addEventListener("change", event => {
    const gameIndex = clampInt(Number(event.target.value), 0, cached.games.length - 1);
    state.viewer.gameIndex = gameIndex;
    state.viewer.orientation = preferredBoardOrientation(player, cached.games[gameIndex]);
    renderDetail();
  });

  document.querySelector("#viewerFlip")?.addEventListener("click", () => {
    state.viewer.orientation = state.viewer.orientation === "black" ? "white" : "black";
    renderDetail();
  });
}

function mountLichessViewer(player) {
  const host = document.querySelector("#lichessPgnViewer");
  const cached = pgnViewerCache.get(state.viewer.pgnPath);
  if (!host || !cached?.games?.length) return;

  const gameIndex = clampInt(state.viewer.gameIndex, 0, cached.games.length - 1);
  const game = cached.games[gameIndex];
  const orientation = state.viewer.orientation || preferredBoardOrientation(player, game);
  state.viewer.orientation = orientation;

  try {
    LichessPgnViewer(host, {
      pgn: game.pgn,
      orientation,
      showPlayers: true,
      showMoves: "auto",
      showControls: true,
      scrollToMove: true,
      keyboardToMove: true,
      drawArrows: true,
      menu: {
        getPgn: { enabled: false },
        practiceWithComputer: { enabled: false },
        analysisBoard: { enabled: false }
      },
      lichess: "https://lichess.org"
    });
    host.dataset.viewerReady = "true";
  } catch (error) {
    host.innerHTML = `<div class="viewer-error">${escapeHTML(error.message)}</div>`;
  }
}

function viewerGameTitle(game, index) {
  const headers = game.headers;
  const date = headers.EventDate ?? headers.Date ?? "";
  const white = headers.White ?? "白方";
  const black = headers.Black ?? "黑方";
  const result = headers.Result ?? "*";
  return `${index + 1}. ${date ? `${date} · ` : ""}${white} - ${black} ${result}`;
}

function preferredBoardOrientation(player, game) {
  const blackName = normalize(game?.headers?.Black);
  if (!blackName) return "white";
  const names = [
    player.displayName,
    player.name,
    player.chineseName,
    player.pinyin,
    ...(player.aliases ?? [])
  ].map(normalize).filter(Boolean);
  if (names.some(name => blackName.includes(name) || name.includes(blackName))) {
    return "black";
  }
  return "white";
}

function ratingCard(label, value) {
  return `
    <div class="rating-card">
      <span>${label}</span>
      <strong>${value ?? "-"}</strong>
    </div>
  `;
}

function metricTile(title, value) {
  return `
    <div class="metric-tile">
      <span>${escapeHTML(title)}</span>
      <strong>${escapeHTML(value)}</strong>
    </div>
  `;
}

function stageTile(player, stage, staticInfo) {
  const age = data.competitionYear - player.birthYear;
  const status = age > stage.upperAge ? "已完成" : age >= stage.lowerAge ? "进行中" : "未完待续";
  const events = (player.events ?? []).filter(event => stageForEvent(player, event)?.id === stage.id);
  const bestRank = events
    .map(event => Number(event.rank))
    .filter(rank => Number.isFinite(rank) && rank > 0)
    .sort((a, b) => a - b)[0];
  const stageGames = Number(staticInfo?.stages?.[stage.id] ?? 0);
  const active = stageForPlayer(player)?.id === stage.id;
  return `
    <div class="stage-tile ${active ? "active" : ""}">
      <strong>${escapeHTML(stage.id)}</strong>
      <span>${escapeHTML(status)}</span>
      <small>${stageGames ? `${compactNumber(stageGames)} 盘` : bestRank ? `最好第 ${bestRank}` : "未收录"}</small>
    </div>
  `;
}

function eventRow(event) {
  const rank = event.rank ? `第 ${event.rank}` : "-";
  const size = event.rounds && event.participants ? `${event.rounds} 轮 · ${event.participants} 人` : "";
  const key = eventKey(event);
  const hasPGN = Boolean(event.pgnPath);
  const checked = state.selectedEventIDs.has(key);
  return `
    <label class="event-row ${hasPGN ? "has-pgn" : ""}">
      <input class="event-check" type="checkbox" data-event-id="${escapeAttribute(key)}" ${checked ? "checked" : ""} ${hasPGN ? "" : "disabled"}>
      <span class="event-copy">
        <strong>${escapeHTML(event.name)}</strong>
        <span>${escapeHTML(event.date ?? "未知日期")} · ${escapeHTML(rank)} · ${escapeHTML(size)}</span>
        <em>${hasPGN ? `${event.gameCount ?? "?"} 盘 PGN 已缓存` : "暂无静态 PGN"}</em>
      </span>
    </label>
  `;
}

function wireDetailActions(player, pgnEvents) {
  document.querySelector("#downloadSelectedPGN")?.addEventListener("click", () => {
    downloadSelectedPGN(player).catch(error => {
      state.downloadStatus = `PGN 下载失败：${error.message}`;
      renderDetail();
    });
  });
  document.querySelector("#selectAllPGN")?.addEventListener("click", () => {
    state.selectedEventIDs = new Set(pgnEvents.map(eventKey));
    state.downloadStatus = "";
    renderDetail();
  });
  document.querySelector("#clearPGNSelection")?.addEventListener("click", () => {
    state.selectedEventIDs = new Set();
    state.downloadStatus = "";
    renderDetail();
  });
  document.querySelector("#downloadStaticPlayerPGN")?.addEventListener("click", () => {
    downloadStaticPlayerPGN(player).catch(error => {
      state.downloadStatus = `全部棋局 PGN 下载失败：${error.message}`;
      renderDetail();
    });
  });
  document.querySelectorAll(".event-check").forEach(input => {
    input.addEventListener("change", () => {
      if (input.checked) {
        state.selectedEventIDs.add(input.dataset.eventId);
      } else {
        state.selectedEventIDs.delete(input.dataset.eventId);
      }
      state.downloadStatus = "";
      renderDetail();
    });
  });
}

async function downloadStaticPlayerPGN(player) {
  const info = staticPlayerInfo(player);
  if (info?.pgnPath) {
    state.downloadStatus = `正在下载 ${info.gameCount} 盘全部棋局 PGN...`;
    renderDetail();
    const response = await fetch(info.pgnPath, { cache: "no-store" });
    if (!response.ok) throw new Error(`全部棋局 PGN HTTP ${response.status}`);
    const text = await response.text();
    triggerDownload(`${slug(displayName(player))}-all.pgn`, text, "application/x-chess-pgn;charset=utf-8");
    state.downloadStatus = `已下载 ${countPGNGames(text)} 盘全部棋局 PGN。`;
    renderDetail();
    return;
  }
  await downloadBulkPlayerPGN(player);
}

async function downloadBulkPlayerPGN(player) {
  const fideID = String(player?.fideID ?? "");
  if (!fideID) return;
  if (!bulkPlayerCache.has(fideID)) {
    requestBulkPlayerDetail(player);
    await bulkPlayerRequests.get(fideID);
  }
  const info = bulkPlayerCache.get(fideID);
  if (!info?.totalGames) {
    state.downloadStatus = "本地 bulk 青少年包未命中该棋手。";
    renderDetail();
    return;
  }

  state.downloadStatus = `正在从本地 bulk 包提取 ${info.totalGames} 盘棋...`;
  renderDetail();

  const sections = [
    `% Extracted from local bulk youth PGN`,
    `% Player: ${displayName(player)}`,
    `% FIDE: ${fideID}`,
    `% Source: ${data.bulkYouthManifest.source}`,
    `% License: ${data.bulkYouthManifest.license}`,
    `% Created: ${new Date().toISOString()}`,
    ``
  ];
  let gameCount = 0;
  const seen = new Set();

  for (const stage of info.stages) {
    const response = await fetch(stage.pgnPath, { cache: "no-store" });
    if (!response.ok) throw new Error(`${stage.id} PGN HTTP ${response.status}`);
    const text = await response.text();
    const games = gamesForBulkEntries(text, stage.games);
    for (const game of games) {
      const key = normalize(game);
      if (seen.has(key)) continue;
      seen.add(key);
      sections.push(`% BulkStage: ${stage.id}\n\n${game}\n`);
      gameCount += 1;
    }
  }

  const merged = sections.join("\n").trim() + "\n";
  triggerDownload(`${slug(displayName(player))}-bulk-youth.pgn`, merged, "application/x-chess-pgn;charset=utf-8");
  state.downloadStatus = `已从本地 bulk 包提取 ${gameCount} 盘棋。`;
  renderDetail();
}

function gamesForBulkEntries(pgnText, entries) {
  const games = splitPGNGames(pgnText);
  const byKey = new Map();
  for (const game of games) {
    const headers = parsePGNHeaders(game);
    const key = bulkGameKey(headers);
    if (!byKey.has(key)) byKey.set(key, []);
    byKey.get(key).push(game);
  }

  const matches = [];
  for (const entry of entries) {
    const key = bulkEntryKey(entry);
    const exact = byKey.get(key)?.shift();
    if (exact) {
      matches.push(exact);
      continue;
    }
    const loose = games.find(game => looseBulkMatch(parsePGNHeaders(game), entry));
    if (loose) matches.push(loose);
  }
  return matches;
}

async function downloadSelectedPGN(player) {
  const events = (player.events ?? [])
    .filter(event => event.pgnPath && state.selectedEventIDs.has(eventKey(event)));
  if (!events.length) {
    state.downloadStatus = "没有选中的可下载 PGN。";
    renderDetail();
    return;
  }

  state.downloadStatus = `正在合并 ${events.length} 个 PGN...`;
  renderDetail();

  const parts = [];
  let gameCount = 0;
  for (const event of events) {
    const response = await fetch(event.pgnPath, { cache: "no-store" });
    if (!response.ok) throw new Error(`${event.name} HTTP ${response.status}`);
    const text = await response.text();
    parts.push(text.trim());
    gameCount += countPGNGames(text);
  }

  const merged = parts.filter(Boolean).join("\n\n") + "\n";
  const fileName = `${slug(displayName(player))}-${data.competitionYear}-merged.pgn`;
  triggerDownload(fileName, merged, "application/x-chess-pgn;charset=utf-8");
  state.downloadStatus = `已生成 ${events.length} 个赛事、${gameCount} 盘棋的合并 PGN。`;
  renderDetail();
}

function downloadLine(selectedEvents, pgnEvents, unifiedGames) {
  if (state.downloadStatus) return state.downloadStatus;
  if (!pgnEvents.length && unifiedGames) return `查询到该棋手在中国青少年棋手库中棋局数量：${compactNumber(unifiedGames)} 盘，可下载或在线播放。`;
  if (!pgnEvents.length) return "当前棋手暂无静态 PGN。GitHub Pages 只能下载仓库内已归档的 PGN。";
  return `已选择 ${selectedEvents.length}/${pgnEvents.length} 个可下载赛事。`;
}

function triggerDownload(fileName, text, type) {
  const blob = new Blob([text], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function countPGNGames(text) {
  return (text.match(/^\[Event\s+"/gim) ?? []).length;
}

function splitPGNGames(text) {
  return String(text ?? "")
    .replace(/\r\n/g, "\n")
    .split(/\n(?=\[Event\s+")/g)
    .map(game => game.trim())
    .filter(game => /^\[Event\s+"/i.test(game));
}

function parsePGNHeaders(game) {
  const headers = {};
  const pattern = /^\[([A-Za-z0-9_]+)\s+"((?:\\.|[^"])*)"\]/gm;
  let match;
  while ((match = pattern.exec(game)) !== null) {
    headers[match[1]] = match[2].replace(/\\"/g, '"');
  }
  return headers;
}

function bulkEntryKey(entry) {
  return [
    normalize(entry.event),
    dateKey(entry.date),
    normalize(entry.white),
    normalize(entry.black),
    normalize(entry.result)
  ].join("|");
}

function bulkGameKey(headers) {
  return [
    normalize(headers.Event),
    dateKey(headers.Date),
    normalize(headers.White),
    normalize(headers.Black),
    normalize(headers.Result)
  ].join("|");
}

function looseBulkMatch(headers, entry) {
  return normalize(headers.Event) === normalize(entry.event)
    && dateKey(headers.Date) === dateKey(entry.date)
    && normalize(headers.White).includes(normalize(entry.white))
    && normalize(headers.Black).includes(normalize(entry.black));
}

function eventKey(event) {
  return event.id ?? `${event.source ?? "event"}:${event.tournamentID ?? event.name}:${event.date ?? ""}`;
}

function resetSelectedEvents() {
  const player = selectedPlayer();
  state.selectedEventIDs = new Set((player?.events ?? []).filter(event => event.pgnPath).map(eventKey));
}

function resetPGNViewer(fideID) {
  state.viewer = {
    fideID: String(fideID ?? ""),
    pgnPath: "",
    status: "idle",
    gameIndex: 0,
    orientation: "",
    error: ""
  };
}

function selectPlayer(fideID) {
  if (state.selectedFideID !== fideID) resetPGNViewer(fideID);
  state.selectedFideID = fideID;
  state.downloadStatus = "";
  resetSelectedEvents();
  renderDetail();
}

function rankingsForStage(stageID) {
  return players
    .filter(player => stageID === "ALL" ? Boolean(stageForPlayer(player)) : stageForPlayer(player)?.id === stageID)
    .map(player => ({ player, rating: ratingForPlayer(player) }))
    .filter(entry => entry.rating)
    .sort((a, b) => {
      if (a.rating.value !== b.rating.value) return b.rating.value - a.rating.value;
      if (a.rating.priority !== b.rating.priority) return a.rating.priority - b.rating.priority;
      return displayName(a.player).localeCompare(displayName(b.player), "zh-Hans-CN");
    })
    .slice(0, 20)
    .map(entry => ({ ...entry, fideID: entry.player.fideID }));
}

function ratingForPlayer(player) {
  if (Number.isFinite(player.standard)) return { value: player.standard, kind: "STD", priority: 0 };
  if (Number.isFinite(player.rapid)) return { value: player.rapid, kind: "RAP", priority: 1 };
  if (Number.isFinite(player.blitz)) return { value: player.blitz, kind: "BLZ", priority: 2 };
  return null;
}

function stageForPlayer(player) {
  const age = data.competitionYear - player.birthYear;
  return stages.find(stage => age >= stage.lowerAge && age <= stage.upperAge) ?? null;
}

function stageForEvent(player, event) {
  if (!event.date) return null;
  const year = Number(event.date.slice(0, 4));
  if (!Number.isFinite(year)) return null;
  const age = year - player.birthYear;
  return stages.find(stage => age >= stage.lowerAge && age <= stage.upperAge) ?? null;
}

function liChengzhiNote(player, stageID) {
  const event = (player.events ?? [])
    .filter(item => Number(item.rank) <= 3)
    .filter(item => item.kind === "li-chengzhi" || normalize(item.name).includes("lichengzhi") || normalize(item.name).includes("nationalyouthchesschampionship"))
    .filter(item => stageForEvent(player, item)?.id === stageID)
    .sort((a, b) => Number(a.rank) - Number(b.rank))[0];

  return event ? `李成智杯第 ${event.rank}` : null;
}

function searchPlayers(query) {
  const normalized = normalize(query);
  if (!normalized) return [];
  return players
    .filter(player => player.searchIndex.some(value => value.includes(normalized)))
    .sort((a, b) => {
      const stageA = stageForPlayer(a)?.id ?? "";
      const stageB = stageForPlayer(b)?.id ?? "";
      if (stageA !== stageB) return stageA.localeCompare(stageB);
      return (ratingForPlayer(b)?.value ?? 0) - (ratingForPlayer(a)?.value ?? 0);
    })
    .slice(0, 12);
}

function preparePlayer(player) {
  const values = [
    player.fideID,
    player.displayName,
    player.name,
    player.chineseName,
    player.pinyin,
    ...(player.aliases ?? [])
  ].filter(Boolean);
  return {
    ...player,
    searchIndex: [...new Set(values.map(normalize))]
  };
}

function displayName(player) {
  if (player.chineseName && player.name && player.chineseName !== player.name) {
    return `${player.chineseName} · ${player.name}`;
  }
  return player.displayName ?? player.name ?? player.chineseName ?? `FIDE ${player.fideID}`;
}

function detailChineseNameLine(player) {
  if (!player.chineseName || displayName(player).includes(player.chineseName)) return "";
  return `<div class="detail-cn-name">${escapeHTML(player.chineseName)}</div>`;
}

function ageRuleText() {
  const ranges = stages
    .map(stage => `${stage.id}=${stage.birthYears} 出生`)
    .join(" · ");
  return `${data.ageRule.title}：${data.ageRule.description}${data.competitionYear} 年口径为 ${ranges}。`;
}

function compactNumber(value) {
  const number = Number(value) || 0;
  if (number >= 1000000) return `${(number / 1000000).toFixed(number >= 10000000 ? 0 : 1)}M`;
  if (number >= 10000) return `${(number / 10000).toFixed(number >= 100000 ? 0 : 1)}万`;
  return String(number);
}

function clampInt(value, min, max) {
  const number = Number.isFinite(value) ? Math.trunc(value) : min;
  return Math.min(Math.max(number, min), max);
}

function normalize(value) {
  return String(value ?? "")
    .normalize("NFKD")
    .toLowerCase()
    .replace(/[\s,.'’"()，。·_\-]+/g, "");
}

function dateKey(value) {
  return String(value ?? "").replace(/\D+/g, "");
}

function slug(value) {
  const normalized = normalize(value).replace(/[^a-z0-9]+/g, "-");
  return normalized || "player";
}

function escapeHTML(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttribute(value) {
  return escapeHTML(value).replaceAll("`", "&#096;");
}
