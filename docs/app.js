const state = {
  activeStage: "ALL",
  selectedFideID: null,
  selectedEventIDs: new Set(),
  downloadStatus: "",
  query: ""
};

const els = {
  playerCount: document.querySelector("#playerCount"),
  stageCount: document.querySelector("#stageCount"),
  eventCount: document.querySelector("#eventCount"),
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

initialize();

async function loadData() {
  try {
    const youth = await fetchJSON("./data/youth-leaderboards.json", true);
    const [manifest, indexedPlayers] = await Promise.all([
      fetchJSON("./data/index/manifest.json", false),
      fetchJSON("./data/index/players.json", false)
    ]);
    return {
      ...youth,
      manifest,
      players: mergePlayers(youth.players ?? [], indexedPlayers ?? [])
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

function mergePlayers(leaderboardPlayers, indexedPlayers) {
  const byFide = new Map();
  leaderboardPlayers.forEach(player => byFide.set(String(player.fideID), { ...player }));

  indexedPlayers.forEach(indexed => {
    const fideID = String(indexed.fideID);
    const current = byFide.get(fideID) ?? {};
    byFide.set(fideID, {
      ...indexed,
      ...current,
      detailPath: indexed.detailPath ?? current.detailPath,
      eventCount: indexed.eventCount ?? current.eventCount,
      pgnCount: indexed.pgnCount ?? current.pgnCount,
      gameCount: indexed.gameCount ?? current.gameCount,
      displayName: current.displayName ?? indexed.displayName,
      name: current.name ?? indexed.name ?? indexed.displayName ?? `FIDE ${fideID}`,
      chineseName: current.chineseName ?? indexed.chineseName,
      pinyin: current.pinyin ?? indexed.pinyin
    });
  });

  return [...byFide.values()];
}

function initialize() {
  state.selectedFideID = rankingsForStage("U18")[0]?.fideID ?? players[0]?.fideID ?? null;
  resetSelectedEvents();
  els.searchInput.addEventListener("input", event => {
    state.query = event.target.value.trim();
    renderSearch();
  });

  render();
}

function render() {
  const eventCount = data.manifest?.totals?.events
    ?? players.reduce((sum, player) => sum + (player.eventCount ?? player.events?.length ?? 0), 0);
  els.playerCount.textContent = String(data.manifest?.totals?.players ?? players.length);
  els.stageCount.textContent = String(stages.length);
  els.eventCount.textContent = String(eventCount);
  els.ageRuleText.textContent = ageRuleText();
  els.rankingMeta.textContent = `${data.competitionYear} 年 · ${state.activeStage === "ALL" ? "全组" : state.activeStage}`;

  renderTabs();
  renderLeaderboards();
  renderSearch();
  renderDetail();
}

function renderTabs() {
  const tabs = [{ id: "ALL", label: "全部" }, ...stages.map(stage => ({ id: stage.id, label: stage.id }))];
  els.stageTabs.innerHTML = tabs.map(tab => `
    <button type="button" role="tab" aria-selected="${state.activeStage === tab.id}" data-stage="${tab.id}">
      ${escapeHTML(tab.label)}
    </button>
  `).join("");

  els.stageTabs.querySelectorAll("button").forEach(button => {
    button.addEventListener("click", () => {
      state.activeStage = button.dataset.stage;
      render();
    });
  });
}

function renderLeaderboards() {
  const visibleStages = state.activeStage === "ALL"
    ? stages
    : stages.filter(stage => stage.id === state.activeStage);

  els.leaderboardGrid.innerHTML = visibleStages.map(stage => leaderboardCard(stage)).join("");
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

function leaderboardCard(stage) {
  const entries = rankingsForStage(stage.id);
  const maxRating = Math.max(...entries.map(entry => entry.rating.value), 1);
  const rows = entries.map((entry, index) => {
    const player = entry.player;
    const note = liChengzhiNote(player, stage.id);
    const width = Math.max(6, Math.round((entry.rating.value / maxRating) * 100));
    return `
      <tr data-fide="${escapeAttribute(player.fideID)}" role="button" tabindex="0">
        <td class="rank-cell"><span class="rank-badge">${index + 1}</span></td>
        <td>
          <div class="player-name">${escapeHTML(displayName(player))}</div>
          <div class="player-meta">FIDE ${escapeHTML(player.fideID)} · ${player.birthYear} 出生</div>
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
          <h2 class="stage-title">${escapeHTML(stage.id)}</h2>
          <div class="stage-range">${escapeHTML(stage.birthYears)} 出生 · ${stage.lowerAge}-${stage.upperAge} 岁</div>
        </div>
        <span class="stage-chip">FIDE</span>
      </div>
      <table class="leaderboard-table">
        <tbody>${rows}</tbody>
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

  const stage = stageForPlayer(player);
  const note = stage ? liChengzhiNote(player, stage.id) : null;
  const events = [...(player.events ?? [])].sort((a, b) => (b.date ?? "").localeCompare(a.date ?? ""));
  const pgnEvents = events.filter(event => event.pgnPath);
  const selectedEvents = pgnEvents.filter(event => state.selectedEventIDs.has(eventKey(event)));
  const totalGames = pgnEvents.reduce((sum, event) => sum + (Number(event.gameCount) || 0), 0);
  const topThree = events.filter(event => Number(event.rank) > 0 && Number(event.rank) <= 3).length;

  els.detailPane.innerHTML = `
    <div class="detail-title">
      <div>
        <h2>${escapeHTML(displayName(player))}</h2>
        <p>FIDE ${escapeHTML(player.fideID)} · ${player.birthYear} 出生 · ${stage?.id ?? "未到 U8"}</p>
      </div>
      <span class="stage-chip">${escapeHTML(stage?.id ?? "-")}</span>
    </div>

    ${note ? `<span class="note-pill">${escapeHTML(note)}</span>` : ""}

    <div class="rating-grid">
      ${ratingCard("STD", player.standard)}
      ${ratingCard("RAP", player.rapid)}
      ${ratingCard("BLZ", player.blitz)}
    </div>

    <div class="dashboard-grid">
      ${metricTile("赛事", events.length)}
      ${metricTile("PGN", pgnEvents.length)}
      ${metricTile("棋局", totalGames)}
      ${metricTile("前三", topThree)}
    </div>

    <div class="stage-strip">
      ${stages.map(stageItem => stageTile(player, stageItem)).join("")}
    </div>

    <div class="detail-actions">
      <button class="primary-action" type="button" id="downloadSelectedPGN" ${selectedEvents.length ? "" : "disabled"}>↓ 下载选中 PGN</button>
      <button class="tool-button" type="button" id="selectAllPGN" ${pgnEvents.length ? "" : "disabled"}>全选 PGN</button>
      <button class="tool-button" type="button" id="clearPGNSelection" ${selectedEvents.length ? "" : "disabled"}>清空</button>
      <a class="action-link" href="https://ratings.fide.com/profile/${encodeURIComponent(player.fideID)}" target="_blank" rel="noreferrer">↗ FIDE</a>
      <a class="action-link" href="https://lichess.org/fide/${encodeURIComponent(player.fideID)}" target="_blank" rel="noreferrer">↗ Lichess FIDE</a>
    </div>

    <div class="download-status" aria-live="polite">${escapeHTML(downloadLine(selectedEvents, pgnEvents))}</div>

    <div class="event-list">
      ${events.length ? events.map(eventRow).join("") : `<div class="event-row"><strong>暂无本地赛事种子</strong><span>macOS 版可继续联网补齐 Chess-Results 和 PGN 缓存。</span></div>`}
    </div>
  `;

  wireDetailActions(player, pgnEvents);
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

function stageTile(player, stage) {
  const age = data.competitionYear - player.birthYear;
  const status = age > stage.upperAge ? "已完成" : age >= stage.lowerAge ? "进行中" : "未完待续";
  const events = (player.events ?? []).filter(event => stageForEvent(player, event)?.id === stage.id);
  const bestRank = events
    .map(event => Number(event.rank))
    .filter(rank => Number.isFinite(rank) && rank > 0)
    .sort((a, b) => a - b)[0];
  const active = stageForPlayer(player)?.id === stage.id;
  return `
    <div class="stage-tile ${active ? "active" : ""}">
      <strong>${escapeHTML(stage.id)}</strong>
      <span>${escapeHTML(status)}</span>
      <small>${bestRank ? `最好第 ${bestRank}` : `${events.length} 赛`}</small>
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

function downloadLine(selectedEvents, pgnEvents) {
  if (state.downloadStatus) return state.downloadStatus;
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

function eventKey(event) {
  return event.id ?? `${event.source ?? "event"}:${event.tournamentID ?? event.name}:${event.date ?? ""}`;
}

function resetSelectedEvents() {
  const player = selectedPlayer();
  state.selectedEventIDs = new Set((player?.events ?? []).filter(event => event.pgnPath).map(eventKey));
}

function selectPlayer(fideID) {
  state.selectedFideID = fideID;
  state.downloadStatus = "";
  resetSelectedEvents();
  renderDetail();
}

function rankingsForStage(stageID) {
  return players
    .filter(player => stageForPlayer(player)?.id === stageID)
    .map(player => ({ player, rating: ratingForPlayer(player) }))
    .filter(entry => entry.rating)
    .sort((a, b) => {
      if (a.rating.value !== b.rating.value) return b.rating.value - a.rating.value;
      if (a.rating.priority !== b.rating.priority) return a.rating.priority - b.rating.priority;
      return displayName(a.player).localeCompare(displayName(b.player), "zh-Hans-CN");
    })
    .slice(0, 5)
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

function ageRuleText() {
  const ranges = stages
    .map(stage => `${stage.id}=${stage.birthYears} 出生`)
    .join(" · ");
  return `${data.ageRule.title}：${data.ageRule.description}${data.competitionYear} 年口径为 ${ranges}。`;
}

function normalize(value) {
  return String(value ?? "")
    .normalize("NFKD")
    .toLowerCase()
    .replace(/[\s,.'’"()，。·_\-]+/g, "");
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
