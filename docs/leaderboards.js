import {
  applyPresentationName,
  buildPresentationNameIndex,
  presentationNameBadge,
  resolvePlayerDisplayName
} from "./presentation-names.js";

const EXPECTED_SCHEMA_VERSION = 2;
const [data, presentationPayload] = await loadLeaderboardPageData();

async function loadLeaderboardPageData() {
  try {
    const snapshotURL = new URL("./data/snapshot.json", location.href);
    snapshotURL.searchParams.set("resolve", String(Date.now()));
    const snapshot = await fetch(snapshotURL, { cache: "no-store" }).then(response => {
      if (!response.ok) throw new Error(`快照版本加载失败：HTTP ${response.status}`);
      return response.json();
    });
    const snapshotId = String(snapshot?.snapshotId || "").trim();
    if (!snapshotId) throw new Error("快照版本缺失");
    const versioned = path => {
      const url = new URL(path, location.href);
      url.searchParams.set("v", snapshotId);
      return url;
    };
    const payloads = await Promise.all([
      fetch(versioned("./data/leaderboards.json")).then(response => {
        if (!response.ok) throw new Error(`排行榜加载失败：HTTP ${response.status}`);
        return response.json();
      }),
      fetch(versioned("./data/identity/presentation-names.json"))
        .then(response => response.ok ? response.json() : null)
        .catch(() => null)
    ]);
    assertLeaderboardSchema(payloads[0]);
    return payloads;
  } catch (error) {
    document.querySelector(".leaderboard-filters")?.setAttribute("hidden", "");
    document.querySelector("#leaderboardTabs")?.setAttribute("hidden", "");
    const target = document.querySelector("#leaderboardPage");
    if (target) {
      target.innerHTML = `<div class="empty-state">排行榜数据版本不匹配或加载失败，请刷新后重试。<small>${escapeHTML(error.message)}</small></div>`;
    }
    throw error;
  }
}

function assertLeaderboardSchema(payload) {
  const controls = payload?.controls;
  const sexes = payload?.sexes;
  const groups = payload?.groups;
  if (payload?.schemaVersion !== EXPECTED_SCHEMA_VERSION
      || !Array.isArray(controls) || !controls.length
      || !Array.isArray(sexes) || !sexes.length
      || !Array.isArray(groups) || !groups.length) {
    throw new Error(`排行榜数据版本不匹配（需要 schema v${EXPECTED_SCHEMA_VERSION}）`);
  }
  for (const group of groups) {
    for (const control of controls) {
      for (const sex of sexes) {
        const ranking = group?.rankings?.[control.id]?.[sex.id];
        if (!ranking || !Array.isArray(ranking.players)
            || !ranking.birthYears || Array.isArray(ranking.birthYears)) {
          throw new Error(`排行榜维度缺失：${group.id}/${control.id}/${sex.id}`);
        }
      }
    }
  }
}

/* Presentation names are optional; leaderboard dimensions are not. */
const presentationNames = buildPresentationNameIndex(presentationPayload);
const groups = data.groups;
const params = new URLSearchParams(location.search);
const state = {
  cohort: valid(params.get("cohort"), groups.map(group => group.id), "OPEN"),
  control: valid(params.get("control"), data.controls.map(item => item.id), "standard"),
  sex: valid(params.get("sex"), data.sexes.map(item => item.id), "all"),
  birthYear: /^\d{4}$/.test(params.get("birthYear") || "") ? params.get("birthYear") : ""
};

const tabs = document.querySelector("#leaderboardTabs");
const page = document.querySelector("#leaderboardPage");
const birthYears = document.querySelector("#birthYearFilters");
const scopeNote = document.querySelector("#leaderboardScopeNote");
const controls = document.querySelector("#controlFilters");
const sexes = document.querySelector("#sexFilters");

controls.innerHTML = data.controls.map(item => filterButton("control", item.id, item.label)).join("");
sexes.innerHTML = data.sexes.map(item => filterButton("sex", item.id, item.label)).join("");

const sections = [
  ["青少年组", ["U8", "U10", "U12", "U14", "U16", "U18", "U20"]],
  ["成年组", ["OPEN"]],
  ["元老组", ["S50", "S65"]]
];

document.querySelector(".leaderboard-filters").addEventListener("click", event => {
  const button = event.target.closest("[data-filter]");
  if (!button) return;
  state[button.dataset.filter] = button.dataset.value;
  state.birthYear = "";
  updateURL();
  renderAll();
});
tabs.addEventListener("click", event => {
  const button = event.target.closest("[data-cohort]");
  if (!button) return;
  state.cohort = button.dataset.cohort;
  state.birthYear = "";
  updateURL();
  renderAll();
});
birthYears.addEventListener("click", event => {
  const button = event.target.closest("[data-birth-year]");
  if (!button) return;
  state.birthYear = button.dataset.birthYear;
  updateURL();
  renderAll();
});

renderAll();

