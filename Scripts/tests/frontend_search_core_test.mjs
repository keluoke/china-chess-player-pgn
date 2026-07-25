import assert from "node:assert/strict";
import {
  defaultDomesticShardKey,
  isLikelyFideID,
  normalizeSearchText,
  routingKeysForQuery,
  searchScore,
  searchTokens,
  searchValuesForPlayer
} from "../../docs/search-core.js";

assert.equal(normalizeSearchText("Dīng, Lìrén"), "dingliren");
assert.equal(isLikelyFideID("8603006"), true);
assert.equal(isLikelyFideID("123"), false);
assert.ok(routingKeysForQuery("逸凡").includes("g:逸凡"));
assert.equal(routingKeysForQuery("王").length, 0);
assert.match(defaultDomesticShardKey("王"), /^h[0-9a-f]{2}$/);

const player = {
  fideID: "8603677",
  displayName: "Ding, Liren",
  pinyin: "Ding Liren"
};
const values = searchValuesForPlayer(player);
assert.ok(values.includes("dl"));
player.searchIndex = values.map(normalizeSearchText);
player.searchTokens = new Set(values.flatMap(searchTokens));
assert.ok(searchScore(player, "dingliren", ["dingliren"], "") > 0);
assert.ok(searchScore(player, "dl", ["dl"], "") > 0);

console.log("frontend search core tests OK");
