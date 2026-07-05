import LichessPgnViewer from "./vendor/lichess-pgn-viewer/lichess-pgn-viewer.min.js";

const state = {
  activeStage: "ALL",
  selectedFideID: null,
  downloadStatus: "",
  query: "",
  viewer: {
    fideID: "",
    pgnPath: "",
    packageId: "",
    packageLabel: "",
    packageGameCount: 0,
    visible: false,
    status: "idle",
    gameIndex: 0,
    orientation: "",
    error: "",
    autoplay: false
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
  rankingMeta: document.querySelector("#rankingMeta"),
  leaderboards: document.querySelector(".leaderboards")
};

const data = await loadData();
const stages = data.ageRule.stages;
const players = data.players.map(preparePlayer);
const detailCache = new Map();
const detailRequests = new Map();
const staticPlayerCache = new Map();
const staticPlayerRequests = new Map();
const bulkStageIndexCache = new Map();
const bulkStageIndexRequests = new Map();
const bulkPlayerCache = new Map();
const bulkPlayerRequests = new Map();
const pgnViewerCache = new Map();
const pgnViewerRequests = new Map();
const PGN_VIEWER_CACHE_MAX_ENTRIES = 3;
const PGN_VIEWER_CACHE_MAX_BYTES = 48 * 1024 * 1024;
let activeLichessViewer = null;
let viewerAutoplayTimer = null;

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
  const response = await fetch(path, { cache: "default" });
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
      aliases: uniqueStrings([...(current.aliases ?? []), ...(indexed.aliases ?? [])]),
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
      aliases: uniqueStrings([...(current.aliases ?? []), ...(player.aliases ?? [])]),
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
      aliases: uniqueStrings([...(current.aliases ?? []), ...(player.aliases ?? [])]),
      eventCount: Math.max(Number(current.eventCount) || 0, Number(player.eventCount) || 0),
      gameCount: Math.max(Number(current.gameCount) || 0, Number(player.gameCount) || 0),
      playerPgnPath: player.playerPgnPath ?? current.playerPgnPath,
      playerPgnGameCount: player.playerPgnGameCount ?? current.playerPgnGameCount,
      playerIndexPath: player.playerIndexPath ?? current.playerIndexPath,
      stages: { ...(current.stages ?? {}), ...(player.stages ?? {}) },
      sources: uniqueStrings([...(current.sources ?? []), ...(player.sources ?? [])]),
      displayName: current.displayName ?? player.displayName,
      name: current.name ?? player.name ?? current.displayName ?? player.displayName ?? `FIDE ${fideID}`,
      chineseName: current.chineseName ?? player.chineseName,
      pinyin: current.pinyin ?? player.pinyin
    });
  });

  return [...byFide.values()];
}

