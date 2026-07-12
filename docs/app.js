import LichessPgnViewer from "./vendor/lichess-pgn-viewer/lichess-pgn-viewer.min.js";

const state = {
  activeStage: "TOTAL",
  selectedFideID: null,
  selectedEventID: null,
  selectedEventRound: null,
  eventFocus: null,
  downloadStatus: "",
  query: "",
  viewer: {
    fideID: "",
    pgnPath: "",
    packageId: "",
    packageLabel: "",
    packageGameCount: 0,
    focusRound: "",
    focusApplied: false,
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
  eventPane: document.querySelector("#eventPane"),
  searchInput: document.querySelector("#searchInput"),
  searchResultsSection: document.querySelector("#searchResultsSection"),
  searchResults: document.querySelector("#searchResults"),
  searchCount: document.querySelector("#searchCount"),
  rankingMeta: document.querySelector("#rankingMeta"),
  leaderboards: document.querySelector(".leaderboards"),
  dashboardSection: document.querySelector("#dashboardSection"),
  statGrid: document.querySelector("#statGrid"),
  recentEvents: document.querySelector("#recentEvents"),
  recentEventsMeta: document.querySelector("#recentEventsMeta"),
  changelogList: document.querySelector("#changelogList"),
  changelogMeta: document.querySelector("#changelogMeta"),
  ageOverview: document.querySelector("#ageOverview"),
  creditsList: document.querySelector("#creditsList")
};

const data = await loadData();
const stages = data.ageRule.stages;
const ADULT_GROUPS = [
  { id: "U20", label: "U20", minAge: 19, maxAge: 20, desc: "19-20 岁" },
  { id: "OPEN", label: "成年", minAge: 19, maxAge: null, desc: "成年公开组 · 19 岁及以上" },
  { id: "S50", label: "S50", minAge: 50, maxAge: null, desc: "元老组 · 50 岁及以上" },
  { id: "S65", label: "S65", minAge: 65, maxAge: null, desc: "元老组 · 65 岁及以上" }
];
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
let eventCatalog = null;
let eventCatalogRequest = null;
const eventDetailCache = new Map();
const eventDetailRequests = new Map();
const domesticDetailCache = new Map();
const domesticShardRequests = new Map();
const PGN_VIEWER_CACHE_MAX_ENTRIES = 3;
const PGN_VIEWER_CACHE_MAX_BYTES = 48 * 1024 * 1024;
let activeLichessViewer = null;
let viewerAutoplayTimer = null;

initialize();

async function loadData() {
  try {
    const youth = await fetchJSON("./data/youth-leaderboards.json", true);
    const dashboard = await fetchJSON("./data/dashboard.json", false);
    const changelog = await fetchJSON("./data/changelog.json", false);
    const allLeaderboards = await fetchJSON("./data/leaderboards.json", false);
    const [
      manifest,
      indexedPlayers,
      registryManifest,
      registryPlayers,
      bulkManifest,
      bulkYouthManifest,
      byPlayerManifest,
      byPlayerPlayers,
      domesticManifest,
      domesticPlayers
    ] = await Promise.all([
      fetchJSON("./data/index/manifest.json", false),
      fetchJSON("./data/index/players.json", false),
      fetchJSON("./data/registry/manifest.json", false),
      fetchJSON("./data/registry/players.json", false),
      fetchJSON("./data/bulk/manifest.json", false),
      fetchJSON("./data/bulk/youth/manifest.json", false),
      fetchJSON("./data/index/by-player/manifest.json", false),
      fetchJSON("./data/index/by-player/players.json", false),
      fetchJSON("./data/registry/domestic/manifest.json", false),
      fetchJSON("./data/registry/domestic/search-index.json", false)
    ]);
    return {
      ...youth,
      dashboard,
      changelog,
      allLeaderboards,
      manifest,
      registryManifest,
      bulkManifest,
      bulkYouthManifest,
      byPlayerManifest,
      domesticManifest,
      players: mergeDomesticPlayers(
        mergePlayers(youth.players ?? [], indexedPlayers ?? [], registryPlayers ?? [], byPlayerPlayers ?? []),
        domesticPlayers ?? []
      )
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

function mergeDomesticPlayers(fidePlayers, domesticPlayers) {
  const merged = [...fidePlayers];
  const byFide = new Map(merged.map(player => [String(player.fideID), player]));
  domesticPlayers.forEach(domestic => {
    const fideID = String(domestic.fideID ?? "");
    if (fideID && byFide.has(fideID)) {
      const player = byFide.get(fideID);
      player.aliases = uniqueStrings([...(player.aliases ?? []), ...(domestic.aliases ?? [])]);
      player.domesticSightings = domestic.sightings ?? [];
      player.domesticIdentity = domestic.confidence ?? null;
      return;
    }
    merged.push({
      ...domestic,
      fideID: "",
      playerID: domestic.id || domestic.domesticID,
      entityType: "domestic-player",
      name: domestic.displayName || domestic.chineseName || domestic.pinyin,
      title: domestic.title || ""
    });
  });
  return merged;
}

function playerKey(player) {
  return String(player?.fideID || player?.playerID || player?.id || "");
}

function isDomesticPlayer(player) {
  return player?.entityType === "domestic-player" && !player?.fideID;
}

function initialize() {
  const routedFideID = initialSelectedPlayerID();
  const routedEventID = initialSelectedEventID();
  state.eventFocus = initialEventFocus();
  state.selectedFideID = players.some(player => playerKey(player) === routedFideID)
    ? routedFideID
    : null;
  state.selectedEventID = state.selectedFideID ? null : routedEventID;
  els.searchInput.addEventListener("input", event => {
    state.query = event.target.value.trim();
    renderSearch();
  });
  document.addEventListener("keydown", handleViewerKeyboard);
  els.detailPane.addEventListener("click", event => {
    const back = event.target.closest('[data-action="back-to-dashboard"]');
    if (back) {
      event.preventDefault();
      clearSelection();
    }
    const playerLink = event.target.closest('[data-action="select-player"]');
    if (playerLink) {
      event.preventDefault();
      selectPlayer(playerLink.dataset.fide);
    }
    const focusedPlayer = event.target.closest('[data-action="select-event-player"]');
    if (focusedPlayer) {
      event.preventDefault();
      selectPlayer(focusedPlayer.dataset.fide, {
        eventID: focusedPlayer.dataset.eventFocus,
        tournamentID: focusedPlayer.dataset.tournamentId,
        round: focusedPlayer.dataset.round || ""
      });
    }
    const roundButton = event.target.closest("[data-event-round]");
    if (roundButton) {
      state.selectedEventRound = Number(roundButton.dataset.eventRound);
      renderEvent();
    }
    const eventLink = event.target.closest('[data-action="select-event"]');
    if (eventLink) {
      event.preventDefault();
      selectEvent(eventLink.dataset.eventId);
    }
  });
  els.eventPane.addEventListener("click", event => {
    const back = event.target.closest('[data-action="back-to-dashboard"]');
    if (back) {
      event.preventDefault();
      clearSelection();
    }
    const playerLink = event.target.closest('[data-action="select-player"]');
    if (playerLink) {
      event.preventDefault();
      selectPlayer(playerLink.dataset.fide);
    }
    const focusedPlayer = event.target.closest('[data-action="select-event-player"]');
    if (focusedPlayer) {
      event.preventDefault();
      selectPlayer(focusedPlayer.dataset.fide, {
        eventID: focusedPlayer.dataset.eventFocus,
        tournamentID: focusedPlayer.dataset.tournamentId,
        round: focusedPlayer.dataset.round || ""
      });
      return;
    }
    const roundButton = event.target.closest("[data-event-round]");
    if (roundButton) {
      state.selectedEventRound = Number(roundButton.dataset.eventRound);
      renderEvent();
      return;
    }
    const eventLink = event.target.closest('[data-action="select-event"]');
    if (eventLink) {
      event.preventDefault();
      selectEvent(eventLink.dataset.eventId);
    }
  });
  window.addEventListener("popstate", () => {
    const fideID = initialSelectedPlayerID();
    state.eventFocus = initialEventFocus();
    state.selectedFideID = players.some(player => playerKey(player) === fideID) ? fideID : null;
    state.selectedEventID = state.selectedFideID ? null : initialSelectedEventID();
    render();
  });

  render();
}

function render() {
  els.ageRuleText.textContent = ageRuleText();
  renderDashboard();
  renderLeaderboardArea();
  renderSearch();
  renderDetail();
  renderEvent();
}

function renderDashboard() {
  const dash = data.dashboard;
  if (!els.statGrid) return;
  const totals = dash?.totals ?? {};
  const community = dash?.community ?? {};
  const cards = [
    { label: "FIDE 注册棋手", value: totals.players },
    { label: "无 FIDE 姓名池", value: totals.domesticUniqueNames },
    { label: "无 FIDE 临时实体", value: totals.domesticPlayers },
    { label: "无 FIDE 赛事观察", value: totals.domesticSightings },
    { label: "可搜索棋手实体", value: totals.searchablePlayers ?? players.length },
    { label: "中文名覆盖", value: totals.withChineseName },
    { label: "收录棋局", value: totals.games },
    { label: "收录赛事", value: totals.events },
    { label: "已核验中文赛事名", value: totals.eventsWithChineseName != null ? `${Number(totals.eventsWithChineseName).toLocaleString("zh-Hans-CN")} / ${Number(totals.events ?? 0).toLocaleString("zh-Hans-CN")}` : null },
    { label: "可查棋局棋手", value: totals.playersWithGames },
    { label: "社区成员", value: community.count },
    { label: "最新贡献者", value: community.latest ? community.latest.name : null, sub: community.latest?.date }
  ];
  els.statGrid.innerHTML = cards
    .filter(card => card.value !== null && card.value !== undefined)
    .map(card => `
      <div class="stat-card">
        <div class="stat-value">${escapeHTML(typeof card.value === "number" ? card.value.toLocaleString("zh-Hans-CN") : String(card.value))}</div>
        <div class="stat-label">${escapeHTML(card.label)}${card.sub ? ` · ${escapeHTML(card.sub)}` : ""}</div>
      </div>
    `).join("");

  const events = dash?.recentEvents ?? [];
  if (els.recentEventsMeta) els.recentEventsMeta.textContent = events.length ? `最近 ${events.length} 项` : "";
  if (els.recentEvents) {
    els.recentEvents.innerHTML = events.length ? `<div class="recent-event-list">${events.map(event => {
      const linkedPlayers = (event.players ?? [])
        .map(fideID => players.find(player => player.fideID === String(fideID)))
        .filter(Boolean);
      return `
        <article class="recent-event-card">
          <div class="recent-event-main">
            <div class="recent-event-kicker">
              <span>${escapeHTML(event.source ?? "赛事归档")}</span>
              <time datetime="${escapeAttribute(event.date ?? "")}">${escapeHTML(event.date ?? "日期待补")}</time>
            </div>
            ${event.id ? `<button class="recent-event-title" type="button" data-event-id="${escapeAttribute(event.id)}">${escapeHTML(event.displayName ?? event.name ?? "未命名赛事")}</button>` : `<strong class="recent-event-title">${escapeHTML(event.displayName ?? event.name ?? "未命名赛事")}</strong>`}
            <div class="recent-event-links">
              ${linkedPlayers.length ? linkedPlayers.map(player => `<button type="button" class="player-chip" data-fide="${escapeAttribute(player.fideID)}">${escapeHTML(displayName(player))}</button>`).join("") : `<span class="text-muted">${escapeHTML(String(event.playerCount ?? 0))} 名中国棋手参赛</span>`}
              ${event.playerCount > linkedPlayers.length ? `<span class="player-chip-more">+${event.playerCount - linkedPlayers.length}</span>` : ""}
            </div>
          </div>
          <div class="recent-event-stats" aria-label="赛事收录统计">
            <span><strong>${compactNumber(event.gameCount ?? 0)}</strong>盘棋局</span>
            <span><strong>${compactNumber(event.playerCount ?? 0)}</strong>名棋手</span>
            ${event.url ? `<a href="${escapeAttribute(event.url)}" target="_blank" rel="noreferrer">信源 ↗</a>` : ""}
          </div>
        </article>`;
    }).join("")}</div>` : `<div class="empty-state compact">暂无数据</div>`;
    els.recentEvents.querySelectorAll("[data-event-id]").forEach(button => {
      button.addEventListener("click", () => selectEvent(button.dataset.eventId));
    });
    els.recentEvents.querySelectorAll("[data-fide]").forEach(button => {
      button.addEventListener("click", () => selectPlayer(button.dataset.fide));
    });
  }

  // 数据更新记录
  const entries = (data.changelog?.entries ?? []).slice(0, 6);
  if (els.changelogMeta) els.changelogMeta.textContent = entries.length ? `最近 ${entries.length} 次` : "";
  if (els.changelogList) {
    els.changelogList.innerHTML = entries.length ? entries.map(entry => {
      const delta = entry.delta ?? {};
      const parts = [];
      if (delta.games) parts.push(`对局 ${delta.games > 0 ? "+" : ""}${delta.games.toLocaleString("zh-Hans-CN")}`);
      if (delta.withChineseName) parts.push(`中文名 ${delta.withChineseName > 0 ? "+" : ""}${delta.withChineseName}`);
      if (delta.players) parts.push(`棋手 ${delta.players > 0 ? "+" : ""}${delta.players}`);
      return `
        <div class="cl-row">
          <span>${escapeHTML(parts.join(" · ") || "索引重建")}</span>
          <span class="cl-date">${escapeHTML(String(entry.date ?? "").slice(0, 10))}</span>
        </div>`;
    }).join("") : `<div class="empty-state compact">暂无记录</div>`;
  }

  // 年龄组分布
  if (els.ageOverview) {
    const groups = (data.allLeaderboards?.groups ?? []).filter(group =>
      ["U8", "U10", "U12", "U14", "U16", "U18", "U20", "OPEN"].includes(group.id));
    const max = Math.max(...groups.map(group => group.totalEligible || 0), 1);
    els.ageOverview.innerHTML = groups.length ? groups.map(group => `
      <div class="age-row">
        <span>${escapeHTML(group.id === "OPEN" ? "成年" : group.id)}</span>
        <div class="age-bar-track"><div class="age-bar-fill" style="width:${Math.max(2, Math.round((group.totalEligible || 0) / max * 100))}%"></div></div>
        <span class="age-count">${escapeHTML(String(group.totalEligible ?? 0))}</span>
      </div>
    `).join("") : `<div class="empty-state compact">暂无数据</div>`;
  }

  // 社区数据贡献鸣谢
  if (els.creditsList) {
    const credits = dash?.dataContributors ?? [];
    const chips = credits.map(person => `
      <span class="credit-chip" title="${escapeAttribute(`${person.submissions} 次提交 · ${person.players} 名棋手 · ${person.events} 项赛事 · ${person.games} 盘棋`)}">
        ${escapeHTML(person.nickname)}${person.github ? `<a href="https://github.com/${escapeAttribute(person.github)}" target="_blank" rel="noreferrer">@${escapeHTML(person.github)}</a>` : ""}
      </span>
    `).join("");
    els.creditsList.innerHTML = `
      ${chips ? `<div class="credit-chips">${chips}</div>` : ""}
      <div class="credit-howto">
        <strong>不需要 Python：</strong>在网页里填写赛事 tnr、棋手线索或数据勘误，即可生成结构化贡献内容。
        <a class="contribute-cta" href="./contribute.html">打开网页贡献向导</a>
        需要批量抓取时仍可使用<a href="https://github.com/keluoke/china-chess-player-pgn/archive/refs/heads/main.zip">桌面贡献工具</a>。
      </div>`;
  }
}

function renderLeaderboardArea() {
  els.rankingMeta.textContent = `${state.activeStage === "TOTAL" ? "总榜" : state.activeStage} · Top 20`;
  renderTabs();
  renderLeaderboards();
}

function renderTabs() {
  const tabs = [
    { id: "TOTAL", label: "总榜" },
    ...stages.map(stage => ({ id: stage.id, label: stage.id })),
    { id: "U20", label: "U20" }
  ];
  const rows = [];
  for (let i = 0; i < tabs.length; i += 4) rows.push(tabs.slice(i, i + 4));
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
  const adultGroup = ADULT_GROUPS.find(item => item.id === stageID);
  const title = stageID === "TOTAL" ? "总榜" : adultGroup ? adultGroup.label : (stage ? stage.id : "总榜");
  const subtitle = stageID === "TOTAL"
    ? "全部现役中国棋手 · 按 FIDE 标准分"
    : adultGroup
    ? `${adultGroup.desc}(基准年 ${data.competitionYear})`
    : stage
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
          <div class="player-meta">${escapeHTML(playerStage?.id ?? "成年")} · FIDE ${escapeHTML(player.fideID)} · ${escapeHTML(publicAgeLabel(player))}${transferBadge(player)}</div>
          ${note ? `<span class="note-pill">${escapeHTML(note)}</span>` : ""}${transferBadge(player)}
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
  const playerGroups = groupPlayerMatches(matches);
  const eventMatches = searchEvents(state.query);
  const hasQuery = state.query.length > 0;
  if (hasQuery && !eventCatalog) requestEventCatalog();
  els.searchResultsSection.hidden = !hasQuery;
  if (els.dashboardSection) els.dashboardSection.hidden = hasQuery || Boolean(selectedPlayer()) || Boolean(state.selectedEventID);
  if (els.detailPane) els.detailPane.hidden = hasQuery || !selectedPlayer();
  if (els.eventPane) els.eventPane.hidden = hasQuery || !state.selectedEventID;
  els.searchCount.textContent = `${playerGroups.length} 个姓名 · ${eventMatches.length} 项赛事`;
  const eventResults = eventMatches.length ? `
    <section class="search-result-group">
      <div class="search-result-group-title"><h3>赛事</h3><span>${eventMatches.length} 项</span></div>
      <div class="event-search-list">${eventMatches.map(event => `
        <button class="result-button event-search-result" type="button" data-event-id="${escapeAttribute(event.id)}">
          <div class="player-name">${escapeHTML(event.displayName ?? event.chineseName ?? event.name ?? "未命名赛事")}</div>
          <div class="player-meta">${escapeHTML([event.date || "日期待补", event.rounds ? `${event.rounds} 轮` : "", event.participants ? `${event.participants} 人` : "", event.source].filter(Boolean).join(" · "))}</div>
        </button>`).join("")}</div>
    </section>` : "";
  const playerResults = playerGroups.length ? `
    <section class="search-result-group">
      <div class="search-result-group-title"><h3>棋手</h3><span>${matches.length} 条档案</span></div>
      <div class="player-search-list">${playerGroups.map(group => group.length > 1 ? disambiguationCard(group) : (() => {
    const player = group[0];
    const rating = ratingForPlayer(player);
    const fideLabel = player.fideID ? `FIDE ${player.fideID}` : "[无FIDE]";
    const ratingLabel = rating ? `${rating.value} ${rating.kind}` : "无等级分";
    const birthLabel = publicAgeLabel(player);
    const titleLabel = player.title || "无称号";
    return `
      <button class="result-button" type="button" data-player="${escapeAttribute(playerKey(player))}" aria-pressed="${state.selectedFideID === playerKey(player)}">
        <div class="player-name">${escapeHTML(displayName(player))} ${publicStatusBadge(player)}</div>
        <div class="player-meta">${escapeHTML(fideLabel)} · ${escapeHTML(ratingLabel)} · ${escapeHTML(birthLabel)} · ${escapeHTML(titleLabel)}</div>
      </button>
    `;
  })()).join("")}</div></section>` : "";
  els.searchResults.innerHTML = eventResults || playerResults
    ? `${eventResults}${playerResults}`
    : `<div class="empty-state compact gap-empty"><strong>本地库暂未匹配</strong><span>可以试试中文名、拼音、FIDE ID 或赛事名称。</span><a class="primary-button" data-gap-query="${escapeAttribute(state.query)}" href="./contribute.html?type=data-gap&query=${encodeURIComponent(state.query)}">登记这条数据缺口</a><small>点击后只在本机记录；提交前会再次展示内容，不会静默上传姓名。</small></div>`;

  els.searchResults.querySelectorAll("[data-player]").forEach(button => {
    button.addEventListener("click", () => selectPlayer(button.dataset.player));
  });
  els.searchResults.querySelectorAll("[data-event-id]").forEach(button => {
    button.addEventListener("click", () => selectEvent(button.dataset.eventId));
  });
  els.searchResults.querySelectorAll("[data-gap-query]").forEach(link => {
    link.addEventListener("click", () => recordLocalGap(link.dataset.gapQuery));
  });
}

function groupPlayerMatches(matches) {
  const groups = new Map();
  matches.forEach(player => {
    const key = normalizedIdentityName(player) || playerKey(player);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(player);
  });
  return [...groups.values()];
}

function disambiguationCard(group) {
  const label = group[0].chineseName || group[0].displayName || group[0].name || "同名棋手";
  return `<article class="disambiguation-card">
    <div class="disambiguation-head"><div><strong>${escapeHTML(displayText(label))}</strong><span class="identity-status same-name">同名待区分</span></div><small>库内有 ${group.length} 条同名档案</small></div>
    <div class="disambiguation-options">${group.map(player => {
      const rating = ratingForPlayer(player);
      const context = player.fideID
        ? [`FIDE ${player.fideID}`, rating ? `${rating.value} ${rating.kind}` : "无等级分", publicAgeLabel(player), player.title].filter(Boolean)
        : ["无 FIDE", player.publicLocation, ...(player.eventYears ?? []), ...(player.eventNames ?? []).slice(0, 1)].filter(Boolean);
      return `<button type="button" data-player="${escapeAttribute(playerKey(player))}"><strong>${escapeHTML(displayName(player))}</strong><span>${escapeHTML(context.join(" · ") || "打开赛事档案核对")}</span></button>`;
    }).join("")}</div>
    <p>这些档案尚未确认属于同一人；请按参赛年份、地区和赛事逐条核对。</p>
  </article>`;
}

function renderDetail() {
  const player = selectedPlayer();
  const showDetail = Boolean(player);
  els.detailPane.hidden = !showDetail;
  if (els.dashboardSection && !state.query) els.dashboardSection.hidden = showDetail || Boolean(state.selectedEventID);
  if (!showDetail) {
    els.detailPane.innerHTML = "";
    return;
  }
  if (isDomesticPlayer(player)) {
    renderDomesticPlayerDetail(player);
    return;
  }
  requestPlayerDetail(player);
  requestStaticPlayerDetail(player);

  const stage = stageForPlayer(player);
  const note = stage ? liChengzhiNote(player, stage.id) : null;
  const staticInfo = staticPlayerInfo(player);
  if (staticInfo) ensureFocusedEventViewer(player, staticInfo);
  const staticGames = staticInfo?.gameCount ?? 0;
  if (!staticGames) requestBulkPlayerDetail(player);
  const bulkInfo = bulkPlayerCache.get(String(player.fideID));
  if (state.viewer.visible && state.viewer.fideID === String(player.fideID) && state.viewer.pgnPath) {
    requestPGNViewer(player, state.viewer);
  }

  els.detailPane.innerHTML = `
    <div class="detail-title">
      <div>
        <span class="eyebrow">${publicStatusBadge(player)} · 赛前情报</span>
        <h2>${escapeHTML(displayName(player))}</h2>
        ${detailChineseNameLine(player)}
        <p>FIDE ${escapeHTML(player.fideID)} · ${escapeHTML(uniqueStrings([publicAgeLabel(player), stageLabelForPlayer(player, stage)]).join(" · "))}</p>
      </div>
      <div class="detail-title-actions">
        ${player.sex === "F" ? `<span class="stage-chip">女</span>` : ""}
        <a class="action-link" href="#" data-action="back-to-dashboard">← 返回</a>
        <a class="action-link" href="https://ratings.fide.com/profile/${encodeURIComponent(player.fideID)}" target="_blank" rel="noreferrer">FIDE 主页</a>
        <a class="action-link" href="./contribute.html?type=player-correction&player=${encodeURIComponent(player.fideID)}&name=${encodeURIComponent(displayName(player))}">补充或勘误</a>
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
    ${playerCoverageStatus(player, staticInfo, bulkInfo)}
    ${playerEventHistory(detailCache.get(player.fideID) ?? player)}
    ${sameNameRelatedBlock(player)}

    ${state.downloadStatus ? `<div class="download-status" aria-live="polite">${escapeHTML(state.downloadStatus)}</div>` : ""}

    ${pgnViewerBlock(player, staticInfo)}
  `;

  wireDetailActions(player, staticInfo);
  wirePGNViewerActions(player);
  mountLichessViewer(player);
}

function renderDomesticPlayerDetail(player) {
  if (!eventCatalog) requestEventCatalog();
  const cachedDetail = domesticDetailCache.get(player.domesticID);
  if (!player.sightings && cachedDetail) Object.assign(player, cachedDetail);
  if (!player.sightings && player.detailPath) {
    requestDomesticPlayerDetail(player);
    els.detailPane.innerHTML = `<div class="event-loading">正在载入该棋手的赛事证据…</div>`;
    return;
  }
  const sightings = player.sightings ?? [];
  const publicLocation = player.publicLocation || publicLocationFromSightings(sightings);
  const stages = uniqueStrings(sightings.map(publicStageFromSighting).filter(Boolean));
  els.detailPane.innerHTML = `
    <div class="detail-title">
      <div>
        <span class="eyebrow">${publicStatusBadge(player)} · 国内赛事参赛档案</span>
        <h2>${escapeHTML(displayName(player))}</h2>
        <p>[无FIDE] · ${escapeHTML(stages[0] || "年龄组待补")} · 公开赛事记录</p>
      </div>
      <div class="detail-title-actions">
        <span class="stage-chip domestic-chip">无 FIDE</span>
        <a class="action-link" href="#" data-action="back-to-dashboard">← 返回</a>
        <a class="action-link" href="./contribute.html?player=${encodeURIComponent(player.domesticID ?? player.id)}&name=${encodeURIComponent(displayName(player))}">补充身份线索</a>
        <a class="action-link" href="./contribute.html?type=privacy-request&player=${encodeURIComponent(player.domesticID ?? player.id)}&name=${encodeURIComponent(displayName(player))}">删除 / 匿名化请求</a>
      </div>
    </div>
    <div class="appearance-summary">
      <div><span>赛事记录</span><strong>${escapeHTML(String(sightings.length))} 次</strong></div>
      <div><span>参赛组别</span><strong>${escapeHTML(stages.length ? `${stages.length} 类` : "待补")}</strong></div>
      <div><span>公开地区</span><strong>${escapeHTML(publicLocation || "未公开")}</strong></div>
    </div>
    <section class="event-roster domestic-sightings">
      <div class="section-heading"><h3>赛事足迹</h3><span>${sightings.length} 次参赛</span></div>
      ${sightings.length ? `<div class="sighting-list">${sightings.map(sighting => `
        <article class="sighting-card">
          <div class="sighting-main"><strong>${escapeHTML(sighting.eventName ?? sighting.group ?? "未命名赛事")}</strong><span>${escapeHTML([sighting.eventDate, publicStageFromSighting(sighting), publicLocationFromSighting(sighting), sighting.rank ? `第 ${sighting.rank} 名` : "", sighting.score ? `${sighting.score} 分` : ""].filter(Boolean).join(" · ") || "赛果待补")}</span></div>
          <div class="sighting-actions">
            ${sightingEventID(sighting) ? `<button type="button" class="action-link" data-action="select-event" data-event-id="${escapeAttribute(sightingEventID(sighting))}">${sightingHasPGN(sighting) ? "查看赛事与棋谱" : "查看赛事档案"}</button>` : ""}
            ${sighting.sourceURL ? `<a href="${escapeAttribute(sighting.sourceURL)}" target="_blank" rel="noreferrer">原始成绩 ↗</a>` : ""}
          </div>
        </article>`).join("")}</div>` : `<div class="empty-state compact">暂无赛事证据。</div>`}
    </section>
    ${sameNameRelatedBlock(player)}
  `;
}

function requestDomesticPlayerDetail(player) {
  const path = player?.detailPath;
  if (!path || domesticShardRequests.has(path)) return;
  const request = fetchJSON(`./${path}`, true)
    .then(rows => {
      (rows ?? []).forEach(row => domesticDetailCache.set(row.domesticID, row));
      const detail = domesticDetailCache.get(player.domesticID);
      if (detail) Object.assign(player, detail);
      if (state.selectedFideID === playerKey(player)) renderDetail();
    })
    .catch(error => {
      if (state.selectedFideID === playerKey(player)) {
        els.detailPane.innerHTML = `<div class="event-empty">赛事证据载入失败：${escapeHTML(error.message)}</div>`;
      }
    })
    .finally(() => domesticShardRequests.delete(path));
  domesticShardRequests.set(path, request);
}

function renderEvent() {
  const eventID = state.selectedEventID;
  els.eventPane.hidden = !eventID;
  if (!eventID) {
    els.eventPane.innerHTML = "";
    return;
  }
  if (!eventCatalog) {
    els.eventPane.innerHTML = `<div class="event-loading">正在载入赛事目录…</div>`;
    requestEventCatalog();
    return;
  }
  const event = eventCatalog.find(item => item.id === eventID);
  if (!event) {
    els.eventPane.innerHTML = `
      <div class="event-empty">
        <h2>未找到赛事</h2>
        <p>该链接对应的赛事已不存在，或本地赛事目录仍在更新。</p>
        <a class="action-link" href="#" data-action="back-to-dashboard">← 返回看板</a>
      </div>`;
    return;
  }
  const eventPlayers = (event.players ?? [])
    .map(fideID => players.find(player => player.fideID === String(fideID)))
    .filter(Boolean);
  const visiblePlayers = eventPlayers.slice(0, 24);
  const extraPlayers = Math.max(0, eventPlayers.length - visiblePlayers.length);
  const eventDetail = eventDetailCache.get(String(event.tournamentID ?? ""));
  if (event.detailPath && !eventDetail) requestEventDetail(event);
  const participantTotal = Number(event.participants);
  const coverageLabel = eventDetail || event.coverageScope === "domestic-full"
    ? "完整赛事覆盖"
    : "仅展示已收录中国棋手";
  const facts = [
    ["日期", event.date],
    ["轮次", event.rounds],
    ["报名人数", event.participants],
    ["中国棋手", event.playerCount ? `${event.playerCount} 名` : null],
    ["覆盖口径", coverageLabel],
    ["已归档 PGN", event.gameCount ? `${compactNumber(event.gameCount)} 盘` : null],
    ["有棋谱棋手", event.pgnPlayerCount ? `${event.pgnPlayerCount} 名` : null]
  ].filter(([, value]) => value !== null && value !== undefined && value !== "");

  els.eventPane.innerHTML = `
    <div class="detail-title event-title">
      <div>
        <span class="eyebrow">${dataStatusBadge(eventDataStatus(event))} · 赛事档案 · ${escapeHTML(event.source ?? "")}</span>
        <h2>${escapeHTML(event.displayName ?? event.name ?? "未命名赛事")}</h2>
        ${event.chineseName && event.name !== event.chineseName ? `<p class="event-source-name">信源原名：${escapeHTML(event.name)}</p>` : ""}
      </div>
      <div class="detail-title-actions">
        <a class="action-link" href="#" data-action="back-to-dashboard">← 返回看板</a>
        ${event.url ? `<a class="action-link" href="${escapeAttribute(event.url)}" target="_blank" rel="noreferrer">Chess-Results ↗</a>` : ""}
      </div>
    </div>
    <div class="event-facts">
      ${facts.map(([label, value]) => `<div><span>${escapeHTML(label)}</span><strong>${escapeHTML(String(value))}</strong></div>`).join("")}
    </div>
    <section class="event-roster">
      <div class="section-heading"><h3>${eventDetail ? "已收录 FIDE 棋手" : "参赛中国棋手"}</h3><span>${eventPlayers.length ? `${eventPlayers.length} 名可跳转` : "名单待同步"}</span></div>
      ${visiblePlayers.length ? `<div class="event-player-grid">${visiblePlayers.map(player => `
        <button class="event-player" type="button" data-action="select-event-player" data-fide="${escapeAttribute(player.fideID)}" data-event-focus="${escapeAttribute(event.id)}" data-tournament-id="${escapeAttribute(event.tournamentID ?? "")}">
          <strong>${escapeHTML(displayName(player))}</strong><span>FIDE ${escapeHTML(player.fideID)}</span>
        </button>`).join("")}</div>${extraPlayers ? `<p class="event-more">另有 ${extraPlayers} 名已收录棋手；完整名单见信源赛事页。</p>` : ""}` : `<div class="empty-state compact">该赛事已有赛事记录，但棋手名单尚未同步。</div>`}
    </section>
    ${eventDetail ? domesticEventData(event, eventDetail) : event.detailPath ? `<div class="event-loading">正在载入逐轮成绩与最终排名…</div>` : ""}
    <p class="event-provenance">${event.canonicalEventID ? `Canonical ID：${escapeHTML(event.canonicalEventID)} · ` : ""}信源 ID：${escapeHTML(event.tournamentID ?? event.id)}${event.evidenceURL ? " · 中文名已由社区核验" : ""}</p>
  `;
}

function requestEventDetail(event) {
  const tournamentID = String(event?.tournamentID ?? "");
  if (!tournamentID || !event.detailPath || eventDetailCache.has(tournamentID) || eventDetailRequests.has(tournamentID)) return;
  const request = fetchJSON(`./${event.detailPath}`, true)
    .then(detail => {
      eventDetailCache.set(tournamentID, detail);
      if (state.selectedEventID === event.id) renderEvent();
    })
    .catch(error => {
      eventDetailCache.set(tournamentID, { error: error.message, standings: [], rounds: [] });
      if (state.selectedEventID === event.id) renderEvent();
    })
    .finally(() => eventDetailRequests.delete(tournamentID));
  eventDetailRequests.set(tournamentID, request);
}

function domesticEventData(event, detail) {
  if (detail.error) return `<div class="event-empty">逐轮成绩载入失败：${escapeHTML(detail.error)}</div>`;
  const rounds = detail.rounds ?? [];
  const selectedRound = rounds.find(item => Number(item.round) === Number(state.selectedEventRound)) ?? rounds[rounds.length - 1];
  if (selectedRound && state.selectedEventRound == null) state.selectedEventRound = Number(selectedRound.round);
  const standings = detail.standings ?? [];
  return `
    <section class="event-results-section">
      <div class="section-heading"><h3>逐轮对阵结果</h3><span>${rounds.length} 轮</span></div>
      <div class="event-round-tabs" role="tablist" aria-label="赛事轮次">
        ${rounds.map(item => `<button type="button" data-event-round="${escapeAttribute(item.round)}" aria-selected="${Number(item.round) === Number(selectedRound?.round)}">第 ${escapeHTML(item.round)} 轮</button>`).join("")}
      </div>
      ${selectedRound ? `<div class="pairing-list">${(selectedRound.pairings ?? []).map(pairing => pairingRow(event, selectedRound.round, pairing)).join("") || `<div class="empty-state compact">该轮暂无对阵数据。</div>`}</div>` : `<div class="empty-state compact">暂无逐轮数据。</div>`}
    </section>
    <section class="event-results-section">
      <div class="section-heading"><h3>最终成绩排行</h3><span>${standings.length} 名</span></div>
      <div class="standings-table-wrap"><table class="standings-table"><thead><tr><th>名次</th><th>棋手</th><th>FIDE ID</th><th>等级分</th><th>得分</th><th>单位</th></tr></thead><tbody>
        ${standings.map(row => `<tr><td>${escapeHTML(row.rank ?? "-")}</td><td>${eventSideControl(event, row, "")}</td><td>${escapeHTML(row.fideID || "无FIDE")}</td><td>${escapeHTML(row.rating || "-")}</td><td><strong>${escapeHTML(row.score || "-")}</strong></td><td>${escapeHTML(row.fideID ? (row.club || "-") : (publicLocationFromSighting(row) || "未公开"))}</td></tr>`).join("")}
      </tbody></table></div>
    </section>`;
}

function pairingRow(event, round, pairing) {
  const localGame = pairing.localGame;
  const focusFideID = localGame?.playerFideIDs?.[0] || pairing.white?.fideID || pairing.black?.fideID || "";
  const pgnAction = localGame && focusFideID
    ? `<button type="button" class="pairing-pgn available" data-action="select-event-player" data-fide="${escapeAttribute(focusFideID)}" data-event-focus="${escapeAttribute(event.id)}" data-tournament-id="${escapeAttribute(event.tournamentID ?? "")}" data-round="${escapeAttribute(round)}">● 本库 PGN</button>`
    : pairing.pgnURL
    ? `<a class="pairing-pgn external" href="${escapeAttribute(pairing.pgnURL)}" target="_blank" rel="noreferrer">PGN ↗</a>`
    : `<span class="pairing-pgn missing">无 PGN</span>`;
  return `<article class="pairing-row"><span class="pairing-board">${escapeHTML(pairing.board || "-")}</span><div>${eventSideControl(event, pairing.white ?? {}, round)}</div><strong class="pairing-result">${escapeHTML(pairing.result || "*")}</strong><div>${eventSideControl(event, pairing.black ?? {}, round)}</div>${pgnAction}</article>`;
}

function eventSideControl(event, side, round) {
  const label = side.chineseName && side.name && side.chineseName !== side.name ? `${side.chineseName} · ${side.name}` : side.chineseName || side.name || "轮空";
  if (!side.fideID) return `<span class="event-side-name">${escapeHTML(label)}<small>[无FIDE]</small></span>`;
  return `<button type="button" class="event-side-name link" data-action="select-event-player" data-fide="${escapeAttribute(side.fideID)}" data-event-focus="${escapeAttribute(event.id)}" data-tournament-id="${escapeAttribute(event.tournamentID ?? "")}" data-round="${escapeAttribute(round)}">${escapeHTML(label)}<small>FIDE ${escapeHTML(side.fideID)}</small></button>`;
}

function requestEventCatalog() {
  if (eventCatalogRequest) return eventCatalogRequest;
  eventCatalogRequest = fetchJSON("./data/index/events.json", true)
    .then(catalog => {
      eventCatalog = Array.isArray(catalog) ? catalog : [];
      renderEvent();
      if (state.query) renderSearch();
      if (isDomesticPlayer(selectedPlayer())) renderDetail();
    })
    .catch(error => {
      els.eventPane.innerHTML = `<div class="event-empty">赛事目录加载失败：${escapeHTML(error.message)}</div>`;
    })
    .finally(() => { eventCatalogRequest = null; });
  return eventCatalogRequest;
}

function selectedPlayer() {
  const fideID = state.selectedFideID;
  return detailCache.get(fideID) ?? players.find(item => playerKey(item) === fideID);
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
      games: detail.games ?? [],
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
      games: player.games ?? [],
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
        <strong>查询到该棋手在本库中的棋局数量：${compactNumber(info.gameCount)} 盘</strong>
        <span>${escapeHTML(stageLine || (info.sources ?? []).join(" · ") || "按棋手聚合静态包")}</span>
      </div>
      <div class="pgn-package-grid">${packageButtons}</div>
    </div>
  `;
}

function playerEventHistory(player) {
  const rows = (player?.events ?? []).slice(0, 12);
  if (!rows.length) return "";
  return `
    <section class="player-event-history">
      <div class="section-heading"><h3>赛事记录</h3><span>${player.eventCount ?? rows.length} 项</span></div>
      <div class="player-event-list">
        ${rows.map(event => {
          const eventID = event.id || (event.tournamentID ? `${String(event.source ?? "Chess-Results").toLowerCase().replace(/\s+/g, "-")}:${event.tournamentID}` : "");
          const name = event.chineseName || event.displayName || event.name || "未命名赛事";
          return `<button type="button" class="player-event-row" ${eventID ? `data-action="select-event" data-event-id="${escapeAttribute(eventID)}"` : "disabled"}>
            <span><strong>${escapeHTML(name)}</strong><small>${escapeHTML(event.date ?? "日期待补")}${event.rank ? ` · 名次 ${escapeHTML(String(event.rank))}` : ""}</small></span>
            <em>${event.gameCount ? `${compactNumber(event.gameCount)} 盘` : "查看赛事"}</em>
          </button>`;
        }).join("")}
      </div>
    </section>`;
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
      focusRound: info.focusRound ?? state.viewer.focusRound ?? "",
      focusApplied: false,
      orientation: "",
      error: "",
      autoplay: false
    };
  }

  const cached = getCachedPGNViewerPackage(pgnPath);
  if (cached) {
    state.viewer.status = "loaded";
    if (state.viewer.focusRound && !state.viewer.focusApplied) {
      state.viewer.gameIndex = focusedGameIndex(cached.games, state.viewer.focusRound);
      state.viewer.focusApplied = true;
    }
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
        state.viewer.gameIndex = state.viewer.focusRound ? focusedGameIndex(games, state.viewer.focusRound) : 0;
        state.viewer.focusApplied = true;
        state.viewer.orientation = preferredBoardOrientation(player, games[state.viewer.gameIndex]);
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

function focusedGameIndex(games, round) {
  const wanted = String(round ?? "").split(".")[0];
  const index = games.findIndex(game => String(game.headers?.Round ?? "").split(".")[0] === wanted);
  return index >= 0 ? index : 0;
}

function ensureFocusedEventViewer(player, info) {
  const focus = state.eventFocus;
  if (!focus?.tournamentID || !info?.games?.length) return;
  const games = info.games.filter(game => String(game.tournamentID ?? "") === String(focus.tournamentID));
  const focused = games.find(game => !focus.round || String(game.round ?? "").split(".")[0] === String(focus.round).split(".")[0]) ?? games[0];
  if (!focused?.sourcePgnPath) return;
  const alreadyFocused = state.viewer.visible
    && state.viewer.pgnPath === focused.sourcePgnPath
    && String(state.viewer.focusRound ?? "") === String(focus.round ?? "");
  if (alreadyFocused) return;
  state.viewer = {
    fideID: String(player.fideID),
    pgnPath: focused.sourcePgnPath,
    packageId: `event-${focus.tournamentID}`,
    packageLabel: `本赛事${focus.round ? `第 ${focus.round} 轮` : ""}`,
    packageGameCount: games.length,
    focusRound: focus.round ?? "",
    focusApplied: false,
    visible: true,
    status: getCachedPGNViewerPackage(focused.sourcePgnPath) ? "loaded" : "idle",
    gameIndex: 0,
    orientation: "",
    error: "",
    autoplay: false
  };
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
    ["白方", gamePlayerLine(headers, "White")],
    ["黑方", gamePlayerLine(headers, "Black")],
    ["ECO", headers.ECO],
    ["时限", headers.TimeControl]
  ].filter(([, value]) => displayText(value ?? "") && displayText(value ?? "") !== "?");

  return items.map(([label, value]) => `
    <div>
      <dt>${escapeHTML(label)}</dt>
      <dd>${value?.html ?? escapeHTML(displayText(value))}</dd>
    </div>
  `).join("");
}

function gamePlayerLine(headers, side) {
  const name = displayText(headers[side] ?? "");
  const elo = displayText(headers[`${side}Elo`] ?? "");
  const title = displayText(headers[`${side}Title`] ?? "");
  const fed = displayText(headers[`${side}Fed`] ?? headers[`${side}Federation`] ?? "");
  const fideID = String(headers[`${side}FideId`] ?? headers[`${side}FIDEId`] ?? "").replace(/\D/g, "");
  const knownPlayer = fideID && players.some(player => player.fideID === fideID);
  const text = [title, name, elo || "", fed].filter(Boolean).join(" · ");
  return knownPlayer
    ? { html: `<button type="button" class="inline-player-link" data-action="select-player" data-fide="${escapeAttribute(fideID)}">${escapeHTML(text)}</button>` }
    : text;
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
    focusRound: "",
    focusApplied: true,
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
    focusRound: "",
    focusApplied: false,
    visible: false,
    status: "idle",
    gameIndex: 0,
    orientation: "",
    error: "",
    autoplay: false
  };
}

function selectPlayer(playerID, eventFocus = null) {
  if (state.selectedFideID !== playerID) resetPGNViewer(playerID);
  state.selectedFideID = playerID;
  state.selectedEventID = null;
  state.eventFocus = eventFocus;
  state.downloadStatus = "";
  const player = players.find(item => playerKey(item) === playerID);
  updateRoute(player?.fideID ? { fideID: player.fideID, eventFocus } : { playerID });
  if (state.query) {
    state.query = "";
    if (els.searchInput) els.searchInput.value = "";
    renderSearch();
  }
  renderDetail();
  renderEvent();
  scrollDetailIntoViewOnMobile();
}

function initialSelectedPlayerID() {
  const params = new URLSearchParams(window.location.search);
  const domesticID = String(params.get("player") || "");
  if (domesticID) return domesticID;
  return String(params.get("fideID") || params.get("fide") || "").replace(/\D/g, "");
}

function initialSelectedEventID() {
  const params = new URLSearchParams(window.location.search);
  return String(params.get("event") || "");
}

function initialEventFocus() {
  const params = new URLSearchParams(window.location.search);
  const tournamentID = String(params.get("eventFocus") || "").replace(/\D/g, "");
  if (!tournamentID) return null;
  return {
    eventID: `chess-results:${tournamentID}`,
    tournamentID,
    round: String(params.get("round") || "").replace(/[^0-9.]/g, "")
  };
}

function updateRoute({ fideID = null, playerID = null, eventID = null, eventFocus = null }) {
  if (!window.history?.replaceState) return;
  const url = new URL(window.location.href);
  if (fideID) url.searchParams.set("fideID", fideID);
  else url.searchParams.delete("fideID");
  if (playerID) url.searchParams.set("player", playerID);
  else url.searchParams.delete("player");
  if (eventID) url.searchParams.set("event", eventID);
  else url.searchParams.delete("event");
  if (eventFocus?.tournamentID) {
    url.searchParams.set("eventFocus", eventFocus.tournamentID);
    if (eventFocus.round) url.searchParams.set("round", eventFocus.round);
    else url.searchParams.delete("round");
  } else {
    url.searchParams.delete("eventFocus");
    url.searchParams.delete("round");
  }
  window.history.replaceState(null, "", url);
}

function selectEvent(eventID) {
  if (!eventID) return;
  resetPGNViewer(null);
  state.selectedFideID = null;
  state.selectedEventID = eventID;
  state.selectedEventRound = null;
  state.eventFocus = null;
  state.downloadStatus = "";
  updateRoute({ eventID });
  if (state.query) {
    state.query = "";
    if (els.searchInput) els.searchInput.value = "";
    renderSearch();
  }
  renderDetail();
  renderEvent();
  scrollDetailIntoViewOnMobile();
}

function clearSelection() {
  state.selectedFideID = null;
  state.selectedEventID = null;
  state.selectedEventRound = null;
  state.eventFocus = null;
  state.downloadStatus = "";
  updateRoute({});
  renderDetail();
  renderEvent();
}

function scrollDetailIntoViewOnMobile() {
  if (!window.matchMedia("(max-width: 720px)").matches) return;
  window.requestAnimationFrame(() => {
    (state.selectedEventID ? els.eventPane : els.detailPane).scrollIntoView({
      block: "start",
      inline: "nearest",
      behavior: "smooth"
    });
  });
}

function rankingsForStage(stageID) {
  if (stageID === "TOTAL") {
    return players
      .filter(player => !player.inactive)
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
  const adultGroup = ADULT_GROUPS.find(group => group.id === stageID);
  const inAdultGroup = player => {
    const age = data.competitionYear - player.birthYear;
    return Number.isFinite(age) && age >= adultGroup.minAge
      && (adultGroup.maxAge == null || age <= adultGroup.maxAge)
      && !player.inactive;
  };
  return players
    .filter(player => adultGroup
      ? inAdultGroup(player)
      : (stageID === "ALL" ? Boolean(stageForPlayer(player)) : stageForPlayer(player)?.id === stageID))
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

function stageLabelForPlayer(player, stage) {
  if (stage?.id) return stage.id;
  const age = data.competitionYear - Number(player.birthYear);
  return Number.isFinite(age) && age >= 19 ? "Adult" : "未到 U8";
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

function searchEvents(query) {
  const normalized = normalize(query);
  if (!normalized || !eventCatalog) return [];
  return eventCatalog
    .map(event => {
      const terms = [
        event.displayName,
        event.chineseName,
        event.name,
        event.id,
        event.tournamentID,
        event.canonicalEventID,
        ...(event.aliases ?? [])
      ].filter(Boolean).map(value => normalize(String(value)));
      let score = 0;
      if (terms.some(term => term === normalized)) score = 1000;
      else if (terms.some(term => term.startsWith(normalized))) score = 850;
      else if (terms.some(term => term.includes(normalized))) score = 700;
      return { event, score };
    })
    .filter(entry => entry.score > 0)
    .sort((a, b) => b.score - a.score || String(b.event.date ?? "").localeCompare(String(a.event.date ?? "")))
    .slice(0, 12)
    .map(entry => entry.event);
}

function sameNameCount(player) {
  const key = normalizedIdentityName(player);
  if (!key) return 1;
  return players.filter(candidate => normalizedIdentityName(candidate) === key).length;
}

function sameNameRelatedPlayers(player) {
  const key = normalizedIdentityName(player);
  const currentKey = playerKey(player);
  if (!key) return [];
  return players
    .filter(candidate => playerKey(candidate) !== currentKey && normalizedIdentityName(candidate) === key)
    .sort((a, b) => Number(Boolean(b.fideID)) - Number(Boolean(a.fideID)) || Number(b.eventCount ?? 0) - Number(a.eventCount ?? 0));
}

function sameNameRelatedBlock(player) {
  const related = sameNameRelatedPlayers(player);
  if (!related.length) return "";
  return `
    <section class="related-identities">
      <div class="section-heading"><h3>其他同名参赛记录</h3><span>同名待区分 · ${related.length} 条</span></div>
      <p class="related-identities-note">这些记录姓名相同，但尚未确认属于同一位棋手。可以逐条打开，按赛事、组别和单位自行核对。</p>
      <div class="related-identity-list">${related.slice(0, 12).map(candidate => {
        const candidateSightings = candidate.sightings?.length ?? candidate.sightingCount ?? candidate.eventCount ?? 0;
        const context = candidate.fideID
          ? [`FIDE ${candidate.fideID}`, publicAgeLabel(candidate), candidate.title].filter(Boolean)
          : ["无 FIDE", candidate.publicLocation, candidateSightings ? `${candidateSightings} 次赛事记录` : "查看参赛档案"].filter(Boolean);
        return `<button type="button" class="related-identity" data-action="select-player" data-fide="${escapeAttribute(playerKey(candidate))}"><strong>${escapeHTML(displayName(candidate))}</strong><span>${escapeHTML(context.join(" · "))}</span></button>`;
      }).join("")}</div>
      ${related.length > 12 ? `<p class="event-more">另有 ${related.length - 12} 条同名记录，可从搜索结果继续查看。</p>` : ""}
    </section>`;
}

function sightingEventID(sighting) {
  const raw = String(sighting?.eventID ?? sighting?.eventId ?? "");
  const match = raw.match(/chess-results(?:-tnr|:)(\d+)/i);
  return match ? `chess-results:${match[1]}` : "";
}

function sightingHasPGN(sighting) {
  const eventID = sightingEventID(sighting);
  const event = eventCatalog?.find(item => item.id === eventID);
  return Boolean(event && (Number(event.gameCount) > 0 || Number(event.pgnCount) > 0 || event.detailPath));
}

function publicStatus(player) {
  if (player?.fideID || player?.publicIdentityStatus === "verified") return { key: "verified", label: "已核验" };
  if (player?.publicIdentityStatus === "same-name" || sameNameCount(player) > 1) return { key: "same-name", label: "同名待区分" };
  return { key: "pending", label: "待确认" };
}

function publicAgeLabel(player) {
  const birthYear = Number(player?.birthYear);
  if (!Number.isFinite(birthYear)) return "年龄组待补";
  const age = Number(data?.competitionYear ?? new Date().getFullYear()) - birthYear;
  if (age <= 18) return stageForPlayer(player)?.id ?? "青少年组";
  return `${birthYear} 出生`;
}

function publicStatusBadge(player) {
  const status = publicStatus(player);
  return `<span class="identity-status ${status.key}">${status.label}</span>`;
}

function eventDataStatus(event) {
  if (Number(event?.gameCount) > 0 || Number(event?.pgnCount) > 0) return "cached";
  if (event?.tournamentID || event?.url || event?.detailPath) return "compare";
  return "missing";
}

function dataStatusBadge(status) {
  const labels = { cached: "PGN 已缓存", compare: "待数据源比对", missing: "待补源" };
  return `<span class="data-status ${escapeAttribute(status)}">${escapeHTML(labels[status] || labels.missing)}</span>`;
}

function playerCoverageStatus(player, staticInfo, bulkInfo) {
  const games = Number(staticInfo?.gameCount ?? bulkInfo?.totalGames ?? player.gameCount ?? 0);
  const status = games > 0 ? "cached" : Number(player.eventCount ?? 0) > 0 ? "compare" : "missing";
  const message = status === "cached"
    ? `本库已缓存 ${games} 盘可复盘棋局。`
    : status === "compare"
    ? "已有赛事记录，但棋谱仍待与数据源比对。"
    : "目前只有注册信息，尚缺可复盘赛事来源。";
  return `<div class="coverage-callout">${dataStatusBadge(status)}<span>${escapeHTML(message)}</span>${status !== "cached" ? `<a href="./contribute.html?type=data-gap&player=${encodeURIComponent(player.fideID || playerKey(player))}&name=${encodeURIComponent(displayName(player))}">帮我们补全这名棋手</a>` : ""}</div>`;
}

function publicLocationFromSightings(sightings) {
  return uniqueStrings((sightings ?? []).map(publicLocationFromSighting).filter(Boolean))[0] || "";
}

function publicLocationFromSighting(sighting) {
  if (sighting?.province) return String(sighting.province);
  const text = String(sighting?.club ?? "");
  const places = ["北京", "上海", "天津", "重庆", "河北", "山西", "辽宁", "吉林", "黑龙江", "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南", "广东", "海南", "四川", "贵州", "云南", "陕西", "甘肃", "青海", "内蒙古", "广西", "西藏", "宁夏", "新疆", "香港", "澳门"];
  const province = places.find(place => text.includes(place));
  if (province) return province;
  return text.match(/[\u4e00-\u9fff]{2,4}市/)?.[0] || "";
}

function publicStageFromSighting(sighting) {
  if (sighting?.ageStage) return String(sighting.ageStage);
  const text = String(sighting?.group ?? "");
  const matches = [...text.matchAll(/(?:男子|女子)?(?:一级棋士[A-Z]?|候补棋协大师|候补|棋协大师|公开|U\s?\d{1,2}|[BG]\d{1,2})组?/gi)];
  return matches.at(-1)?.[0] || "组别待补";
}

function recordLocalGap(query) {
  const value = String(query ?? "").trim();
  if (!value) return;
  try {
    const key = "china-chess-local-demand-gaps-v1";
    const rows = JSON.parse(localStorage.getItem(key) || "[]");
    const normalized = normalize(value);
    const existing = rows.find(row => row.normalizedQuery === normalized);
    if (existing) {
      existing.demandCount = Number(existing.demandCount || 0) + 1;
      existing.lastRequestedAt = new Date().toISOString();
    } else {
      rows.push({ displayQuery: value, normalizedQuery: normalized, demandCount: 1, lastRequestedAt: new Date().toISOString() });
    }
    localStorage.setItem(key, JSON.stringify(rows.slice(-100)));
  } catch {
    // Search remains fully functional when local storage is disabled.
  }
}

function normalizedIdentityName(player) {
  return normalize(player.chineseName || player.displayName || player.name || "").replace(/[^0-9a-z\u4e00-\u9fff]/g, "");
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

function transferBadge(player) {
  if (!player.formerFederation && !player.transfer) return "";
  const type = player.transfer?.type ?? (player.federation !== "CHN" ? "transferred_out" : "transferred_in");
  const text = type === "transferred_out"
    ? `已转出 CHN → ${player.federation ?? "?"}`
    : `转入 ${player.formerFederation ?? "?"} → CHN`;
  return ` <span class="note-pill">${escapeHTML(text)}</span>`;
}

function detailChineseNameLine(player) {
  if (!player.chineseName || displayName(player).includes(player.chineseName)) return "";
  return `<div class="detail-cn-name">${escapeHTML(displayText(player.chineseName))}</div>`;
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