function renderAll() {
  const group = groups.find(item => item.id === state.cohort) || groups.find(item => item.id === "OPEN") || groups[0];
  renderCohortTabs();
  controls.querySelectorAll("[data-filter=control]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.value === state.control)));
  sexes.querySelectorAll("[data-filter=sex]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.value === state.sex)));
  tabs.querySelectorAll("[data-cohort]").forEach(button => button.setAttribute("aria-selected", String(button.dataset.cohort === group?.id)));
  renderBirthYears(group);
  render(group);
}

function renderCohortTabs() {
  tabs.innerHTML = sections.map(([label, ids]) => {
    const buttons = ids.map(id => {
      const group = groups.find(item => item.id === id);
      if (!group) return "";
      const count = rankingFor(group).totalEligible ?? group.totalEligible ?? 0;
      return `<button type="button" role="tab" data-cohort="${escapeHTML(id)}">${escapeHTML(id === "OPEN" ? "公开" : id)} <small>${Number(count).toLocaleString("zh-CN")}</small></button>`;
    }).join("");
    return `<div class="stage-tab-section"><span>${label}</span><div>${buttons}</div></div>`;
  }).join("");
}

function renderBirthYears(group) {
  const ranking = rankingFor(group);
  const entries = Object.entries(ranking.birthYears ?? {});
  const youth = /^U(?:8|10|12|14|16|18|20)$/.test(group?.id || "");
  birthYears.hidden = !youth || !entries.length;
  if (birthYears.hidden) {
    state.birthYear = "";
    birthYears.innerHTML = "";
    return;
  }
  birthYears.innerHTML = `<span>出生年份</span><div class="filter-chips">
    <button type="button" data-birth-year="" aria-pressed="${String(!state.birthYear)}">组内全部</button>
    ${entries.map(([year, bucket]) => `<button type="button" data-birth-year="${year}" aria-pressed="${String(state.birthYear === year)}">${year} <small>${Number(bucket.totalEligible || 0).toLocaleString("zh-CN")}</small>${Number(bucket.totalEligible || 0) < 20 ? `<em>样本较少</em>` : ""}</button>`).join("")}
  </div>`;
}

function render(group) {
  if (!group) {
    page.innerHTML = '<div class="empty-state">暂无排行数据</div>';
    return;
  }
  const ranking = rankingFor(group);
  const selected = state.birthYear ? ranking.birthYears?.[state.birthYear] : ranking;
  const all = selected?.players ?? [];
  const rows = all.slice(0, 20).map(rowFor).join("");
  const rest = all.slice(20).map((player, index) => rowFor(player, index + 20)).join("");
  const total = Number(selected?.totalEligible || 0);
  const label = group.id === "OPEN" ? "成年公开组" : group.label || group.id;
  const ageText = group.minAge != null ? `${group.minAge}${group.maxAge ? `–${group.maxAge}` : "+"} 岁` : "全年龄";
  const birthText = state.birthYear ? `${state.birthYear} 年出生 · ${data.basisYear} 年为 ${group.id}` : ageText;
  const missing = Number(data.birthYearMissing?.[state.control]?.[state.sex] || 0);
  scopeNote.textContent = state.birthYear
    ? `本榜仅含有 FIDE 注册信息且出生年份明确的棋手；未注册 FIDE 的棋手不参与本维度排序。${missing ? `另有 ${missing} 名有分棋手出生年待补。` : ""}`
    : "官方榜仅使用 registry 中对应棋种的 FIDE 等级分；不同棋种不会互相回退。";
  const more = rest ? `<details class="leaderboard-more"><summary>查看第 21–${all.length} 名</summary>${rest}</details>` : "";
  page.innerHTML = `<article class="leaderboard-card leaderboard-page-card">
    <div class="card-head"><div><h2 class="stage-title">${escapeHTML(label)}</h2><div class="stage-range">${escapeHTML(birthText)}</div></div><span class="stage-chip">${total.toLocaleString("zh-CN")} 人</span></div>
    ${rows || '<div class="empty-state compact">当前筛选暂无官方等级分记录。</div>'}${more}
  </article>`;
}

function rankingFor(group) {
  return group?.rankings?.[state.control]?.[state.sex]
    || { totalEligible: 0, players: [], birthYears: {} };
}

function rowFor(player, index) {
  applyPresentationName(player, presentationNames.get(String(player.fideID)));
  const rating = player[state.control];
  const controlLabel = data.controls.find(item => item.id === state.control)?.label || state.control;
  return `<a class="leaderboard-row-link" href="./?fideID=${encodeURIComponent(player.fideID)}">
    <span class="rank-badge">${index + 1}</span>
    <span><strong>${escapeHTML(resolvePlayerDisplayName(player))}</strong>${presentationBadgeHTML(player)}<small>FIDE ${escapeHTML(player.fideID)} · ${escapeHTML(player.title || "无称号")} · ${escapeHTML(player.birthYear ? `${player.birthYear} 年出生` : "出生年待补")}</small></span>
    <span class="rating-value">${escapeHTML(rating ?? "—")}<small>${escapeHTML(controlLabel)}</small></span>
  </a>`;
}

function filterButton(filter, value, label) {
  return `<button type="button" data-filter="${filter}" data-value="${value}" aria-pressed="false">${escapeHTML(label)}</button>`;
}

function updateURL() {
  const url = new URL(location.href);
  url.searchParams.set("track", "official");
  url.searchParams.set("cohort", state.cohort);
  url.searchParams.set("control", state.control);
  url.searchParams.set("sex", state.sex);
  if (state.birthYear) url.searchParams.set("birthYear", state.birthYear);
  else url.searchParams.delete("birthYear");
  history.replaceState(null, "", url);
}

function valid(value, allowed, fallback) {
  return allowed.includes(value) ? value : fallback;
}

function presentationBadgeHTML(player) {
  const badge = presentationNameBadge(player);
  if (!badge) return "";
  return `<span class="identity-status ${badge.key}" title="${escapeHTML(badge.title)}">${escapeHTML(badge.label)}</span>`;
}

function escapeHTML(value) {
  const node = document.createElement("span");
  node.textContent = String(value ?? "");
  return node.innerHTML;
}
