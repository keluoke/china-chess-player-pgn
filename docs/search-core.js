export const HANZI_SHARD_BUCKETS = 64;

export function normalizeSearchText(value) {
  return String(value ?? "")
    .normalize("NFKD")
    .toLowerCase()
    .replace(/\p{Diacritic}/gu, "")
    .replace(/[\s,.'’"()，。·_\-]+/g, "");
}

export function searchTokens(value) {
  return String(value ?? "")
    .normalize("NFKD")
    .toLowerCase()
    .replace(/\p{Diacritic}/gu, "")
    .replace(/[,.，。'’"()·_\-]+/g, " ")
    .split(/\s+/)
    .map(token => token.trim())
    .filter(Boolean);
}

export function searchInitials(value) {
  const tokens = searchTokens(value);
  return tokens.length >= 2 && tokens.every(token => /^[a-z]/.test(token))
    ? tokens.map(token => token[0]).join("")
    : "";
}

export function searchValuesForPlayer(player) {
  const values = [
    player.fideID,
    player.displayName,
    player.name,
    player.chineseName,
    player.presentationChineseName,
    player.pinyin,
    ...(player.aliases ?? [])
  ].filter(Boolean).map(String);

  for (const value of [...values]) {
    const parts = searchTokens(value);
    if (parts.length >= 2) {
      values.push(parts.join(" "));
      values.push(parts.slice().reverse().join(" "));
      values.push(parts.map(part => part[0]).join(""));
    }
  }
  return [...new Set(values)];
}

export function searchScore(player, normalized, tokens, reversed) {
  const terms = player.searchIndex ?? [];
  let best = 0;
  for (const term of terms) {
    if (term === normalized) best = Math.max(best, 1200);
    else if (reversed && term === reversed) best = Math.max(best, 1160);
    else if (term.startsWith(normalized)) {
      best = Math.max(best, 1000 + Math.min(120, normalized.length * 12) - Math.min(80, term.length - normalized.length));
    } else {
      const index = term.indexOf(normalized);
      if (index >= 0) best = Math.max(best, 760 - Math.min(180, index * 18) + Math.min(100, normalized.length * 8));
    }
  }
  if (!best && tokens.length && tokens.every(token => player.searchTokens?.has(token))) best = 680;
  if (!best && tokens.length && tokens.every(token => terms.some(term => term.includes(token)))) best = 580;
  return best;
}

export function playerQualityScore(player) {
  const games = Math.min(80, Number(player.gameCount || 0));
  const events = Math.min(30, Number(player.eventCount || 0));
  const active = player.inactive ? -120 : 30;
  const federation = player.formerFederation || (player.federation && player.federation !== "CHN") ? -15 : 0;
  return games * 2 + events * 3 + active + federation;
}

export function defaultDomesticShardKey(query) {
  const normalized = normalizeSearchText(query);
  const idMatch = normalized.match(/^domestic([0-9a-f])/);
  if (idMatch) return `id${idMatch[1]}`;
  const first = normalized[0];
  if (!first) return "";
  if (first >= "一" && first <= "鿿") {
    return `h${(first.codePointAt(0) % HANZI_SHARD_BUCKETS).toString(16).padStart(2, "0")}`;
  }
  if (first >= "a" && first <= "z") return `p${first}`;
  return "";
}

export function routingKeysForQuery(query) {
  const normalized = normalizeSearchText(query);
  const hanzi = [...normalized].filter(char => char >= "一" && char <= "鿿");
  const keys = [];
  for (let index = 0; index < hanzi.length - 1; index += 1) keys.push(`g:${hanzi[index]}${hanzi[index + 1]}`);
  if (/^[a-z]+$/.test(normalized)) keys.push(`p:${normalized}`);
  return keys;
}

export function isSingleHanziQuery(value) {
  return /^[\u4e00-\u9fff]$/.test(normalizeSearchText(value));
}

export function isLikelyFideID(value) {
  return /^\d{7,8}$/.test(normalizeSearchText(value));
}
