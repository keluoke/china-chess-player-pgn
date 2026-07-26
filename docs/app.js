import LichessPgnViewer from "./vendor/lichess-pgn-viewer/lichess-pgn-viewer.min.js";
import {
  applyPresentationName,
  buildPresentationNameIndex,
  presentationNameBadge,
  presentationNameDetail,
  resolvePlayerDisplayName
} from "./presentation-names.js";
import {
  defaultDomesticShardKey,
  isLikelyFideID,
  isSingleHanziQuery,
  normalizeSearchText,
  playerQualityScore,
  routingKeysForQuery,
  searchScore,
  searchTokens,
  searchValuesForPlayer
} from "./search-core.js";

const state = {
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
    focusBoard: "",
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
  searchHome: document.querySelector("#searchHome"),
  searchSuggestions: document.querySelector("#searchSuggestions"),
  searchForm: document.querySelector("#searchForm"),
  searchCommand: document.querySelector("#searchCommand"),
  rankingMeta: document.querySelector("#rankingMeta"),
  leaderboards: document.querySelector(".leaderboards"),
  dashboardSection: document.querySelector("#dashboardSection"),
  statGrid: document.querySelector("#statGrid"),
  recentEvents: document.querySelector("#recentEvents"),
  recentEventsMeta: document.querySelector("#recentEventsMeta"),
  changelogList: document.querySelector("#changelogList"),
  changelogMeta: document.querySelector("#changelogMeta"),
  ageOverview: document.querySelector("#ageOverview"),
  creditsList: document.querySelector("#creditsList"),
  creditsHeading: document.querySelector("#creditsHeading")
};

const data = await loadData();
const typedBeforeDataReady = els.searchInput?.value || "";
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
let presentationGroups = null;      // groupID -> group (display-only aggregation)
let presentationMemberIndex = null; // domesticID -> group
const eventDetailCache = new Map();
const eventDetailRequests = new Map();
const domesticDetailCache = new Map();
const domesticShardRequests = new Map();
const participationCache = new Map();
const participationRequests = new Map();
let presentationFideIndex = null;   // FIDE ID -> display-only group
let presentationNameIndex = null;   // FIDE ID -> sanitized display-only name candidate
const PGN_VIEWER_CACHE_MAX_ENTRIES = 3;
const PGN_VIEWER_CACHE_MAX_BYTES = 48 * 1024 * 1024;
let activeLichessViewer = null;
let viewerAutoplayTimer = null;
let searchDebounceTimer = null;
let composingSearch = false;
let domesticSearchReady = false;
let defaultSuggestionCache = null;
const domesticSeenIDs = new Set();
const domesticShardLoaded = new Set();
let domesticFullLoaded = false;
let domesticRouting = null;
let domesticRoutingRequest = null;

initialize();
// Domestic entities load on demand (review §5.3): prefix shards arrive with
// the first matching keystroke or domestic deep link. The full monolith is
// only a compatibility fallback for older shard-less deployments.
if (initialSelectedPlayerID().startsWith("domestic-")) ensureDomesticShard(initialSelectedPlayerID());
loadPresentationGroups();
renderSearchTrustLine();

async function loadData() {
  try {
    const bootstrap = await fetchJSON("./data/search-bootstrap.json", true);
    const optional = path => fetchJSON(path, false).catch(() => null);
    const [manifest, registryManifest, bulkManifest, bulkYouthManifest, byPlayerManifest, domesticManifest, participationManifest] = await Promise.all([
      optional("./data/index/manifest.json"),
      optional("./data/registry/manifest.json"),
      optional("./data/bulk/manifest.json"),
      optional("./data/bulk/youth/manifest.json"),
      optional("./data/index/by-player/manifest.json"),
      optional("./data/registry/domestic/manifest.json"),
      optional("./data/index/player-participation/manifest.json")
    ]);
    return {
      ...bootstrap,
      manifest,
      registryManifest,
      bulkManifest,
      bulkYouthManifest,
      byPlayerManifest,
      domesticManifest,
      participationManifest,
      players: bootstrap.players ?? []
    };
  } catch (error) {
    const target = document.querySelector("#searchCount");
    if (target) target.textContent = `核心搜索数据加载失败：${error.message}`;
    throw error;
  }
}

function mergeDomesticRows(rows) {
  (rows ?? []).forEach(row => {
    const domesticID = row.domesticID || row.id;
    if (!domesticID || domesticSeenIDs.has(domesticID)) return;
    domesticSeenIDs.add(domesticID);
    if (row.fideID) {
      const fidePlayer = players.find(player => String(player.fideID || "") === String(row.fideID));
      if (fidePlayer) {
        // Reviewed domestic facts enrich the existing FIDE card. Registry
        // names and ratings remain authoritative, and no duplicate card is
        // added to the search union.
        fidePlayer.linkedDomesticID = domesticID;
        fidePlayer.linkedDomesticDetailPath = row.detailPath
          || (row.shard ? `data/registry/domestic/shards/${row.shard}.json` : "");
        annotatePresentationGroup(fidePlayer);
        return;
      }
    }
    const player = preparePlayer({
      ...row,
      domesticID,
      playerID: row.id || domesticID,
      entityType: "domestic-player",
      federation: row.federation || "unknown",
      name: row.displayName || row.chineseName || row.pinyin,
      title: row.title || "",
      detailPath: row.detailPath || (row.shard ? `data/registry/domestic/shards/${row.shard}.json` : "")
    });
    annotatePresentationGroup(player);
    players.push(player);
  });
}

// Prefix shards (review §5.3): only the bucket the current query prefix can
// match is downloaded. The monolith stays as deep-link/legacy fallback.
async function loadDomesticRouting() {
  if (domesticRouting) return domesticRouting;
  if (!domesticRoutingRequest) {
    domesticRoutingRequest = fetchJSON("./data/search/domestic-routing.json", false)
      .then(payload => {
        domesticRouting = payload?.routes ?? {};
        return domesticRouting;
      })
      .catch(() => (domesticRouting = {}));
  }
  return domesticRoutingRequest;
}

async function ensureDomesticShard(query) {
  if (domesticFullLoaded) return;
  const routeKeys = routingKeysForQuery(query);
  const routing = routeKeys.length ? await loadDomesticRouting() : {};
  const routed = routeKeys.map(key => routing[key]).filter(Array.isArray).sort((a, b) => a.length - b.length)[0];
  const keys = [...new Set(routed?.length ? routed : [defaultDomesticShardKey(query)].filter(Boolean))];
  if (!keys.length || keys.every(key => domesticShardLoaded.has(key))) return;
  await Promise.all(keys.map(loadDomesticShard));
  domesticSearchReady = true;
  resolveRoutedDomesticPlayer();
  if (state.query) renderSearch();
}

async function loadDomesticShard(key) {
  if (!key || domesticShardLoaded.has(key)) return;
  domesticShardLoaded.add(key);
  try {
    const payload = await fetchJSON(`./data/search/domestic/${key}.json`, false);
    if (payload === null) {
      // Older deploy without shards: fall back to the monolith once.
      domesticShardLoaded.delete(key);
      await loadDomesticSearchIndex();
      return;
    }
    mergeDomesticRows(payload.players);
  } catch (_error) {
    domesticShardLoaded.delete(key);
  }
}

function resolveRoutedDomesticPlayer() {
  const routedID = initialSelectedPlayerID();
  const routedPlayer = routedID ? players.find(player => playerKey(player) === routedID) : null;
  if (!state.selectedFideID && routedPlayer) {
    state.selectedFideID = routedPlayer.presentationCanonicalFideID || routedID;
    state.selectedEventID = null;
    renderDetail();
  }
}

