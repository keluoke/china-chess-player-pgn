const [coverage, sourceCoverage, eventQueue, quality, dashboard, changelog] = await Promise.all([
  fetchJSON("./data/audit/player-coverage.json"),
  fetchJSON("./data/audit/source-coverage.json"),
  fetchJSON("./data/audit/domestic-event-queue.json"),
  fetchJSON("./data/audit/data-quality-review.json"),
  fetchJSON("./data/dashboard.json"),
  fetchJSON("./data/changelog.json")
]);
const funnel = await fetchJSON("./data/contribution-funnel.json").catch(() => null);

const totals = coverage?.totals ?? {};
document.querySelector("#coverageHeadline").innerHTML = [
  ["注册棋手", totals.registryPlayers],
  ["已有 PGN", totals.playersWithPgn],
  ["总体覆盖", totals.coveragePercent != null ? `${totals.coveragePercent}%` : null],
  ["收录棋局", sourceCoverage?.totals?.games]
].filter(([, value]) => value != null).map(([label, value]) => `<div><strong>${escapeHTML(format(value))}</strong><span>${escapeHTML(label)}</span></div>`).join("");

document.querySelector("#coverageUpdated").textContent = `更新于 ${formatTime(coverage?.generatedAt)}`;

const recentEvents = dashboard?.recentEvents ?? [];
document.querySelector("#recentEventsMeta").textContent = recentEvents.length ? `最近 ${recentEvents.length} 项` : "";
document.querySelector("#coverageRecentEvents").innerHTML = recentEvents.length ? recentEvents.map(event => `<a class="recent-event" href="./?event=${encodeURIComponent(event.id)}"><div><strong>${escapeHTML(event.displayName || event.name || "未命名赛事")}</strong><span>${escapeHTML([event.date, event.playerCount ? `${event.playerCount} 位中国棋手` : "", event.gameCount ? `${event.gameCount} 盘` : ""].filter(Boolean).join(" · "))}</span></div></a>`).join("") : empty("暂无赛事更新");

const changes = (changelog?.entries ?? []).slice(0, 8);
document.querySelector("#coverageChangelogMeta").textContent = changes.length ? `最近 ${changes.length} 次` : "";
document.querySelector("#coverageChangelog").innerHTML = changes.length ? changes.map(entry => {
  const delta = entry.delta ?? {};
  const parts = [];
  if (delta.games) parts.push(`对局 ${delta.games > 0 ? "+" : ""}${format(delta.games)}`);
  if (delta.playersWithGames) parts.push(`有棋谱棋手 ${delta.playersWithGames > 0 ? "+" : ""}${format(delta.playersWithGames)}`);
  if (delta.withChineseName) parts.push(`中文名 ${delta.withChineseName > 0 ? "+" : ""}${format(delta.withChineseName)}`);
  return `<div class="cl-row"><span>${escapeHTML(parts.join(" · ") || "数据索引重建")}</span><span class="cl-date">${escapeHTML(String(entry.date || "").slice(0, 10))}</span></div>`;
}).join("") : empty("暂无更新记录");
document.querySelector("#stageCoverage").innerHTML = Object.entries(coverage?.stageCoverage ?? {}).map(([stage, row]) => `
  <div class="coverage-row"><strong>${escapeHTML(stage)}</strong><div class="coverage-track"><span style="width:${Math.max(1, Number(row.coveragePercent || 0))}%"></span></div><span>${escapeHTML(String(row.playersWithPgn ?? 0))} / ${escapeHTML(String(row.players ?? 0))} · ${escapeHTML(String(row.coveragePercent ?? 0))}%</span></div>`).join("") || empty("暂无年龄段覆盖数据");

const queueTotals = eventQueue?.totals ?? {};
const nextTargets = (eventQueue?.targets ?? []).slice(0, 5);
document.querySelector("#eventQueue").innerHTML = `
  <div class="mini-stat-row"><span><strong>${format(queueTotals.registered || 0)}</strong>待整取</span><span><strong>${format(queueTotals.captured || 0)}</strong>已抓取</span><span><strong>${format(queueTotals.snapshotAudited || 0)}</strong>有快照哈希</span></div>
  <ol class="queue-list">${nextTargets.map(item => `<li><div><strong>${escapeHTML(item.eventName || `tnr${item.tournamentID}`)}</strong><span>${escapeHTML(item.category || "国内赛事")} · tnr${escapeHTML(item.tournamentID)}</span></div><b>${escapeHTML(String(item.priorityScore))}</b></li>`).join("")}</ol>`;

const qualityTotals = quality?.totals ?? {};
const issues = quality?.issues ?? [];
document.querySelector("#qualityQueue").innerHTML = `
  <div class="mini-stat-row"><span><strong>${format(qualityTotals.eventsScanned || 0)}</strong>赛事已检查</span><span><strong>${format(qualityTotals.eventDetailsScanned || 0)}</strong>完整赛果</span><span><strong>${format(qualityTotals.issues || 0)}</strong>待审核</span></div>
  ${issues.length ? `<ul class="quality-list">${issues.slice(0, 5).map(issue => `<li><strong>${escapeHTML(issue.type)}</strong><span>${escapeHTML(issue.eventID || issue.canonicalEventID || "待定位")}</span></li>`).join("")}</ul>` : `<div class="quality-clear">当前快照未发现规则异常</div>`}`;

const funnelTotals = funnel?.totals ?? {};
document.querySelector("#contributionFunnel").innerHTML = funnel ? `
  <div class="funnel-grid">
    ${funnelStep("工具下载", funnelTotals.toolDownloads)}
    ${funnelStep("抓取成功", funnelTotals.successfulCaptures)}
    ${funnelStep("贡献 PR", funnelTotals.pullRequests)}
    ${funnelStep("审核入库", funnelTotals.ingested)}
    ${funnelStep("网页 Issue", funnelTotals.webIssues)}
  </div>
  <p class="coverage-note">统计口径随数据公开，更新时间 ${escapeHTML(String(funnel.generatedAt || "").slice(0, 10))}。</p>` : empty("贡献漏斗将在首次定时统计后显示");

async function fetchJSON(path) {
  const response = await fetch(path);
  return response.ok ? response.json() : null;
}
function format(value) { return typeof value === "number" ? value.toLocaleString("zh-CN") : String(value); }
function funnelStep(label, value) { return `<div class="funnel-step"><span>${escapeHTML(label)}</span><strong>${format(Number(value || 0))}</strong></div>`; }
function formatTime(value) { return value ? new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "待重建"; }
function empty(text) { return `<div class="empty-state compact">${escapeHTML(text)}</div>`; }
function escapeHTML(value) { const node = document.createElement("span"); node.textContent = String(value ?? ""); return node.innerHTML; }
