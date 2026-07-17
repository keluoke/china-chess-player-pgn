// 公开赛事目录页逻辑（从 events.html 内联脚本迁出，2026-07）
(async function () {
  const PAGE_SIZE = 100;
  const $ = (s) => document.querySelector(s);
  const state = { tab: "past", scope: "all", series: "", year: "", search: "", page: 0 };
  let events = [];
  let seriesLabels = {};

  function esc(v) {
    return String(v ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  }

  try {
    const resp = await fetch("./data/index/public-events.json");
    const payload = await resp.json();
    events = payload.events || [];
    seriesLabels = payload.series || {};
  } catch (err) {
    $("#eventsStatus").textContent = "公开赛事目录加载失败：" + err;
    return;
  }

  const today = new Date().toISOString().slice(0, 10);
  for (const item of events) {
    item._bucket = !item.date ? "unknown" : item.date > today ? "future" : "past";
  }
  events.sort((a, b) => String(b.date || "").localeCompare(String(a.date || "")) || String(a.displayName || "").localeCompare(String(b.displayName || "")));

  $("#seriesFilter").innerHTML += Object.entries(seriesLabels).map(([k, v]) => `<option value="${esc(k)}">${esc(v)}</option>`).join("");
  const years = [...new Set(events.map((e) => e.year).filter(Boolean))].sort().reverse();
  $("#yearFilter").innerHTML += years.map((y) => `<option value="${esc(y)}">${esc(y)}</option>`).join("");

  function filtered() {
    const term = state.search.trim().toLowerCase();
    return events.filter((e) => {
      if (e._bucket !== state.tab) return false;
      if (state.scope === "full" && e.detailStatus !== "published") return false;
      if (state.series && e.series !== state.series) return false;
      if (state.year && String(e.year) !== state.year) return false;
      if (term) {
        const hay = `${e.displayName || ""} ${e.name || ""} ${e.chineseName || ""} ${(e.aliases || []).join(" ")} ${e.groupLabel || ""} ${e.station || ""} ${e.tournamentID || ""}`.toLowerCase();
        if (!hay.includes(term)) return false;
      }
      return true;
    });
  }

  function render() {
    const rows = filtered();
    const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
    state.page = Math.min(state.page, pages - 1);
    const slice = rows.slice(state.page * PAGE_SIZE, (state.page + 1) * PAGE_SIZE);
    $("#eventsBody").innerHTML = slice.map((e) => {
      const link = `./?event=${encodeURIComponent(e.id)}`;
      const tags = (e.tournamentID ? `<span class="tag">tnr${esc(e.tournamentID)}</span>` : "") +
        (e.roundsPendingVerification ? '<span class="tag pending">逐轮待核验</span>' : "");
      const detail = e.detailStatus === "published"
        ? '<span class="tag full">完整赛果</span>'
        : '<span class="tag">元数据</span>';
      return `<tr><td>${esc(e.date || (e.year ? e.year + " 年" : "待补"))}</td><td><a href="${link}">${esc(e.displayName || e.name || e.id)}</a>${tags}</td><td>${esc(seriesLabels[e.series] || e.series)}</td><td>${esc(e.groupLabel || "-")}</td><td>${esc(e.participants || e.playerCount || "-")}</td><td>${esc(e.rounds || "-")}</td><td>${detail}</td></tr>`;
    }).join("") || '<tr><td colspan="7">该筛选下没有赛事</td></tr>';
    $("#countInfo").textContent = `共 ${rows.length} 场`;
    $("#pageInfo").textContent = `第 ${state.page + 1} / ${pages} 页`;
    $("#prevPage").disabled = state.page <= 0;
    $("#nextPage").disabled = state.page >= pages - 1;
    $("#eventsStatus").hidden = true;
    $("#eventsTable").hidden = false;
    $("#pager").hidden = false;
  }

  document.querySelectorAll("#timeTabs button").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#timeTabs button").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.tab = btn.dataset.tab;
      state.page = 0;
      render();
    });
  });
  $("#scopeFilter").addEventListener("change", (e) => { state.scope = e.target.value; state.page = 0; render(); });
  $("#seriesFilter").addEventListener("change", (e) => { state.series = e.target.value; state.page = 0; render(); });
  $("#yearFilter").addEventListener("change", (e) => { state.year = e.target.value; state.page = 0; render(); });
  $("#searchBox").addEventListener("input", (e) => { state.search = e.target.value; state.page = 0; render(); });
  $("#prevPage").addEventListener("click", () => { state.page = Math.max(0, state.page - 1); render(); });
  $("#nextPage").addEventListener("click", () => { state.page += 1; render(); });

  render();
})();