// Full monolith: deep links (?player=domestic-…) and shard-less deploys.
async function loadDomesticSearchIndex() {
  const path = data?.deferred?.domestic || "data/search-bootstrap-domestic.json";
  if (domesticFullLoaded) return;
  domesticFullLoaded = true;
  try {
    const payload = await fetchJSON(`./${path}`, false);
    mergeDomesticRows(payload?.players);
  } catch (_error) {
    domesticFullLoaded = false;
  } finally {
    domesticSearchReady = true;
    resolveRoutedDomesticPlayer();
    if (state.query) renderSearch();
  }
}

// --- presentation identity groups (display-only aggregation, review §4) ----
async function loadPresentationGroups() {
  try {
    const [payload, namesPayload] = await Promise.all([
      fetchJSON("./data/registry/domestic/presentation-groups.json", false),
      fetchJSON("./data/identity/presentation-names.json", false)
    ]);
    presentationGroups = new Map();
    presentationMemberIndex = new Map();
    presentationFideIndex = new Map();
    presentationNameIndex = buildPresentationNameIndex(namesPayload);
    (payload?.groups ?? []).forEach(group => {
      presentationGroups.set(group.groupID, group);
      if (group.canonicalFideID) presentationFideIndex.set(String(group.canonicalFideID), group);
      (group.members ?? []).forEach((member, index) => {
        presentationMemberIndex.set(member, { group, primary: !group.canonicalFideID && index === 0 });
      });
    });
    players.forEach(annotatePresentationGroup);
    const selected = selectedPlayer();
    if (selected?.presentationCanonicalFideID) state.selectedFideID = selected.presentationCanonicalFideID;
    if (state.query || selectedPlayer() || state.selectedEventID) render();
  } catch (_error) {
    presentationGroups = presentationGroups ?? new Map();
    presentationMemberIndex = presentationMemberIndex ?? new Map();
    presentationFideIndex = presentationFideIndex ?? new Map();
    presentationNameIndex = presentationNameIndex ?? new Map();
  }
}

function annotatePresentationGroup(player) {
  if (!player) return;
  if (player.fideID) {
    const group = presentationFideIndex?.get(String(player.fideID));
    const candidate = presentationNameIndex?.get(String(player.fideID))
      || (group?.suggestedChineseName ? {
        fideID: String(player.fideID),
        suggestedChineseName: group.suggestedChineseName,
        confidence: "high",
        displayPolicy: "default",
        provisional: true
      } : null);
    if (group) {
      player.presentationGroupID = group.groupID;
      player.presentationGroupSize = (group.members ?? []).length + 1;
      player.presentationGroupSightings = group.sightingCount;
    }
    applyPresentationName(player, candidate);
    if (group || candidate) Object.assign(player, preparePlayer(player));
    return;
  }
  if (!presentationMemberIndex || player.entityType !== "domestic-player") return;
  const membership = presentationMemberIndex.get(player.domesticID);
  if (!membership) return;
  player.presentationGroupID = membership.group.groupID;
  player.presentationGroupSize = (membership.group.members ?? []).length;
  player.presentationGroupSightings = membership.group.sightingCount;
  player.presentationCanonicalFideID = membership.group.canonicalFideID || "";
  // Default presentation: one card per group; non-primary members stay
  // resolvable via deep link but leave the search list (review §4.3).
  player.hiddenByPresentationGroup = !membership.primary;
}

