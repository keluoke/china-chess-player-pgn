const DATA_PATH = "./data/master-series-summary.json";
const STATUS_ORDER = ["full", "live", "partial", "missing", "none", "unknown"];

const elements = {
  content: document.querySelector("#reportContent"),
  error: document.querySelector("#reportError"),
  errorMessage: document.querySelector("#reportErrorMessage"),
  loading: document.querySelector("#reportLoading"),
  refreshButton: document.querySelector("#refreshButton"),
  refreshStatus: document.querySelector("#refreshStatus"),
  retryButton: document.querySelector("#retryButton"),
  kpis: document.querySelector("#masterKpis"),
  definition: document.querySelector("#reportDefinition"),
  snapshotTime: document.querySelector("#snapshotTime"),
  snapshotID: document.querySelector("#snapshotID"),
  yearJump: document.querySelector("#yearJump"),
  statusLegend: document.querySelector("#statusLegend"),
  resetFilter: document.querySelector("#resetFilter"),
  activeFilter: document.querySelector("#activeFilter"),
  yearSections: document.querySelector("#yearSections"),
};

let payload = null;
let statusFilter = "all";
let lastSnapshotID = "";

function escapeHTML(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

function formatNumber(value) {
  return Number(value || 0).toLocaleString("zh-CN");
}

function formatDate(value) {
  if (!value) return "日期待核";
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric" }).format(date);
}

function formatSnapshotTime(value) {
  if (!value) return "快照时间待核";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return `数据快照：${new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "long", day: "numeric", hour: "2-digit", minute: "2-digit",
  }).format(date)}`;
}

function assertPayload(data) {
  if (!data || data.schemaVersion !== 1 || !Array.isArray(data.years) || !data.totals) {
    throw new Error("汇总文件格式不兼容，请等待下一次仓库快照重建。");
  }
  return data;
}

function statusCount(code) {
  return Number(payload?.totals?.statusCounts?.[code] || 0);
}

function statusBadge(code, label) {
  return `<span class="pgn-badge status-${escapeHTML(code)}"><span class="badge-dot" aria-hidden="true"></span>${escapeHTML(label)}</span>`;
}

function renderKpis() {
  const totals = payload.totals;
  const cards = [
    ["收录年度", `${formatNumber(totals.years)} 年`, "2022—2026"],
    ["已入库赛站", `${formatNumber(totals.stations)} 站`, "各年度分别计数"],
    ["已入库组别", `${formatNumber(totals.groups)} 组`, "完整赛果详情"],
    ["全台 PGN 完整", `${formatNumber(statusCount("full"))} 组`, "全部实际对局"],
    ["仅直播台次 PGN", `${formatNumber(statusCount("live"))} 组`, "公开直播范围完整"],
    ["无 PGN", `${formatNumber(statusCount("none"))} 组`, "赛事赛果已入库"],
  ];
  elements.kpis.innerHTML = cards.map(([label, value, note], index) => `
    <article class="master-kpi ${index > 2 ? "pgn-kpi" : ""}">
      <span>${escapeHTML(label)}</span>
      <strong>${escapeHTML(value)}</strong>
      <small>${escapeHTML(note)}</small>
    </article>`).join("");
}

function renderYearJump() {
  elements.yearJump.innerHTML = payload.years.map((year) => `
    <a href="#year-${year.year}">
      <strong>${year.year}</strong>
      <span>${formatNumber(year.stationCount)} 站 · ${formatNumber(year.groupCount)} 组</span>
    </a>`).join("");
}

function renderLegend() {
  elements.statusLegend.innerHTML = STATUS_ORDER.map((code) => {
    const legend = payload.statusLegend?.[code];
    if (!legend) return "";
    const active = statusFilter === code;
    return `<button type="button" class="legend-card status-${escapeHTML(code)}${active ? " active" : ""}"
      data-status-filter="${escapeHTML(code)}" aria-pressed="${active}">
      <span class="legend-top">${statusBadge(code, legend.label)}<strong>${formatNumber(statusCount(code))} 组</strong></span>
      <span class="legend-description">${escapeHTML(legend.description)}</span>
    </button>`;
  }).join("");
  elements.statusLegend.querySelectorAll("[data-status-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      const next = button.dataset.statusFilter;
      statusFilter = statusFilter === next ? "all" : next;
      render();
    });
  });
}

function groupMatches(group) {
  return statusFilter === "all" || group.pgnStatus === statusFilter;
}

function coverageText(group) {
  const archived = group.archivedGames;
  const played = group.playedGames;
  if (group.pgnStatus === "none") return "0 盘归档";
  if (archived == null && played == null) return "盘数待核";
  if (played) return `${formatNumber(archived)} / ${formatNumber(played)} 盘 · 全台 ${group.allBoardCoveragePercent ?? 0}%`;
  return `${formatNumber(archived)} 盘归档`;
}

