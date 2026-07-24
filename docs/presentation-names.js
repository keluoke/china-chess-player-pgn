const HAN_NAME = /^[\u3400-\u9fff]{2,6}$/u;

// Historical identity mistakes are pinned at the presentation boundary too.
// The registry/manual correction layer remains authoritative; this prevents a
// stale event observation from resurfacing while a snapshot is being rebuilt.
const FORBIDDEN_PRESENTATION_NAMES = new Map([
  ["8602980", new Set(["居文君"])],
  ["8608288", new Set(["徐翔宇"])]
]);

export function buildPresentationNameIndex(payload) {
  const index = new Map();
  const rejectedFideIDs = new Set();
  for (const row of payload?.players ?? []) {
    const fideID = String(row?.fideID ?? "").trim();
    const suggestedChineseName = String(row?.suggestedChineseName ?? "").trim();
    const confidence = normalizedConfidence(row);
    if (!fideID || rejectedFideIDs.has(fideID) || !HAN_NAME.test(suggestedChineseName)) continue;
    if (!["high", "medium"].includes(confidence)) continue;
    if (FORBIDDEN_PRESENTATION_NAMES.get(fideID)?.has(suggestedChineseName)) continue;
    if (index.has(fideID)) {
      // Duplicate candidates are not deterministic enough for presentation.
      index.delete(fideID);
      rejectedFideIDs.add(fideID);
      continue;
    }
    index.set(fideID, {
      fideID,
      suggestedChineseName,
      confidence,
      displayPolicy: confidence === "high" ? "default" : "detail-only",
      provisional: true
    });
  }
  return index;
}

export function applyPresentationName(player, candidate) {
  if (!player || player.chineseName || !candidate) return player;
  player.presentationNameCandidate = candidate.suggestedChineseName;
  player.presentationNameConfidence = candidate.confidence;
  player.presentationNamePolicy = candidate.displayPolicy;
  if (candidate.confidence === "high" && candidate.displayPolicy === "default") {
    player.presentationChineseName = candidate.suggestedChineseName;
  } else {
    delete player.presentationChineseName;
  }
  return player;
}

export function resolvePlayerDisplayName(player, { includeLatin = true } = {}) {
  const chineseName = String(
    player?.chineseName
    || (player?.presentationNameConfidence === "high" ? player?.presentationChineseName : "")
    || ""
  ).trim();
  const latinName = String(player?.name || player?.displayName || "").trim();
  if (chineseName && includeLatin && latinName && chineseName !== latinName) {
    return `${chineseName} · ${latinName}`;
  }
  return chineseName || latinName || (player?.fideID ? `FIDE ${player.fideID}` : "姓名待核验");
}

export function presentationNameBadge(player, { detail = false } = {}) {
  if (!player || player.chineseName) return null;
  if (player.presentationNameConfidence === "high" && player.presentationChineseName) {
    return {
      key: "presentation-high",
      label: "中文名高置信暂定",
      title: "同一 FIDE ID 在至少两场赛事中出现一致中文名，尚未写入权威主档"
    };
  }
  if (detail && player.presentationNameConfidence === "medium" && player.presentationNameCandidate) {
    return {
      key: "presentation-medium",
      label: "中文名待核验",
      title: "该中文名目前仅有单场赛事观测，不参与默认展示"
    };
  }
  return null;
}

export function presentationNameDetail(player) {
  if (!player || player.chineseName) return null;
  if (player.presentationNameConfidence === "high" && player.presentationChineseName) {
    return {
      label: "中文名暂定（高置信）",
      value: player.presentationChineseName
    };
  }
  if (player.presentationNameConfidence === "medium" && player.presentationNameCandidate) {
    return {
      label: "可能中文名（单场观测，待核验）",
      value: player.presentationNameCandidate
    };
  }
  return null;
}

function normalizedConfidence(row) {
  const explicit = String(row?.confidence ?? "").trim().toLowerCase();
  if (explicit) return explicit;
  return String(row?.identityBasis ?? "").includes("high") ? "high" : "";
}