function initialize() {
  const routedFideID = initialSelectedFideID();
  state.selectedFideID = players.some(player => player.fideID === routedFideID)
    ? routedFideID
    : rankingsForStage("ALL")[0]?.fideID ?? players[0]?.fideID ?? null;
  els.searchInput.addEventListener("input", event => {
    state.query = event.target.value.trim();
    renderSearch();
  });
  document.addEventListener("keydown", handleViewerKeyboard);

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
  const rows = [tabs.slice(0, 4), tabs.slice(4)];
  els.stageTabs.innerHTML = rows.map(row => `
    <div class="stage-tab-row">
      ${row.map(tab => `
        <button type="button" role="tab" aria-selected="${state.activeStage === tab.id}" data-stage="${escapeAttribute(tab.id)}">
          ${escapeHTML(tab.label)}
        </button>
      `).join("")}
    </div>
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
          <div class="player-meta">${escapeHTML(playerStage?.id ?? "-")} · FIDE ${escapeHTML(player.fideID)} · ${escapeHTML(displayText(player.birthYear ?? "-"))} 出生</div>
          ${note ? `<span class="note-pill">${escapeHTML(note)}</span>` : ""}
          <div class="bar-track" aria-hidden="true"><div class="bar-fill" style="--bar-width: ${width}%"></div></div>
        </td>
        <td class="rating-cell">
          <div class="rating-value">${escapeHTML(displayText(entry.rating.value))}</div>
          <div class="rating-kind">${escapeHTML(displayText(entry.rating.kind))}</div>
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
  const hasQuery = state.query.length > 0;
  els.searchResultsSection.hidden = !hasQuery;
  els.leaderboards.hidden = hasQuery;
  els.searchCount.textContent = `${matches.length} 名`;
  els.searchResults.innerHTML = matches.length ? matches.map(player => {
    const stage = stageForPlayer(player);
    const rating = ratingForPlayer(player);
    return `
      <button class="result-button" type="button" data-fide="${escapeAttribute(player.fideID)}" aria-pressed="${state.selectedFideID === player.fideID}">
        <div class="player-name">${escapeHTML(displayName(player))}</div>
        <div class="player-meta">${escapeHTML(stage?.id ?? "-")} · FIDE ${escapeHTML(player.fideID)} · ${escapeHTML(displayText(rating?.value ?? "-"))} ${escapeHTML(displayText(rating?.kind ?? ""))}</div>
      </button>
    `;
  }).join("") : `
    <div class="empty-state compact">本地库暂未匹配到该棋手。可以试试 FIDE ID，或用姓 名拼音全称搜索。</div>
  `;

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
  const staticGames = staticInfo?.gameCount ?? 0;
  if (!staticGames) requestBulkPlayerDetail(player);
  const bulkInfo = bulkPlayerCache.get(String(player.fideID));
  if (state.viewer.visible && state.viewer.fideID === String(player.fideID) && state.viewer.pgnPath) {
    requestPGNViewer(player, state.viewer);
  }

  els.detailPane.innerHTML = `
    <div class="detail-title">
      <div>
        <h2>${escapeHTML(displayName(player))}</h2>
        ${detailChineseNameLine(player)}
        <p>FIDE ${escapeHTML(player.fideID)} · ${escapeHTML(displayText(player.birthYear ?? "-"))} 出生 · ${escapeHTML(stage?.id ?? "未到 U8")}</p>
      </div>
      <div class="detail-title-actions">
        <span class="stage-chip">${escapeHTML(stage?.id ?? "-")}</span>
        <a class="action-link mimic-action" href="${escapeAttribute(mimicGameHref(player))}">
          模拟对局
          <span class="mimic-mini-badge">AI</span>
          <span class="mimic-mini-badge is-beta">BETA</span>
        </a>
        <a class="action-link" href="https://ratings.fide.com/profile/${encodeURIComponent(player.fideID)}" target="_blank" rel="noreferrer">FIDE 主页</a>
      </div>
    </div>

    ${note ? `<span class="note-pill">${escapeHTML(note)}</span>` : ""}

    <div class="rating-grid">
      ${ratingCard("STANDARD", player.standard)}
      ${ratingCard("RAPID", player.rapid)}
      ${ratingCard("BLITZ", player.blitz)}
    </div>

    ${staticInfo?.gameCount ? staticPlayerHitBlock(player, staticInfo) : ""}
    ${!staticInfo?.gameCount && bulkInfo?.totalGames ? bulkPlayerHitBlock(bulkInfo) : ""}

    ${state.downloadStatus ? `<div class="download-status" aria-live="polite">${escapeHTML(state.downloadStatus)}</div>` : ""}

    ${pgnViewerBlock(player, staticInfo)}
  `;

  wireDetailActions(player, staticInfo);
  wirePGNViewerActions(player);
  mountLichessViewer(player);
}

function selectedPlayer() {
  const fideID = state.selectedFideID;
  return detailCache.get(fideID) ?? players.find(item => item.fideID === fideID);
}

function requestPlayerDetail(player) {
  if (!player?.detailPath || detailCache.has(player.fideID) || detailRequests.has(player.fideID)) return;

  const request = fetch(player.detailPath, { cache: "default" })
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

  const request = fetch(player.playerIndexPath, { cache: "default" })
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
  if (bulkStageIndexRequests.has(stage.id)) return bulkStageIndexRequests.get(stage.id);

  const request = fetch(stage.indexPath, { cache: "default" })
    .then(response => {
      if (!response.ok) throw new Error(`${stage.id} HTTP ${response.status}`);
      return response.json();
    })
    .then(index => {
      bulkStageIndexCache.set(stage.id, index);
      return index;
    })
    .finally(() => {
      bulkStageIndexRequests.delete(stage.id);
    });
  bulkStageIndexRequests.set(stage.id, request);
  return request;
}

function bulkPlayerHitBlock(info) {
  return `
    <div class="bulk-player-hit">
      <strong>本地青少年 bulk 命中 ${compactNumber(info.totalGames)} 盘</strong>
      <span>${escapeHTML(info.stages.map(stage => `${stage.id} ${stage.count} 盘`).join(" · "))}</span>
    </div>
  `;
}

function staticPlayerHitBlock(player, info) {
  const stageLine = Object.entries(info.stages ?? {})
    .map(([stage, count]) => `${stage} ${count} 盘`)
    .join(" · ");
  const packageButtons = pgnPackages(info)
    .filter(item => item.pgnPath)
    .map(item => `
      <button class="pgn-package-button" type="button" data-pgn-path="${escapeAttribute(item.pgnPath)}" aria-pressed="${isActiveViewerPackage(player, item)}">
        <strong>${escapeHTML(packageDisplayLabel(item))}</strong>
        <span>${compactNumber(item.gameCount)} 盘</span>
      </button>
    `)
    .join("");
  return `
    <div class="static-player-hit">
      <div>
        <strong>查询到该棋手在中国青少年棋手库中棋局数量：${compactNumber(info.gameCount)} 盘</strong>
        <span>${escapeHTML(stageLine || (info.sources ?? []).join(" · ") || "按棋手聚合静态包")}</span>
      </div>
      <div class="pgn-package-grid">${packageButtons}</div>
    </div>
  `;
}

function pgnPackages(info) {
  return (info?.packages ?? []).filter(item => item.pgnPath);
}

function packageDisplayLabel(item) {
  const label = item?.packageLabel ?? item?.label ?? item?.id ?? "PGN";
  if (item?.id === "all" || normalize(label) === "全部pgn") return "全部棋局 PGN";
  return label;
}

function packageShortLabel(item) {
  if (item?.id === "all" || item?.packageId === "all") return "全部棋局";
  return String(item?.id ?? packageDisplayLabel(item)).replace(/\s*PGN$/i, "");
}

function isActiveViewerPackage(player, item) {
  return state.viewer.visible
    && state.viewer.fideID === String(player.fideID)
    && state.viewer.pgnPath === item.pgnPath;
}

function requestPGNViewer(player, info) {
  const fideID = String(player?.fideID ?? "");
  const pgnPath = info?.pgnPath;
  if (!fideID || !pgnPath) return;

  if (state.viewer.fideID !== fideID || state.viewer.pgnPath !== pgnPath) {
    state.viewer = {
      ...state.viewer,
      fideID,
      pgnPath,
      status: getCachedPGNViewerPackage(pgnPath) ? "loaded" : "idle",
      visible: true,
      gameIndex: 0,
      orientation: "",
      error: "",
      autoplay: false
    };
  }

  const cached = getCachedPGNViewerPackage(pgnPath);
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
  const request = fetch(pgnPath, { cache: "default" })
    .then(response => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.text();
    })
    .then(text => {
      const games = splitPGNGames(text).map((rawPGN, index) => {
        const pgn = repairPGNText(rawPGN);
        return {
          index,
          pgn,
          headers: parsePGNHeaders(pgn),
          parsed: null
        };
      });
      if (!games.length) throw new Error("PGN 中没有可解析对局");
      setCachedPGNViewerPackage(pgnPath, {
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

function getCachedPGNViewerPackage(pgnPath) {
  const cached = pgnViewerCache.get(pgnPath);
  if (!cached) return null;
  pgnViewerCache.delete(pgnPath);
  pgnViewerCache.set(pgnPath, cached);
  return cached;
}

function setCachedPGNViewerPackage(pgnPath, cached) {
  pgnViewerCache.delete(pgnPath);
  pgnViewerCache.set(pgnPath, cached);
  prunePGNViewerCache(pgnPath);
}

function prunePGNViewerCache(activePath) {
  while (
    pgnViewerCache.size > PGN_VIEWER_CACHE_MAX_ENTRIES
    || pgnViewerCacheByteCount() > PGN_VIEWER_CACHE_MAX_BYTES
  ) {
    const evictable = [...pgnViewerCache.keys()].find(path => path !== activePath);
    if (!evictable) break;
    pgnViewerCache.delete(evictable);
  }
}

function pgnViewerCacheByteCount() {
  let total = 0;
  for (const cached of pgnViewerCache.values()) {
    total += Number(cached.bytes ?? 0);
  }
  return total;
}

function pgnViewerBlock(player, info) {
  if (!state.viewer.visible || state.viewer.fideID !== String(player.fideID) || !state.viewer.pgnPath) return "";

  const viewer = state.viewer;
  const selectedPackage = selectedViewerPackage(info);
  const packageLabel = selectedPackage ? packageShortLabel(selectedPackage) : viewer.packageLabel || "棋局";
  const packageGames = selectedPackage?.gameCount ?? viewer.packageGameCount ?? 0;
  const title = `${displayName(player)} ${packageCollectionLabel(selectedPackage ?? viewer)}`;
  const downloadText = `点击下载 ${displayName(player)} ${packageLabel} ${compactNumber(packageGames)}局 PGN`;
  const downloadName = `${slug(displayName(player))}-${slug(packageLabel)}.pgn`;
  const cached = getCachedPGNViewerPackage(viewer.pgnPath);
  if (viewer.status === "error") {
    return `
      <section class="pgn-viewer is-empty" aria-label="${escapeAttribute(title)}">
        <div class="pgn-viewer-head">
          <div>
            <h3>${escapeHTML(title)}</h3>
            <span>${escapeHTML(viewer.error || "PGN 加载失败")}</span>
          </div>
        </div>
      </section>
    `;
  }

  if (!cached || viewer.status === "loading") {
    return `
      <section class="pgn-viewer is-loading" aria-label="${escapeAttribute(title)}">
        <div class="pgn-viewer-head">
          <div>
            <h3>${escapeHTML(title)}</h3>
            <span>正在载入 ${compactNumber(packageGames)} 盘棋</span>
          </div>
          <div class="viewer-pulse" aria-hidden="true"></div>
        </div>
        <a class="pgn-download-link" href="${escapeAttribute(viewer.pgnPath)}" download="${escapeAttribute(downloadName)}">${escapeHTML(downloadText)}</a>
      </section>
    `;
  }

  const games = cached.games;
  const gameIndex = clampInt(viewer.gameIndex, 0, games.length - 1);
  const game = games[gameIndex];
  const selectOptions = games.map((item, index) => `
    <option value="${index}" ${index === gameIndex ? "selected" : ""}>${escapeHTML(viewerGameTitle(item, index))}</option>
  `).join("");
  const white = displayText(game.headers.White ?? "白方");
  const black = displayText(game.headers.Black ?? "黑方");
  const result = displayText(game.headers.Result ?? "*");
  const gameInfo = viewerGameInfo(game);

  return `
    <section class="pgn-viewer" aria-label="${escapeAttribute(title)}">
      <div class="pgn-viewer-head">
        <div>
          <h3>${escapeHTML(title)}</h3>
          <span>${compactNumber(games.length)} 盘 · ${escapeHTML(white)} - ${escapeHTML(black)} · ${escapeHTML(result)}</span>
        </div>
        <button class="tool-button viewer-flip" type="button" id="viewerFlip" title="翻转棋盘" aria-label="翻转棋盘">↕</button>
      </div>

      <a class="pgn-download-link" href="${escapeAttribute(viewer.pgnPath)}" download="${escapeAttribute(downloadName)}">${escapeHTML(downloadText)}</a>

      <label class="viewer-select">
        <span>对局</span>
        <select id="viewerGameSelect">${selectOptions}</select>
      </label>

      <div class="viewer-playback-layout">
        <div class="lichess-viewer-shell">
          <div id="lichessPgnViewer" class="lichess-viewer-host" data-viewer-ready="false"></div>
        </div>
        <aside class="viewer-game-side">
          <h4>棋局信息</h4>
          <dl>${gameInfo}</dl>
        </aside>
      </div>
    </section>
  `;
}

function viewerGameInfo(game) {
  const headers = game.headers ?? {};
  const items = [
    ["赛事", headers.Event],
    ["轮次", headers.Round],
    ["时间", headers.EventDate ?? headers.Date],
    ["地点", headers.Site],
    ["结果", headers.Result],
    ["白方", playerLine(headers, "White")],
    ["黑方", playerLine(headers, "Black")],
    ["ECO", headers.ECO],
    ["时限", headers.TimeControl]
  ].filter(([, value]) => displayText(value ?? "") && displayText(value ?? "") !== "?");

  return items.map(([label, value]) => `
    <div>
      <dt>${escapeHTML(label)}</dt>
      <dd>${escapeHTML(displayText(value))}</dd>
    </div>
  `).join("");
}

function playerLine(headers, side) {
  const name = displayText(headers[side] ?? "");
  const elo = displayText(headers[`${side}Elo`] ?? "");
  const title = displayText(headers[`${side}Title`] ?? "");
  const fed = displayText(headers[`${side}Fed`] ?? headers[`${side}Federation`] ?? "");
  return [title, name, elo ? elo : "", fed].filter(Boolean).join(" · ");
}

function selectedViewerPackage(info) {
  return pgnPackages(info).find(item => item.pgnPath === state.viewer.pgnPath) ?? null;
}

function packageCollectionLabel(item) {
  const label = packageShortLabel(item);
  return label === "全部棋局" ? "全部棋局合集" : `${label} 棋局合集`;
}

function wirePGNViewerActions(player) {
  const viewer = state.viewer;
  const cached = getCachedPGNViewerPackage(viewer.pgnPath);
  if (!cached) return;

  document.querySelector("#viewerGameSelect")?.addEventListener("change", event => {
    stopViewerAutoplay();
    const gameIndex = clampInt(Number(event.target.value), 0, cached.games.length - 1);
    state.viewer.gameIndex = gameIndex;
    state.viewer.orientation = preferredBoardOrientation(player, cached.games[gameIndex]);
    renderDetail();
  });

  document.querySelector("#viewerFlip")?.addEventListener("click", () => {
    stopViewerAutoplay();
    state.viewer.orientation = state.viewer.orientation === "black" ? "white" : "black";
    renderDetail();
  });
}

function mountLichessViewer(player) {
  const host = document.querySelector("#lichessPgnViewer");
  const cached = getCachedPGNViewerPackage(state.viewer.pgnPath);
  if (!host || !cached?.games?.length) return;

  const gameIndex = clampInt(state.viewer.gameIndex, 0, cached.games.length - 1);
  const game = cached.games[gameIndex];
  const orientation = state.viewer.orientation || preferredBoardOrientation(player, game);
  state.viewer.orientation = orientation;

  try {
    activeLichessViewer = LichessPgnViewer(host, {
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
    activeLichessViewer = null;
    host.innerHTML = `<div class="viewer-error">${escapeHTML(error.message)}</div>`;
  }
}

function handleViewerKeyboard(event) {
  if (!state.viewer.visible || !activeLichessViewer) return;
  if (!document.querySelector(".pgn-viewer")) return;
  if (isTypingTarget(event.target)) return;

  if (event.key === "ArrowLeft") {
    event.preventDefault();
    stopViewerAutoplay();
    activeLichessViewer.goTo("prev");
  } else if (event.key === "ArrowRight") {
    event.preventDefault();
    activeLichessViewer.goTo("next");
  } else if (event.key === " ") {
    event.preventDefault();
    toggleViewerAutoplay();
  }
}

function isTypingTarget(target) {
  return target instanceof HTMLInputElement
    || target instanceof HTMLTextAreaElement
    || target instanceof HTMLSelectElement
    || target?.isContentEditable;
}

function toggleViewerAutoplay() {
  if (viewerAutoplayTimer) {
    stopViewerAutoplay();
    return;
  }
  state.viewer.autoplay = true;
  viewerAutoplayTimer = window.setInterval(() => {
    activeLichessViewer?.goTo("next");
  }, 900);
}

function stopViewerAutoplay() {
  if (viewerAutoplayTimer) {
    window.clearInterval(viewerAutoplayTimer);
    viewerAutoplayTimer = null;
  }
  state.viewer.autoplay = false;
}

function viewerGameTitle(game, index) {
  const headers = game.headers;
  const date = displayText(headers.EventDate ?? headers.Date ?? "");
  const white = displayText(headers.White ?? "白方");
  const black = displayText(headers.Black ?? "黑方");
  const result = displayText(headers.Result ?? "*");
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
      <span>${escapeHTML(label)}</span>
      <strong>${escapeHTML(displayText(value ?? "-"))}</strong>
    </div>
  `;
}

function wireDetailActions(player, staticInfo) {
  const packages = pgnPackages(staticInfo);
  document.querySelectorAll("[data-pgn-path]").forEach(button => {
    button.addEventListener("click", () => {
      const item = packages.find(pkg => pkg.pgnPath === button.dataset.pgnPath);
      if (item) selectPGNPackage(player, item);
    });
  });
}

function selectPGNPackage(player, item) {
  stopViewerAutoplay();
  activeLichessViewer = null;
  state.viewer = {
    fideID: String(player.fideID),
    pgnPath: item.pgnPath,
    packageId: item.id ?? "",
    packageLabel: packageShortLabel(item),
    packageGameCount: Number(item.gameCount ?? 0),
    visible: true,
    status: getCachedPGNViewerPackage(item.pgnPath) ? "loaded" : "idle",
    gameIndex: 0,
    orientation: "",
    error: "",
    autoplay: false
  };
  state.downloadStatus = "";
  requestPGNViewer(player, state.viewer);
  renderDetail();
  scrollPGNViewerIntoView();
}

function scrollPGNViewerIntoView() {
  window.requestAnimationFrame(() => {
    document.querySelector(".pgn-viewer")?.scrollIntoView({
      block: "start",
      inline: "nearest",
      behavior: "smooth"
    });
  });
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
    headers[match[1]] = displayText(match[2].replace(/\\"/g, '"'));
  }
  return headers;
}

function repairPGNText(text) {
  return String(text ?? "").replace(/^\[([A-Za-z0-9_]+)\s+"((?:\\.|[^"])*)"\]/gm, (_line, tag, rawValue) => {
    const value = displayText(rawValue.replace(/\\"/g, '"'));
    return `[${tag} "${escapePGNHeaderValue(value)}"]`;
  });
}

function escapePGNHeaderValue(value) {
  return String(value ?? "").replace(/\\/g, "\\\\").replace(/"/g, '\\"');
}

function resetPGNViewer(fideID) {
  stopViewerAutoplay();
  activeLichessViewer = null;
  state.viewer = {
    fideID: String(fideID ?? ""),
    pgnPath: "",
    packageId: "",
    packageLabel: "",
    packageGameCount: 0,
    visible: false,
    status: "idle",
    gameIndex: 0,
    orientation: "",
    error: "",
    autoplay: false
  };
}

function selectPlayer(fideID) {
  if (state.selectedFideID !== fideID) resetPGNViewer(fideID);
  state.selectedFideID = fideID;
  state.downloadStatus = "";
  updateRouteFideID(fideID);
  renderDetail();
  if (state.query) renderSearch();
  scrollDetailIntoViewOnMobile();
}

function initialSelectedFideID() {
  const params = new URLSearchParams(window.location.search);
  return String(params.get("fideID") || params.get("fide") || "").replace(/\D/g, "");
}

function updateRouteFideID(fideID) {
  if (!window.history?.replaceState || !fideID) return;
  const url = new URL(window.location.href);
  url.searchParams.set("fideID", fideID);
  window.history.replaceState(null, "", url);
}

function scrollDetailIntoViewOnMobile() {
  if (!window.matchMedia("(max-width: 720px)").matches) return;
  window.requestAnimationFrame(() => {
    els.detailPane.scrollIntoView({
      block: "start",
      inline: "nearest",
      behavior: "smooth"
    });
  });
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
    .filter(item => {
      const rank = Number(item.rank);
      return Number.isInteger(rank) && rank > 0 && rank <= 3;
    })
    .filter(item => item.kind === "li-chengzhi" || normalize(item.name).includes("lichengzhi") || normalize(item.name).includes("nationalyouthchesschampionship"))
    .filter(item => stageForEvent(player, item)?.id === stageID)
    .sort((a, b) => Number(a.rank) - Number(b.rank))[0];

  return event ? `李成智杯第 ${event.rank}` : null;
}

function searchPlayers(query) {
  const normalized = normalize(query);
  const tokens = searchTokens(query);
  const reversed = tokens.length > 1 ? tokens.slice().reverse().join("") : "";
  if (!normalized) return [];
  return players
    .map(player => ({ player, score: searchScore(player, normalized, tokens, reversed) }))
    .filter(entry => entry.score > 0)
    .sort((a, b) => {
      if (a.score !== b.score) return b.score - a.score;
      const stageA = stageForPlayer(a.player)?.id ?? "";
      const stageB = stageForPlayer(b.player)?.id ?? "";
      if (stageA !== stageB) return stageA.localeCompare(stageB);
      return (ratingForPlayer(b.player)?.value ?? 0) - (ratingForPlayer(a.player)?.value ?? 0);
    })
    .slice(0, 30)
    .map(entry => entry.player);
}

function searchScore(player, normalized, tokens, reversed) {
  const terms = player.searchIndex ?? [];
  if (terms.some(term => term === normalized)) return 1000;
  if (reversed && terms.some(term => term === reversed)) return 960;
  if (terms.some(term => term.startsWith(normalized))) return 850;
  if (terms.some(term => term.includes(normalized))) return 700;
  if (tokens.length && tokens.every(token => player.searchTokens?.has(token))) return 620;
  if (tokens.length && tokens.every(token => terms.some(term => term.includes(token)))) return 520;
  return 0;
}

function preparePlayer(player) {
  const values = searchValuesForPlayer(player);
  const tokenSet = new Set(values.flatMap(searchTokens));
  return {
    ...player,
    searchIndex: [...new Set(values.map(normalize).filter(Boolean))],
    searchTokens: tokenSet
  };
}

function searchValuesForPlayer(player) {
  const values = [
    player.fideID,
    player.displayName,
    player.name,
    player.chineseName,
    player.pinyin,
    ...(player.aliases ?? [])
  ].filter(Boolean).map(String);

  for (const value of [...values]) {
    const parts = searchTokens(value);
    if (parts.length >= 2) {
      values.push(parts.join(" "));
      values.push(parts.slice().reverse().join(" "));
    }
  }
  return [...new Set(values)];
}

function uniqueStrings(values) {
  return [...new Set(values.filter(Boolean).map(String))];
}

function displayName(player) {
  let name = "";
  if (player.chineseName && player.name && player.chineseName !== player.name) {
    name = `${player.chineseName} · ${player.name}`;
  } else {
    name = player.displayName ?? player.name ?? player.chineseName ?? `FIDE ${player.fideID}`;
  }
  return displayText(name);
}

function detailChineseNameLine(player) {
  if (!player.chineseName || displayName(player).includes(player.chineseName)) return "";
  return `<div class="detail-cn-name">${escapeHTML(displayText(player.chineseName))}</div>`;
}

function mimicGameHref(player) {
  const params = new URLSearchParams({
    fideID: String(player.fideID),
    name: displayName(player)
  });
  return `mimic/?${params.toString()}`;
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
  return displayText(value)
    .normalize("NFKD")
    .toLowerCase()
    .replace(/[\s,.'’"()，。·_\-]+/g, "");
}

function searchTokens(value) {
  return displayText(value)
    .normalize("NFKD")
    .toLowerCase()
    .replace(/[,.，。'’"()·_\-]+/g, " ")
    .split(/\s+/)
    .map(token => token.trim())
    .filter(Boolean);
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

function displayText(value) {
  return repairMojibake(String(value ?? ""));
}

function repairMojibake(text) {
  if (!text || !looksLikeMojibake(text)) return text;
  const chars = Array.from(text);
  if (chars.some(char => char.codePointAt(0) > 255)) return text;
  try {
    const bytes = Uint8Array.from(chars, char => char.codePointAt(0));
    const decoded = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    if (decoded && !decoded.includes("�") && mojibakeScore(decoded) < mojibakeScore(text)) {
      return decoded;
    }
  } catch (_error) {
    return text;
  }
  return text;
}

function looksLikeMojibake(text) {
  return /[ÃÂâåæèéäï]|[\u0080-\u009f]/.test(text);
}

function mojibakeScore(text) {
  const markerCount = (text.match(/[ÃÂâåæèéäï]|[\u0080-\u009f]/g) ?? []).length;
  const replacementCount = (text.match(/�/g) ?? []).length;
  const cjkCount = (text.match(/[\u4e00-\u9fff]/g) ?? []).length;
  return markerCount * 3 + replacementCount * 20 - cjkCount;
}
