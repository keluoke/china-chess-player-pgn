const state = {
  activeStage: "ALL",
  selectedFideID: null,
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

initialize();

async function loadData() {
  try {
    const response = await fetch("./data/youth-leaderboards.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  } catch (error) {
    document.body.innerHTML = `<main class="empty-state">无法加载静态数据：${escapeHTML(error.message)}</main>`;
    throw error;
  }
}

function initialize() {
  state.selectedFideID = rankingsForStage("U18")[0]?.fideID ?? players[0]?.fideID ?? null;
  els.searchInput.addEventListener("input", event => {
    state.query = event.target.value.trim();
    renderSearch();
  });

  render();
}

function render() {
  const eventCount = players.reduce((sum, player) => sum + (player.events?.length ?? 0), 0);
  els.playerCount.textContent = String(players.length);
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
  const player = players.find(item => item.fideID === state.selectedFideID);
  if (!player) {
    els.detailPane.innerHTML = `<div class="empty-state">请选择棋手</div>`;
    return;
  }

  const stage = stageForPlayer(player);
  const note = stage ? liChengzhiNote(player, stage.id) : null;
  const events = [...(player.events ?? [])].sort((a, b) => (b.date ?? "").localeCompare(a.date ?? ""));

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

    <div class="detail-actions">
      <a class="action-link" href="https://ratings.fide.com/profile/${encodeURIComponent(player.fideID)}" target="_blank" rel="noreferrer">↗ FIDE</a>
      <a class="action-link" href="https://lichess.org/fide/${encodeURIComponent(player.fideID)}" target="_blank" rel="noreferrer">↗ Lichess FIDE</a>
      <a class="action-link" href="data/youth-leaderboards.json" download>↓ 数据 JSON</a>
    </div>

    <div class="event-list">
      ${events.length ? events.map(eventRow).join("") : `<div class="event-row"><strong>暂无本地赛事种子</strong><span>macOS 版可继续联网补齐 Chess-Results 和 PGN 缓存。</span></div>`}
    </div>
  `;
}

function ratingCard(label, value) {
  return `
    <div class="rating-card">
      <span>${label}</span>
      <strong>${value ?? "-"}</strong>
    </div>
  `;
}

function eventRow(event) {
  const rank = event.rank ? `第 ${event.rank}` : "-";
  const size = event.rounds && event.participants ? `${event.rounds} 轮 · ${event.participants} 人` : "";
  return `
    <div class="event-row">
      <strong>${escapeHTML(event.name)}</strong>
      <span>${escapeHTML(event.date ?? "未知日期")} · ${escapeHTML(rank)} · ${escapeHTML(size)}</span>
    </div>
  `;
}

function selectPlayer(fideID) {
  state.selectedFideID = fideID;
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
  return player.chineseName ? `${player.chineseName} · ${player.name}` : player.name;
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
