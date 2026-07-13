const data = await fetch("./data/leaderboards.json").then(response => {
  if (!response.ok) throw new Error(`排行榜加载失败：HTTP ${response.status}`);
  return response.json();
});
const groups = data.groups ?? [];
const tabs = document.querySelector("#leaderboardTabs");
const page = document.querySelector("#leaderboardPage");
let active = groups[0]?.id || "OPEN";

tabs.innerHTML = groups.map(group => `<button type="button" role="tab" data-group="${escapeHTML(group.id)}">${escapeHTML(group.id === "OPEN" ? "成年" : group.label || group.id)}</button>`).join("");
tabs.addEventListener("click", event => {
  const button = event.target.closest("[data-group]");
  if (!button) return;
  active = button.dataset.group;
  render();
});
render();

function render() {
  const group = groups.find(item => item.id === active) || groups[0];
  tabs.querySelectorAll("[data-group]").forEach(button => button.setAttribute("aria-selected", String(button.dataset.group === active)));
  if (!group) { page.innerHTML = '<div class="empty-state">暂无排行数据</div>'; return; }
  const rows = (group.players ?? []).slice(0, 20).map((player, index) => {
    const rating = ratingFor(player);
    return `<a class="leaderboard-row-link" href="./?fideID=${encodeURIComponent(player.fideID)}">
      <span class="rank-badge">${index + 1}</span>
      <span><strong>${escapeHTML(player.displayName || player.chineseName || player.name)}</strong><small>FIDE ${escapeHTML(player.fideID)} · ${escapeHTML(player.title || "无称号")} · ${escapeHTML(player.age != null ? `${player.age} 岁` : "年龄待补")}</small></span>
      <span class="rating-value">${escapeHTML(rating ? rating.value : "—")}<small>${escapeHTML(rating?.kind || "无等级分")}</small></span>
    </a>`;
  }).join("");
  page.innerHTML = `<article class="leaderboard-card leaderboard-page-card"><div class="card-head"><div><h2 class="stage-title">${escapeHTML(group.id === "OPEN" ? "成年公开组" : group.label || group.id)}</h2><div class="stage-range">${escapeHTML(group.minAge != null ? `${group.minAge}${group.maxAge ? `–${group.maxAge}` : "+"} 岁` : "全年龄")}</div></div><span class="stage-chip">${Number(group.totalEligible || 0).toLocaleString("zh-CN")} 人</span></div>${rows || '<div class="empty-state compact">暂无排行数据</div>'}</article>`;
}

function ratingFor(player) {
  if (Number.isFinite(player.standard)) return { value: player.standard, kind: "标准棋" };
  if (Number.isFinite(player.rapid)) return { value: player.rapid, kind: "快棋" };
  if (Number.isFinite(player.blitz)) return { value: player.blitz, kind: "超快棋" };
  return null;
}
function escapeHTML(value) { const node = document.createElement("span"); node.textContent = String(value ?? ""); return node.innerHTML; }