async function fetchJSON(path, required) {
  const response = await fetch(path, { cache: "default" });
  if (!response.ok) {
    if (!required && response.status === 404) return null;
    throw new Error(`${path} HTTP ${response.status}`);
  }
  const contentType = String(response.headers.get("content-type") || "").toLowerCase();
  if (!contentType.includes("application/json")) {
    if (!required) return null;
    throw new Error(`${path} 返回了非 JSON 内容`);
  }
  return response.json();
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
  state.query = String(new URLSearchParams(location.search).get("q") || typedBeforeDataReady || "");
  els.searchInput.disabled = false;
  els.searchInput.placeholder = "中文名 / 拼音 / FIDE ID / 赛事名";
  els.searchInput.value = state.query;
  els.searchInput.addEventListener("compositionstart", () => { composingSearch = true; });
  els.searchInput.addEventListener("compositionend", event => {
    composingSearch = false;
    scheduleSearch(event.target.value);
  });
  els.searchInput.addEventListener("input", event => {
    if (!composingSearch) scheduleSearch(event.target.value);
  });
  els.searchInput.addEventListener("keydown", handleSearchInputKeydown);
  els.searchForm?.addEventListener("submit", event => {
    event.preventDefault();
    state.query = String(els.searchInput.value || "").trim();
    const url = new URL(location.href);
    if (state.query) url.searchParams.set("q", state.query);
    else url.searchParams.delete("q");
    history.pushState(routeSnapshot(), "", url);
    renderSearch();
  });
  els.searchResults.addEventListener("click", event => {
    const gapLink = event.target.closest("[data-gap-query]");
    if (gapLink) recordLocalGap(gapLink.dataset.gapQuery);
    const anchor = event.target.closest("a[data-player],a[data-event-id]");
    if (!anchor) return;
    rememberSearch(state.query);
    // Progressive enhancement: plain left-clicks stay in-page (pushState via
    // selectPlayer/selectEvent) instead of a full reload; modified clicks
    // (⌘/Ctrl/Shift/middle) keep native new-tab behaviour through the href.
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    if (anchor.dataset.player) selectPlayer(anchor.dataset.player);
    else selectEvent(anchor.dataset.eventId);
  });
  els.searchResults.addEventListener("keydown", event => {
    if (event.key === "Escape") {
      event.preventDefault();
      els.searchInput.focus();
      return;
    }
    if (!['ArrowDown', 'ArrowUp'].includes(event.key)) return;
    const buttons = [...els.searchResults.querySelectorAll("[data-player],[data-event-id]")];
    const index = buttons.indexOf(document.activeElement);
    if (index < 0) return;
    event.preventDefault();
    if (event.key === 'ArrowUp' && index === 0) {
      els.searchInput.focus();
      return;
    }
    buttons[(index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length]?.focus();
  });
  window.history.replaceState(routeSnapshot(), "", window.location.href);
  document.addEventListener("keydown", event => {
    if (event.key === "/" && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName || "")) {
      event.preventDefault();
      els.searchInput.focus();
      return;
    }
    handleViewerKeyboard(event);
  });
  els.detailPane.addEventListener("click", event => {
    const back = event.target.closest('[data-action="back-to-dashboard"]');
    if (back) {
      event.preventDefault();
      goBackOrHome();
    }
    const playerLink = event.target.closest('[data-action="select-player"]');
    if (playerLink) {
      event.preventDefault();
      selectPlayer(playerLink.dataset.fide);
    }
    const share = event.target.closest('[data-action="share-player"]');
    if (share) {
      event.preventDefault();
      shareSelectedPlayer();
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
      goBackOrHome();
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
    const eventPGN = event.target.closest('[data-action="open-event-pgn"]');
    if (eventPGN) {
      event.preventDefault();
      const catalogEvent = findCatalogEvent(state.selectedEventID);
      const detail = eventDetailCache.get(String(catalogEvent?.tournamentID ?? ""));
      const viewerPlayer = eventViewerPlayer(catalogEvent);
      state.viewer = {
        fideID: viewerPlayer.fideID,
        pgnPath: eventPGN.dataset.pgnPath,
        packageId: `event-${catalogEvent?.tournamentID ?? ""}`,
        packageLabel: "赛事全台棋谱",
        packageGameCount: detail?.completeness?.matchedPairings ?? 0,
        focusRound: eventPGN.dataset.round || "",
        focusBoard: eventPGN.dataset.board || "",
        focusApplied: false,
        visible: true,
        status: getCachedPGNViewerPackage(eventPGN.dataset.pgnPath) ? "loaded" : "idle",
        gameIndex: 0,
        orientation: "",
        error: "",
        autoplay: false
      };
      renderEvent();
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
  window.addEventListener("popstate", event => {
    const fideID = initialSelectedPlayerID();
    state.eventFocus = initialEventFocus();
    state.selectedFideID = players.some(player => playerKey(player) === fideID) ? fideID : null;
    state.selectedEventID = state.selectedFideID ? null : initialSelectedEventID();
    state.query = String(event.state?.query || new URLSearchParams(location.search).get("q") || "");
    els.searchInput.value = state.query;
    render();
    requestAnimationFrame(() => window.scrollTo({ top: Number(event.state?.scrollY || 0), behavior: "instant" }));
  });

  render();
  if (!state.query && !state.selectedFideID && !state.selectedEventID
      && window.matchMedia("(pointer: fine)").matches) {
    requestAnimationFrame(() => els.searchInput.focus({ preventScroll: true }));
  }
}

function render() {
  renderSearch();
  renderDetail();
  renderEvent();
  renderSearchSuggestions();
}

function scheduleSearch(value) {
  clearTimeout(searchDebounceTimer);
  searchDebounceTimer = setTimeout(() => {
    state.query = String(value || "").trim();
    const url = new URL(location.href);
    if (state.query) url.searchParams.set("q", state.query);
    else url.searchParams.delete("q");
    history.replaceState(routeSnapshot(), "", url);
    renderSearch();
    renderSearchSuggestions();
  }, 180);
}

// U4: lightweight trust line under the search hero — how much data, how fresh.
async function renderSearchTrustLine() {
  const target = document.querySelector("#searchTrust");
  if (!target) return;
  try {
    const metrics = await fetchJSON("./data/public-metrics.json", false);
    const totals = metrics?.totals ?? {};
    const updated = String(metrics?.generatedAt || "").slice(0, 10);
    const parts = [
      totals.players ? `${Number(totals.players).toLocaleString("zh-CN")} 名注册棋手` : "",
      totals.games ? `${Number(totals.games).toLocaleString("zh-CN")} 盘对局` : "",
      updated ? `更新于 ${updated}` : ""
    ].filter(Boolean);
    if (!parts.length) return;
    target.textContent = parts.join(" · ");
    target.hidden = false;
  } catch (_error) {
    /* trust line is optional */
  }
}

function defaultSearchSuggestions() {
  // Derive examples from the live data (top rated players that actually have
  // games and a Chinese name) instead of a hardcoded list that can go stale.
  if (defaultSuggestionCache) return defaultSuggestionCache;
  const names = players
    .filter(player => player.chineseName && Number(player.gameCount || 0) > 0 && Number.isFinite(player.standard))
    .sort((a, b) => (b.standard ?? 0) - (a.standard ?? 0))
    .slice(0, 2)
    .map(player => player.chineseName);
  defaultSuggestionCache = [...names, "李成智杯"].filter(Boolean);
  if (!names.length) defaultSuggestionCache = ["侯逸凡", "李成智杯"];
  return defaultSuggestionCache;
}

function renderSearchSuggestions() {
  if (!els.searchSuggestions) return;
  const recent = readLocalList("china-chess-recent-searches-v1");
  const suggestions = recent.length ? recent.slice(0, 6) : defaultSearchSuggestions();
  els.searchSuggestions.innerHTML = `<span>${recent.length ? "最近搜索" : "试试"}</span>${suggestions.map(value => `<button type="button" data-search-suggestion="${escapeAttribute(value)}">${escapeHTML(value)}</button>`).join("")}`;
  els.searchSuggestions.querySelectorAll("[data-search-suggestion]").forEach(button => button.addEventListener("click", () => {
    els.searchInput.value = button.dataset.searchSuggestion;
    state.query = button.dataset.searchSuggestion;
    const url = new URL(location.href);
    url.searchParams.set("q", state.query);
    history.replaceState(routeSnapshot(), "", url);
    renderSearch();
    els.searchInput.focus();
  }));
}

function rememberSearch(value) {
  const text = String(value || "").trim();
  if (!text) return;
  const rows = readLocalList("china-chess-recent-searches-v1").filter(item => item !== text);
  rows.unshift(text);
  try { localStorage.setItem("china-chess-recent-searches-v1", JSON.stringify(rows.slice(0, 8))); } catch { /* optional */ }
}

function readLocalList(key) {
  try {
    const value = JSON.parse(localStorage.getItem(key) || "[]");
    return Array.isArray(value) ? value.map(String) : [];
  } catch { return []; }
}

function handleSearchInputKeydown(event) {
  if (event.key === "ArrowDown") {
    const first = els.searchResults?.querySelector("[data-player],[data-event-id]");
    if (first) { event.preventDefault(); first.focus(); }
  } else if (event.key === "Enter") {
    const first = els.searchResults?.querySelector("[data-player],[data-event-id]");
    if (first) { event.preventDefault(); first.click(); }
  } else if (event.key === "Escape") {
    if (!state.query && !els.searchInput.value) return;
    event.preventDefault();
    els.searchInput.value = "";
    scheduleSearch("");
  }
}

function renderSearch() {
  const normalizedQuery = normalize(state.query);
  const singleHanzi = isSingleHanziQuery(state.query);
  const queryReady = normalizedQuery.length >= 2 || singleHanzi || /^\d{2,}$/.test(normalizedQuery);
  if (state.query) ensureDomesticShard(state.query);
  const playerSearch = queryReady ? searchPlayers(state.query) : { items: [], total: 0, truncated: false };
  const matches = playerSearch.items;
  const playerGroups = groupPlayerMatches(matches);
  const eventSearch = normalizedQuery.length >= 2 ? searchEvents(state.query) : { items: [], total: 0, truncated: false };
  const eventMatches = eventSearch.items;
  const hasQuery = state.query.length > 0;
  if (els.searchCommand) {
    els.searchCommand.dataset.mode = hasQuery || Boolean(selectedPlayer()) || Boolean(state.selectedEventID)
      ? "compact"
      : "hero";
  }
  if (queryReady && !eventCatalog) requestEventCatalog();
  els.searchResultsSection.hidden = !hasQuery;
  els.searchInput.setAttribute("aria-expanded", String(hasQuery));
  if (els.searchHome) els.searchHome.hidden = hasQuery || Boolean(selectedPlayer()) || Boolean(state.selectedEventID);
  const ambiguousNames = playerGroups.filter(group => group.length > 1).length;
  els.searchCount.textContent = queryReady
    ? `找到 ${eventSearch.total} 项赛事 · ${playerSearch.total} 位棋手${ambiguousNames ? `（${ambiguousNames} 个姓名待区分）` : ""}${domesticSearchReady ? "" : " · 无 FIDE 档案加载中…"}`
    : "请输入姓名、拼音、FIDE ID 或赛事名";
  const eventResults = eventMatches.length ? `
    <section class="search-result-group">
      <div class="search-result-group-title"><h3>赛事</h3><span>${eventSearch.truncated ? `显示 ${eventMatches.length} / ${eventSearch.total} 项` : `${eventSearch.total} 项`}</span></div>
      <div class="event-search-list">${eventMatches.map(event => `
        <a class="result-button event-search-result" href="?event=${encodeURIComponent(event.id)}" data-event-id="${escapeAttribute(event.id)}">
          <div class="player-name">${highlightMatch(event.displayName ?? event.chineseName ?? event.name ?? "未命名赛事", state.query)}</div>
          <div class="player-meta">${escapeHTML([event.date || "日期待补", event.groupLabel || "", event.rounds ? `${event.rounds} 轮` : "", event.participants ? `${event.participants} 人` : "", event.seriesLabel || ""].filter(Boolean).join(" · "))}</div>
        </a>`).join("")}</div>
      ${eventSearch.truncated ? `<p class="search-limit-note">赛事结果仅显示前 ${eventMatches.length} 条（共 ${eventSearch.total} 条），请补充年份、站点或组别缩小范围。</p>` : ""}
    </section>` : "";
  const playerResults = playerGroups.length ? `
    <section class="search-result-group">
      <div class="search-result-group-title"><h3>棋手</h3><span>显示 ${matches.length} / ${playerSearch.total}</span></div>
      <div class="player-search-list">${playerGroups.map(group => group.length > 1 ? disambiguationCard(group) : (() => {
    const player = group[0];
    const rating = ratingForPlayer(player);
    const fideLabel = player.fideID ? `FIDE ${player.fideID}` : "[无FIDE]";
    const ratingLabel = rating ? `${rating.value} ${rating.kind}` : "无等级分";
    const birthLabel = publicAgeLabel(player);
    const titleLabel = player.title || "无称号";
    return `
      <a class="result-button" href="${escapeAttribute(playerHref(player))}" data-player="${escapeAttribute(playerKey(player))}" aria-pressed="${state.selectedFideID === playerKey(player)}">
        <div class="player-name">${highlightMatch(displayName(player), state.query)} ${publicStatusBadge(player)} ${presentationNameBadgeHTML(player)}</div>
        <div class="player-meta">${escapeHTML(fideLabel)} · ${escapeHTML(ratingLabel)} · ${escapeHTML(birthLabel)} · ${escapeHTML(titleLabel)}</div>
      </a>
    `;
  })()).join("")}</div>${playerSearch.truncated ? `<p class="search-limit-note">仅显示前 30 条，请补充拼音、出生年份或 FIDE ID 缩小范围。</p>` : ""}</section>` : "";
  const eventFirst = !isLikelyFideID(normalizedQuery) && (/^tnr\d+$/i.test(normalizedQuery) || /(杯|赛|锦标|公开|master|open)/i.test(state.query));
  if (!queryReady) {
    els.searchResults.innerHTML = `<div class="empty-state compact"><strong>开始搜索</strong><span>可输入中文名、拼音、FIDE ID 或赛事名称；单个汉字会显示前 30 条，并建议继续补全姓名。</span></div>`;
  } else if (eventResults || playerResults) {
    els.searchResults.innerHTML = eventFirst ? `${eventResults}${playerResults}` : `${playerResults}${eventResults}`;
  } else {
    els.searchResults.innerHTML = `<div class="empty-state compact gap-empty"><strong>本地库暂未匹配</strong><span>试试完整中文名、不带空格拼音或 7–8 位 FIDE ID。</span><a class="primary-button" href="./leaderboards.html">浏览棋手排行榜</a><small>搜索词仅保存在当前浏览器，不会静默上传。</small></div>`;
  }

}

function highlightMatch(value, query) {
  const text = String(value ?? "");
  const needle = String(query ?? "").trim();
  if (!needle) return escapeHTML(text);
  const index = text.toLocaleLowerCase().indexOf(needle.toLocaleLowerCase());
  if (index < 0) return escapeHTML(text);
  return `${escapeHTML(text.slice(0, index))}<mark>${escapeHTML(text.slice(index, index + needle.length))}</mark>${escapeHTML(text.slice(index + needle.length))}`;
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
    <div class="disambiguation-head"><div><strong>${highlightMatch(displayText(label), state.query)}</strong><span class="identity-status same-name">同名待区分</span></div><small>库内有 ${group.length} 条同名档案</small></div>
    <div class="disambiguation-options">${group.map(player => {
      const rating = ratingForPlayer(player);
      const context = player.fideID
        ? [`FIDE ${player.fideID}`, rating ? `${rating.value} ${rating.kind}` : "无等级分", publicAgeLabel(player), player.title].filter(Boolean)
        : ["无 FIDE", player.publicLocation, ...(player.eventYears ?? []), ...(player.eventNames ?? []).slice(0, 1)].filter(Boolean);
      return `<a href="${escapeAttribute(playerHref(player))}" data-player="${escapeAttribute(playerKey(player))}"><strong>${escapeHTML(displayName(player))}</strong><span>${escapeHTML(context.join(" · ") || "打开赛事档案核对")}</span></a>`;
    }).join("")}</div>
    <p>这些档案尚未确认属于同一人；请按参赛年份、地区和赛事逐条核对。</p>
  </article>`;
}

function playerHref(player) {
  const fideID = player.fideID || player.presentationCanonicalFideID;
  return fideID ? `?fideID=${encodeURIComponent(fideID)}` : `?player=${encodeURIComponent(player.domesticID || player.id)}`;
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
  requestPlayerParticipation(player);
  const identityGroup = player.presentationGroupID && presentationGroups
    ? presentationGroups.get(player.presentationGroupID) : null;
  if (identityGroup?.canonicalFideID) requestPresentationGroupDetails(identityGroup);

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
        <span class="eyebrow">${publicStatusBadge(player)} ${presentationNameBadgeHTML(player, { detail: true })} · 赛前情报</span>
        <h1>${escapeHTML(displayName(player))}</h1>
        ${detailChineseNameLine(player)}
        <p>FIDE ${escapeHTML(player.fideID)} · ${escapeHTML(uniqueStrings([publicAgeLabel(player), stageLabelForPlayer(player, stage)]).join(" · "))}</p>
      </div>
      <div class="detail-title-actions">
        ${player.sex === "F" ? `<span class="stage-chip">女</span>` : ""}
        <a class="action-link" href="#" data-action="back-to-dashboard">← 返回搜索</a>
        <button class="action-link" type="button" data-action="share-player">分享档案</button>
        <a class="action-link" href="https://ratings.fide.com/profile/${encodeURIComponent(player.fideID)}" target="_blank" rel="noreferrer">FIDE 主页</a>
        <a class="action-link" href="./contribute.html?type=privacy-request&player=${encodeURIComponent(player.fideID)}&name=${encodeURIComponent(displayName(player))}">删除 / 匿名化请求</a>
      </div>
    </div>

    ${note ? `<span class="note-pill">${escapeHTML(note)}</span>` : ""}

    ${identityGroup ? `<div class="note-pill">高置信身份展示聚合 · 含 ${identityGroup.members.length} 个尚待维护者落表的国内赛事身份</div>` : ""}
    ${playerEventHistory(detailCache.get(player.fideID) ?? player)}
    ${staticInfo?.gameCount ? staticPlayerHitBlock(player, staticInfo) : ""}
    ${!staticInfo?.gameCount && bulkInfo?.totalGames ? bulkPlayerHitBlock(bulkInfo) : ""}
    ${state.downloadStatus ? `<div class="download-status" aria-live="polite">${escapeHTML(state.downloadStatus)}</div>` : ""}
    ${pgnViewerBlock(player, staticInfo)}
    <div class="rating-grid">
      ${ratingCard("标准棋", player.standard)}
      ${ratingCard("快棋", player.rapid)}
      ${ratingCard("超快棋", player.blitz)}
    </div>
    ${playerCoverageStatus(player, staticInfo, bulkInfo)}
    ${sameNameRelatedBlock(player)}
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
  // Display-only aggregation (review §4.3): members of a high-confidence
  // presentation group open one merged profile. Facts keep their original
  // member IDs; the group never rewrites Person or observation data.
  const group = player.presentationGroupID && presentationGroups
    ? presentationGroups.get(player.presentationGroupID) : null;
  let groupSightings = null;
  if (group) {
    const refs = group.memberRefs ?? [];
    const missing = refs.filter(ref => !domesticDetailCache.has(ref.id) && ref.id !== player.domesticID);
    if (missing.length) {
      missing.forEach(ref => requestDomesticShardPath(`data/registry/domestic/shards/${ref.shard}.json`));
      els.detailPane.innerHTML = `<div class="event-loading">正在聚合该棋手的全部赛事证据…</div>`;
      return;
    }
    const seen = new Set();
    groupSightings = [];
    refs.forEach(ref => {
      const memberDetail = ref.id === player.domesticID ? player : domesticDetailCache.get(ref.id);
      (memberDetail?.sightings ?? []).forEach(sighting => {
        const key = sighting.sightingID ?? JSON.stringify([sighting.eventID, sighting.playerNo, sighting.eventName]);
        if (seen.has(key)) return;
        seen.add(key);
        groupSightings.push(sighting);
      });
    });
    groupSightings.sort((a, b) => String(b.eventDate ?? "").localeCompare(String(a.eventDate ?? "")));
  }
  const sightings = groupSightings ?? player.sightings ?? [];
  const publicLocation = player.publicLocation || publicLocationFromSightings(sightings);
  const stages = uniqueStrings(sightings.map(publicStageFromSighting).filter(Boolean));
  els.detailPane.innerHTML = `
    <div class="detail-title">
      <div>
        <span class="eyebrow">${publicStatusBadge(player)} · 国内赛事参赛档案${group ? ` · <span class="identity-status pending">身份暂定 · 已聚合 ${group.members.length} 条记录</span>` : ""}</span>
        <h1>${escapeHTML(displayName(player))}</h1>
        <p>[无FIDE] · ${escapeHTML(stages[0] || "年龄组待补")} · 公开赛事记录</p>
      </div>
      <div class="detail-title-actions">
        <span class="stage-chip domestic-chip">无 FIDE</span>
        <a class="action-link" href="#" data-action="back-to-dashboard">← 返回搜索</a>
        <button class="action-link" type="button" data-action="share-player">分享档案</button>
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
          </div>
        </article>`).join("")}</div>` : `<div class="empty-state compact">暂无赛事证据。</div>`}
    </section>
    ${sameNameRelatedBlock(player)}
  `;
}

function requestDomesticShardPath(path) {
  if (!path || domesticShardRequests.has(path)) return;
  const request = fetchJSON(`./${path}`, true)
    .then(rows => {
      (rows ?? []).forEach(row => domesticDetailCache.set(row.domesticID, row));
      if (selectedPlayer()?.presentationGroupID) renderDetail();
    })
    .catch(() => {})
    .finally(() => { domesticShardRequests.delete(path); });
  domesticShardRequests.set(path, request);
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
  const event = findCatalogEvent(eventID);
  if (!event) {
    els.eventPane.innerHTML = `
      <div class="event-empty">
        <h2>未找到赛事</h2>
        <p>该链接对应的赛事已不存在，或本地赛事目录仍在更新。</p>
        <a class="action-link" href="#" data-action="back-to-dashboard">← 返回搜索</a>
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
  const coverageLabel = completenessLabel(event, eventDetail);
  const facts = [
    ["日期", event.date],
    ["系列", event.seriesLabel],
    ["组别", event.groupLabel],
    ["轮次", event.rounds],
    ["报名人数", event.participants],
    ["中国棋手", event.playerCount ? `${event.playerCount} 名` : null],
    ["覆盖口径", coverageLabel],
    ["已归档 PGN", event.gameCount ? `${compactNumber(event.gameCount)} 盘` : null],
    ["有棋谱棋手", event.pgnPlayerCount ? `${event.pgnPlayerCount} 名` : null]
  ].filter(([, value]) => value !== null && value !== undefined && value !== "");
  // Long roster / per-round sections fold by default on narrow (mobile)
  // screens; the overview stays always visible.
  const foldOpen = (typeof window !== "undefined" && window.innerWidth > 720) ? " open" : "";

  els.eventPane.innerHTML = `
    <div class="detail-title event-title">
      <div>
        <span class="eyebrow">${dataStatusBadge(eventDataStatus(event))} · 赛事档案</span>
        <h1>${escapeHTML(event.displayName ?? event.name ?? "未命名赛事")}</h1>
        ${event.chineseName && event.name !== event.chineseName ? `<p class="event-source-name">别名：${escapeHTML(event.name)}</p>` : ""}
      </div>
      <div class="detail-title-actions">
        <a class="action-link" href="#" data-action="back-to-dashboard">← 返回搜索</a>
      </div>
    </div>
    <div class="event-facts">
      ${facts.map(([label, value]) => `<div><span>${escapeHTML(label)}</span><strong>${escapeHTML(String(value))}</strong></div>`).join("")}
    </div>
    <details class="event-roster event-fold"${foldOpen}>
      <summary class="section-heading"><h3>${eventDetail ? "已收录 FIDE 棋手" : "参赛中国棋手"}</h3><span>${eventPlayers.length ? `${eventPlayers.length} 名可跳转` : "名单待同步"}</span></summary>
      ${visiblePlayers.length ? `<div class="event-player-grid">${visiblePlayers.map(player => `
        <button class="event-player" type="button" data-action="select-event-player" data-fide="${escapeAttribute(player.fideID)}" data-event-focus="${escapeAttribute(event.id)}" data-tournament-id="${escapeAttribute(event.tournamentID ?? "")}">
          <strong>${escapeHTML(displayName(player))}</strong>${presentationNameBadgeHTML(player)}<span>FIDE ${escapeHTML(player.fideID)}</span>
        </button>`).join("")}</div>${extraPlayers ? `<p class="event-more">另有 ${extraPlayers} 名已收录棋手。</p>` : ""}` : `<div class="empty-state compact">该赛事已有赛事记录，但棋手名单尚未同步。</div>`}
    </details>
    ${eventDetail ? domesticEventData(event, eventDetail) : event.detailPath ? `<div class="event-loading">正在载入逐轮成绩与最终排名…</div>` : ""}
    <p class="event-provenance">${event.canonicalEventID ? `Canonical ID：${escapeHTML(event.canonicalEventID)} · ` : ""}赛事 ID：${escapeHTML(event.tournamentID ?? event.id)}${event.evidenceURL ? " · 中文名已由社区核验" : ""}</p>
  `;
  const viewerPlayer = eventViewerPlayer(event);
  if (state.viewer.visible && state.viewer.fideID === viewerPlayer.fideID && state.viewer.pgnPath) {
    requestPGNViewer(viewerPlayer, state.viewer);
    wirePGNViewerActions(viewerPlayer);
    mountLichessViewer(viewerPlayer);
  }
}

function eventViewerPlayer(event) {
  return {
    fideID: `event-${event?.tournamentID ?? "unknown"}`,
    displayName: event?.displayName ?? event?.name ?? "赛事",
    name: event?.displayName ?? event?.name ?? "赛事"
  };
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
  const foldOpen = (typeof window !== "undefined" && window.innerWidth > 720) ? " open" : "";
  const pendingRounds = Boolean(detail.roundsPendingVerification || event.roundsPendingVerification);
  const roundsSection = pendingRounds && !rounds.length
    ? `<section class="event-results-section"><div class="section-heading"><h3>逐轮对阵结果</h3><span>待核验</span></div>
       <div class="empty-state compact"><strong>逐轮待核验</strong><span>源表结构异常（如赛果列错位），逐轮成绩暂缓发布；最终排名不受影响。</span></div></section>`
    : `<details class="event-results-section event-fold"${foldOpen}>
      <summary class="section-heading"><h3>逐轮对阵结果</h3><span>${rounds.length} 轮</span></summary>
      <div class="event-round-tabs" role="tablist" aria-label="赛事轮次">
        ${rounds.map(item => `<button type="button" data-event-round="${escapeAttribute(item.round)}" aria-selected="${Number(item.round) === Number(selectedRound?.round)}">第 ${escapeHTML(item.round)} 轮</button>`).join("")}
      </div>
      ${selectedRound ? `<div class="pairing-list">${(selectedRound.pairings ?? []).map(pairing => pairingRow(event, selectedRound.round, pairing)).join("") || `<div class="empty-state compact">该轮暂无对阵数据。</div>`}</div>` : `<div class="empty-state compact">暂无逐轮数据。</div>`}
    </details>`;
  return `
    ${pgnViewerBlock(eventViewerPlayer(event), { packages: [] })}
    ${roundsSection}
    <details class="event-results-section event-fold"${foldOpen}>
      <summary class="section-heading"><h3>最终成绩排行</h3><span>${standings.length} 名</span></summary>
      <div class="standings-table-wrap"><table class="standings-table"><thead><tr><th>名次</th><th>棋手</th><th>FIDE ID</th><th>等级分</th><th>得分</th><th>单位</th></tr></thead><tbody>
        ${standings.map(row => `<tr><td>${escapeHTML(row.rank ?? "-")}</td><td>${eventSideControl(event, row, "")}</td><td>${escapeHTML(row.fideID || "无FIDE")}</td><td>${escapeHTML(row.rating || "-")}</td><td><strong>${escapeHTML(row.score || "-")}</strong></td><td>${escapeHTML(row.fideID ? (row.club || "-") : (publicLocationFromSighting(row) || "未公开"))}</td></tr>`).join("")}
      </tbody></table></div>
    </details>`;
}

function pairingRow(event, round, pairing) {
  const localGame = pairing.localGame;
  const focusFideID = localGame?.playerFideIDs?.[0] || pairing.white?.fideID || pairing.black?.fideID || "";
  // Missing-PGN states carry a reason instead of a bare "无 PGN":
  // hasPGN=false → the source never published a game for this board;
  // hasPGN=true but neither local nor external copy → not yet collected.
  const missingReason = pairing.hasPGN === false
    ? "来源未公开棋谱"
    : pairing.hasPGN
    ? "待抓取"
    : "无棋谱信息";
  const pgnAction = localGame?.pgnPath
    ? `<button type="button" class="pairing-pgn available" data-action="open-event-pgn" data-pgn-path="${escapeAttribute(localGame.pgnPath)}" data-round="${escapeAttribute(round)}" data-board="${escapeAttribute(pairing.board || localGame.board || "")}">● 本库 PGN</button>`
    : localGame && focusFideID
    ? `<button type="button" class="pairing-pgn available" data-action="select-event-player" data-fide="${escapeAttribute(focusFideID)}" data-event-focus="${escapeAttribute(event.id)}" data-tournament-id="${escapeAttribute(event.tournamentID ?? "")}" data-round="${escapeAttribute(round)}">● 本库 PGN</button>`
    : pairing.pgnURL
    ? `<a class="pairing-pgn external" href="${escapeAttribute(pairing.pgnURL)}" target="_blank" rel="noreferrer">PGN ↗</a>`
    : `<span class="pairing-pgn missing" title="${escapeAttribute(missingReason)}">${escapeHTML(missingReason)}</span>`;
  return `<article class="pairing-row"><span class="pairing-board">${escapeHTML(pairing.board || "-")}</span><div>${eventSideControl(event, pairing.white ?? {}, round)}</div><strong class="pairing-result">${escapeHTML(pairing.result || "*")}</strong><div>${eventSideControl(event, pairing.black ?? {}, round)}</div>${pgnAction}</article>`;
}

function eventSideControl(event, side, round) {
  const label = side.chineseName && side.name && side.chineseName !== side.name ? `${side.chineseName} · ${side.name}` : side.chineseName || side.name || "轮空";
  if (!side.fideID) return `<span class="event-side-name">${escapeHTML(label)}<small>[无FIDE]</small></span>`;
  return `<button type="button" class="event-side-name link" data-action="select-event-player" data-fide="${escapeAttribute(side.fideID)}" data-event-focus="${escapeAttribute(event.id)}" data-tournament-id="${escapeAttribute(event.tournamentID ?? "")}" data-round="${escapeAttribute(round)}">${escapeHTML(label)}<small>FIDE ${escapeHTML(side.fideID)}</small></button>`;
}

function requestEventCatalog() {
  // Curated public catalog (四类目标赛事) — the only catalog product surfaces
  // read. The full events.json stays an internal evidence/audit artifact and
  // is fetched lazily only for deep links / per-sighting PGN checks.
  if (eventCatalogRequest) return eventCatalogRequest;
  eventCatalogRequest = fetchJSON("./data/index/public-events.json", true)
    .then(payload => {
      eventCatalog = Array.isArray(payload?.events) ? payload.events : [];
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

function findCatalogEvent(eventID) {
  return (eventCatalog ?? []).find(item => item.id === eventID);
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
      annotatePresentationGroup(prepared);
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

function requestPresentationGroupDetails(group) {
  (group?.memberRefs ?? []).forEach(ref => {
    if (!domesticDetailCache.has(ref.id)) {
      requestDomesticShardPath(`data/registry/domestic/shards/${ref.shard}.json`);
    }
  });
}

function participationBucket(fideID) {
  const numeric = Number.parseInt(String(fideID), 10);
  return Number.isFinite(numeric) ? (numeric % 256).toString(16).padStart(2, "0") : "";
}

function requestPlayerParticipation(player) {
  const fideID = String(player?.fideID ?? "");
  const bucket = participationBucket(fideID);
  if (!fideID || !bucket || !data.participationManifest || participationCache.has(fideID) || participationRequests.has(bucket)) return;
  const request = fetchJSON(`./data/index/player-participation/buckets/${bucket}.json`, false)
    .then(payload => {
      Object.entries(payload?.players ?? {}).forEach(([id, events]) => participationCache.set(String(id), events ?? []));
      if (!participationCache.has(fideID)) participationCache.set(fideID, []);
      if (state.selectedFideID === fideID) renderDetail();
    })
    .catch(() => { participationCache.set(fideID, []); })
    .finally(() => { participationRequests.delete(bucket); });
  participationRequests.set(bucket, request);
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
      <strong>已收录 ${compactNumber(info.totalGames)} 盘青少年赛事对局</strong>
      <span>${escapeHTML(info.stages.map(stage => `${friendlyStageLabel(stage.id)} ${stage.count} 盘`).join(" · "))}</span>
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
        <strong>已收录这位棋手的 ${compactNumber(info.gameCount)} 盘对局</strong>
        <span>${escapeHTML(stageLine || (info.sources ?? []).join(" · ") || "按赛事归档")}</span>
      </div>
      <div class="pgn-package-grid">${packageButtons}</div>
    </div>
  `;
}

function playerEventHistory(player) {
  const rows = mergedPlayerEvents(player);
  if (!rows.length) return "";
  const eventRow = event => {
    const eventID = event.id || "";
    const name = event.chineseName || event.displayName || event.name || "未命名赛事";
    const status = event.gameCount
      ? `${compactNumber(event.gameCount)} 盘棋谱`
      : event.resultStatus === "scheduled" ? "报名/名单记录 · 尚未完赛"
      : event.resultStatus === "recorded" ? "赛果已收录 · 暂无棋谱" : "查看赛事";
    return `<button type="button" class="player-event-row" ${eventID ? `data-action="select-event" data-event-id="${escapeAttribute(eventID)}"` : "disabled"}>
      <span><strong>${escapeHTML(name)}</strong><small>${escapeHTML([event.date || "日期待补", event.rounds ? `${event.rounds} 轮` : "", event.participants ? `${event.participants} 人` : ""].filter(Boolean).join(" · "))}</small></span>
      <em>${event.rank && event.rank !== "-" ? `<b>第 ${escapeHTML(String(event.rank))} 名</b>` : ""}${escapeHTML(status)}</em>
    </button>`;
  };
  const first = rows.slice(0, 12).map(eventRow).join("");
  const rest = rows.slice(12).map(eventRow).join("");
  return `
    <section class="player-event-history">
      <div class="section-heading"><h3>赛事记录</h3><span>${rows.length} 项</span></div>
      <div class="player-event-list">${first}</div>
      ${rest ? `<details class="event-history-more"><summary>查看其余 ${rows.length - 12} 项赛事</summary><div class="player-event-list">${rest}</div></details>` : ""}
    </section>`;
}

function mergedPlayerEvents(player) {
  const fideID = String(player?.fideID ?? "");
  const rows = new Map();
  const merge = event => {
    const key = String(event?.tournamentID || event?.id || `${event?.name || ""}|${event?.date || ""}`);
    if (!key) return;
    const current = rows.get(key) ?? {};
    rows.set(key, { ...current, ...event, gameCount: Math.max(Number(current.gameCount || 0), Number(event.gameCount || 0)) });
  };
  (participationCache.get(fideID) ?? []).forEach(merge);

  const group = player?.presentationGroupID && presentationGroups
    ? presentationGroups.get(player.presentationGroupID) : null;
  if (group?.canonicalFideID) {
    (group.memberRefs ?? []).forEach(ref => {
      (domesticDetailCache.get(ref.id)?.sightings ?? []).forEach(sighting => merge({
        id: sightingEventID(sighting),
        tournamentID: String(sighting.eventID ?? "").match(/(?:tnr)?(\d+)/i)?.[1] || "",
        name: sighting.eventName || sighting.group || "未命名赛事",
        date: sighting.eventDate || "",
        rank: sighting.rank || "",
        rounds: sighting.rounds || "",
        resultStatus: "recorded",
        pgnStatus: sightingHasPGN(sighting) ? "available" : "not-archived",
        gameCount: 0
      }));
    });
  }
  (player?.events ?? []).forEach(event => merge({ ...event, pgnStatus: Number(event.gameCount || 0) ? "available" : event.pgnStatus }));
  (staticPlayerInfo(player)?.events ?? []).forEach(event => merge({ ...event, pgnStatus: Number(event.gameCount || 0) ? "available" : event.pgnStatus }));
  return [...rows.values()].sort((a, b) => String(b.date || "").localeCompare(String(a.date || "")));
}

function friendlyStageLabel(stage) {
  const match = String(stage || "").match(/^U(\d+)$/i);
  return match ? `${match[1]} 岁组` : String(stage || "");
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
      state.viewer.gameIndex = focusedGameIndex(cached.games, state.viewer.focusRound, state.viewer.focusBoard);
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
      if (state.viewer.fideID === fideID && state.viewer.pgnPath === pgnPath) {
        state.viewer.status = "loaded";
        state.viewer.gameIndex = state.viewer.focusRound ? focusedGameIndex(games, state.viewer.focusRound, state.viewer.focusBoard) : 0;
        state.viewer.focusApplied = true;
        state.viewer.orientation = preferredBoardOrientation(player, games[state.viewer.gameIndex]);
        renderViewerTarget(fideID);
      }
    })
    .catch(error => {
      if (state.viewer.fideID === fideID && state.viewer.pgnPath === pgnPath) {
        state.viewer.status = "error";
        state.viewer.error = error.message;
        renderViewerTarget(fideID);
      }
    })
    .finally(() => {
      pgnViewerRequests.delete(pgnPath);
    });

  pgnViewerRequests.set(pgnPath, request);
}

function focusedGameIndex(games, round, board = "") {
  const wanted = String(round ?? "").split(".")[0];
  const wantedBoard = String(board ?? "").trim();
  const index = games.findIndex(game => {
    const roundMatches = String(game.headers?.Round ?? "").split(".")[0] === wanted;
    const boardMatches = !wantedBoard || String(game.headers?.Board ?? "").trim() === wantedBoard;
    return roundMatches && boardMatches;
  });
  return index >= 0 ? index : 0;
}

function renderViewerTarget(fideID) {
  if (String(fideID).startsWith("event-")) renderEvent();
  else renderDetail();
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
      <p class="viewer-keyboard-hint">键盘：← → 翻看着法，空格开始或暂停自动播放。手机端可使用棋盘下方控制按钮。</p>
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
    renderViewerTarget(player.fideID);
  });

  document.querySelector("#viewerFlip")?.addEventListener("click", () => {
    stopViewerAutoplay();
    state.viewer.orientation = state.viewer.orientation === "black" ? "white" : "black";
    renderViewerTarget(player.fideID);
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
  history.replaceState(routeSnapshot(), "", location.href);
  rememberSearch(state.query);
  state.query = "";
  if (els.searchInput) els.searchInput.value = "";
  if (state.selectedFideID !== playerID) resetPGNViewer(playerID);
  state.selectedFideID = playerID;
  state.selectedEventID = null;
  state.eventFocus = eventFocus;
  state.downloadStatus = "";
  const player = players.find(item => playerKey(item) === playerID);
  updateRoute(player?.fideID ? { fideID: player.fideID, eventFocus } : { playerID }, "push");
  renderSearch();
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

function updateRoute({ fideID = null, playerID = null, eventID = null, eventFocus = null }, mode = "replace") {
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
  url.searchParams.delete("q");
  const snapshot = { ...routeSnapshot(), depth: Number(history.state?.depth || 0) + (mode === "push" ? 1 : 0) };
  window.history[mode === "push" ? "pushState" : "replaceState"](snapshot, "", url);
}

function selectEvent(eventID) {
  if (!eventID) return;
  history.replaceState(routeSnapshot(), "", location.href);
  rememberSearch(state.query);
  state.query = "";
  if (els.searchInput) els.searchInput.value = "";
  resetPGNViewer(null);
  state.selectedFideID = null;
  state.selectedEventID = eventID;
  state.selectedEventRound = null;
  state.eventFocus = null;
  state.downloadStatus = "";
  updateRoute({ eventID }, "push");
  renderSearch();
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
  renderSearch();
  renderSearchSuggestions();
  renderDetail();
  renderEvent();
}

function goBackOrHome() {
  if (Number(history.state?.depth || 0) > 0) {
    history.back();
    return;
  }
  clearSelection();
}

async function shareSelectedPlayer() {
  const player = selectedPlayer();
  if (!player) return;
  const url = new URL(location.href);
  url.search = "";
  if (player.fideID) url.searchParams.set("fideID", player.fideID);
  else url.searchParams.set("player", player.domesticID || player.id);
  const rating = ratingForPlayer(player);
  const games = Number(player.playerPgnGameCount || player.gameCount || 0);
  const text = `${displayName(player)}的国际象棋档案${games ? `：已收录 ${games} 盘对局` : ""}${rating ? `，最新${rating.kind}等级分 ${rating.value}` : ""}`;
  try {
    if (navigator.share) await navigator.share({ title: `${displayName(player)} · 棋手档案`, text, url: url.href });
    else await navigator.clipboard.writeText(`${text}\n${url.href}`);
    state.downloadStatus = navigator.share ? "分享面板已打开。" : "档案链接已复制。";
  } catch (error) {
    if (error?.name !== "AbortError") state.downloadStatus = "暂时无法分享，请稍后重试。";
  }
  renderDetail();
}

function routeSnapshot() {
  return {
    app: "china-chess-player-db",
    depth: Number(history.state?.depth || 0),
    query: state.query,
    scrollY: window.scrollY
  };
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
  if (!normalized) return { items: [], total: 0, truncated: false };
  const ranked = players
    .filter(player => !player.hiddenByPresentationGroup)
    .map(player => ({ player, score: searchScore(player, normalized, tokens, reversed) }))
    .filter(entry => entry.score > 0)
    .sort((a, b) => {
      if (a.score !== b.score) return b.score - a.score;
      const quality = playerQualityScore(b.player) - playerQualityScore(a.player);
      if (quality) return quality;
      return (ratingForPlayer(b.player)?.value ?? 0) - (ratingForPlayer(a.player)?.value ?? 0);
    });
  return {
    items: ranked.slice(0, 30).map(entry => entry.player),
    total: ranked.length,
    truncated: ranked.length > 30
  };
}

function searchEvents(query) {
  const normalized = normalize(query);
  if (!normalized || !eventCatalog) return { items: [], total: 0, truncated: false };
  const scored = eventCatalog
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
    .sort((a, b) => b.score - a.score || String(b.event.date ?? "").localeCompare(String(a.event.date ?? "")));
  const items = scored.slice(0, 12).map(entry => entry.event);
  return { items, total: scored.length, truncated: scored.length > items.length };
}

function sameNameCount(player) {
  const key = normalizedIdentityName(player);
  if (!key) return 0;
  const groupID = player.presentationGroupID;
  return players.filter(candidate => {
    if (normalizedIdentityName(candidate) !== key) return false;
    if (groupID && candidate.presentationGroupID === groupID) return false;
    return true;
  }).length;
}

function sameNameRelatedPlayers(player) {
  const key = normalizedIdentityName(player);
  const currentKey = playerKey(player);
  if (!key) return [];
  const groupID = player.presentationGroupID;
  return players
    .filter(candidate => {
      if (playerKey(candidate) === currentKey) return false;
      if (normalizedIdentityName(candidate) !== key) return false;
      if (groupID && candidate.presentationGroupID === groupID) return false;
      return true;
    })
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
  const event = (eventCatalog ?? []).find(item => item.id === eventID);
  // Factual only: archived games exist. A detail page alone is results-only.
  return Boolean(event && (Number(event.gameCount) > 0 || Number(event.pgnCount) > 0));
}

function publicStatus(player) {
  if (player?.fideID || player?.publicIdentityStatus === "verified") return { key: "verified", label: "已核验" };
  if (player?.presentationGroupID) return { key: "presentation-high", label: "已归组" };
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

function presentationNameBadgeHTML(player, { detail = false } = {}) {
  const badge = presentationNameBadge(player, { detail });
  if (!badge) return "";
  return `<span class="identity-status ${badge.key}" title="${escapeAttribute(badge.title)}">${escapeHTML(badge.label)}</span>`;
}

function eventDataStatus(event) {
  // Copy derives from explicit completeness states only (review §5.1).
  if (event?.playableComplete) return "complete";
  if (event?.eventComplete) return "archive-complete";
  if (["fetch-failed", "empty-response"].includes(event?.pgnSourceStatus)) return "pgn-failed";
  const availability = event?.pgnAvailability;
  if (availability === "not-published") return "results-only";
  if (availability === "advertised-partial") return "partial-live";
  if (availability === "advertised-full") return "pgn-pending";
  if (Number(event?.gameCount) > 0 || Number(event?.pgnCount) > 0) return "cached";
  if (event?.detailPath) return "results-only";
  return "unverified";
}

function dataStatusBadge(status) {
  const labels = {
    complete: "全台棋谱",
    "archive-complete": "本地归档完整",
    "partial-live": "部分直播台棋谱",
    "results-only": "赛果完整 · 无公开棋谱",
    "pgn-pending": "棋谱待匹配",
    "pgn-failed": "公开棋谱抓取失败",
    cached: "已归档棋谱",
    unverified: "覆盖待核验",
  };
  return `<span class="data-status ${escapeAttribute(status)}">${escapeHTML(labels[status] || labels.unverified)}</span>`;
}

function completenessLabel(event, eventDetail) {
  const completeness = eventDetail?.completeness ?? {};
  const availability = completeness.pgnAvailability ?? event?.pgnAvailability;
  const sourceStatus = completeness.pgnSourceStatus ?? event?.pgnSourceStatus;
  const resultsOK = completeness.resultsStatus === "results-complete" || Boolean(event?.detailPath);
  if (completeness.playableComplete || event?.playableComplete) return "赛果完整 · 全台棋谱";
  if (completeness.eventComplete || event?.eventComplete) return "赛果完整 · 本地归档完整";
  if (["fetch-failed", "empty-response"].includes(sourceStatus)) return "赛果完整 · 公开棋谱抓取失败，待补";
  if (resultsOK && availability === "not-published") return "赛果完整 · 来源未公开棋谱";
  if (resultsOK && availability === "advertised-partial") return "赛果完整 · 部分直播台棋谱";
  if (resultsOK && availability === "advertised-full") return "赛果完整 · 棋谱待匹配";
  if (resultsOK) return "赛果完整";
  return "仅展示已收录中国棋手";
}

function playerCoverageStatus(player, staticInfo, bulkInfo) {
  const games = Number(staticInfo?.gameCount ?? bulkInfo?.totalGames ?? player.gameCount ?? 0);
  const status = games > 0 ? "cached" : Number(player.eventCount ?? 0) > 0 ? "compare" : "missing";
  const message = status === "cached"
    ? `本库已缓存 ${games} 盘可复盘棋局。`
    : status === "compare"
    ? "已有赛事记录，但棋谱仍待与数据源比对。"
    : "目前只有注册信息，尚缺可复盘赛事来源。";
  return `<div class="coverage-callout">${dataStatusBadge(status)}<span>${escapeHTML(message)}</span></div>`;
}

function publicLocationFromSightings(sightings) {
  return uniqueStrings((sightings ?? []).map(publicLocationFromSighting).filter(Boolean))[0] || "";
}

function publicLocationFromSighting(sighting) {
  // Data layer now ships province-level `publicLocation` instead of the raw
  // club/school string; keep club-based fallback for stale cached payloads.
  if (sighting?.publicLocation) return String(sighting.publicLocation);
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
  return normalize(player.chineseName || player.presentationChineseName || player.displayName || player.name || "").replace(/[^0-9a-z\u4e00-\u9fff]/g, "");
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

function normalize(value) {
  return normalizeSearchText(displayText(value));
}

function uniqueStrings(values) {
  return [...new Set(values.filter(Boolean).map(String))];
}

function displayName(player) {
  return displayText(resolvePlayerDisplayName(player));
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
  const presentation = presentationNameDetail(player);
  if (presentation) {
    return `<div class="detail-cn-name">${escapeHTML(presentation.label)}：${escapeHTML(displayText(presentation.value))}</div>`;
  }
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