function groupRow(group) {
  const href = group.routeID ? `./?event=${encodeURIComponent(group.routeID)}` : "./events.html";
  const pending = group.groupLabelPending ? '<span class="pending-label">名称待核</span>' : "";
  const facts = [
    formatDate(group.date),
    group.participants != null ? `${formatNumber(group.participants)} 人` : "人数待核",
    group.rounds != null ? `${formatNumber(group.rounds)} 轮` : "轮次待核",
    group.tournamentID ? `编号 ${group.tournamentID}` : "",
  ].filter(Boolean).join(" · ");
  return `<li class="group-row">
    <div class="group-identity">
      <a href="${href}">${escapeHTML(group.groupLabel)}</a>${pending}
      <span>${escapeHTML(facts)}</span>
    </div>
    <div class="group-pgn">
      ${statusBadge(group.pgnStatus, group.pgnStatusLabel)}
      <span>${escapeHTML(coverageText(group))}</span>
    </div>
  </li>`;
}

function renderYears() {
  let visibleGroups = 0;
  let visibleStations = 0;
  elements.yearSections.innerHTML = payload.years.map((year, yearIndex) => {
    let stationIndex = 0;
    const stations = year.stations.map((station) => {
      const groups = station.groups.filter(groupMatches);
      if (!groups.length) return "";
      visibleGroups += groups.length;
      visibleStations += 1;
      stationIndex += 1;
      return `<article class="station-card">
        <header class="station-head">
          <div><span>赛站 ${String(stationIndex).padStart(2, "0")}</span><h3>${escapeHTML(station.station)}</h3></div>
          <strong>${formatNumber(groups.length)}<small> / ${formatNumber(station.groupCount)} 组</small></strong>
        </header>
        <ul class="group-list">${groups.map(groupRow).join("")}</ul>
      </article>`;
    }).filter(Boolean).join("");
    const visibleInYear = year.stations.reduce((total, station) => total + station.groups.filter(groupMatches).length, 0);
    return `<section class="year-section" id="year-${year.year}">
      <header class="year-header">
        <div><span class="section-index">0${yearIndex + 1}</span><h2>${year.year}<small> 年</small></h2></div>
        <p><strong>${formatNumber(year.stationCount)}</strong> 站 · <strong>${formatNumber(year.groupCount)}</strong> 组${statusFilter === "all" ? "" : ` · 当前显示 ${formatNumber(visibleInYear)} 组`}</p>
      </header>
      <div class="station-grid">${stations || '<div class="year-empty">该年份没有符合当前筛选的组别</div>'}</div>
    </section>`;
  }).join("");

  elements.resetFilter.hidden = statusFilter === "all";
  elements.activeFilter.textContent = statusFilter === "all"
    ? `当前展示全部 ${formatNumber(payload.totals.groups)} 个已入库组别。`
    : `筛选结果：${payload.statusLegend[statusFilter].label} · ${formatNumber(visibleGroups)} 组，分布于 ${formatNumber(visibleStations)} 个年度赛站。`;
}

function render() {
  renderKpis();
  renderYearJump();
  renderLegend();
  renderYears();
  elements.definition.textContent = payload.definition;
  elements.snapshotTime.textContent = formatSnapshotTime(payload.generatedAt);
  elements.snapshotID.textContent = payload.snapshotId ? `快照 ${payload.snapshotId}` : "快照编号待核";
}

async function refresh({ initial = false } = {}) {
  elements.refreshButton.disabled = true;
  elements.refreshButton.setAttribute("aria-busy", "true");
  elements.refreshStatus.textContent = initial ? "正在读取最新快照…" : "正在绕过缓存重新读取…";
  if (initial) elements.loading.hidden = false;
  elements.error.hidden = true;
  try {
    const url = new URL(DATA_PATH, window.location.href);
    url.searchParams.set("resolve", String(Date.now()));
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) throw new Error(`服务器返回 ${response.status}`);
    const next = assertPayload(await response.json());
    const changed = lastSnapshotID && lastSnapshotID !== next.snapshotId;
    lastSnapshotID = next.snapshotId || "";
    payload = next;
    render();
    elements.content.hidden = false;
    elements.loading.hidden = true;
    const now = new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date());
    elements.refreshStatus.textContent = changed ? `已更新到新快照 · ${now}` : `已是仓库最新快照 · ${now}`;
  } catch (error) {
    elements.loading.hidden = true;
    if (!payload) elements.content.hidden = true;
    elements.error.hidden = false;
    elements.errorMessage.textContent = error instanceof Error ? error.message : "未知读取错误";
    elements.refreshStatus.textContent = "刷新失败，已保留当前页面内容";
  } finally {
    elements.refreshButton.disabled = false;
    elements.refreshButton.removeAttribute("aria-busy");
  }
}

elements.refreshButton.addEventListener("click", () => refresh());
elements.retryButton.addEventListener("click", () => refresh({ initial: !payload }));
elements.resetFilter.addEventListener("click", () => { statusFilter = "all"; render(); });

refresh({ initial: true });
