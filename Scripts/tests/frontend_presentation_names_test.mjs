import assert from "node:assert/strict";
import test from "node:test";

import {
  applyPresentationName,
  buildPresentationNameIndex,
  presentationNameBadge,
  presentationNameDetail,
  resolvePlayerDisplayName
} from "../../docs/presentation-names.js";

test("authoritative registry name always wins", () => {
  const index = buildPresentationNameIndex({
    players: [{ fideID: "1", suggestedChineseName: "候选姓名", confidence: "high" }]
  });
  const player = { fideID: "1", chineseName: "权威姓名", name: "Authoritative, Name" };
  applyPresentationName(player, index.get("1"));
  assert.equal(resolvePlayerDisplayName(player), "权威姓名 · Authoritative, Name");
  assert.equal(player.presentationChineseName, undefined);
  assert.equal(presentationNameBadge(player), null);
});

test("high confidence is the only default presentation fallback", () => {
  const index = buildPresentationNameIndex({
    players: [{ fideID: "2", suggestedChineseName: "高置信名", confidence: "high" }]
  });
  const player = applyPresentationName({ fideID: "2", name: "Gao, Confidence" }, index.get("2"));
  assert.equal(resolvePlayerDisplayName(player), "高置信名 · Gao, Confidence");
  assert.equal(presentationNameBadge(player)?.key, "presentation-high");
  assert.deepEqual(presentationNameDetail(player), {
    label: "中文名暂定（高置信）",
    value: "高置信名"
  });
});

test("medium confidence is detail-only and falls back to latin by default", () => {
  const index = buildPresentationNameIndex({
    players: [{ fideID: "3", suggestedChineseName: "单场候选", confidence: "medium" }]
  });
  const player = applyPresentationName({ fideID: "3", name: "Single, Event" }, index.get("3"));
  assert.equal(resolvePlayerDisplayName(player), "Single, Event");
  assert.equal(presentationNameBadge(player), null);
  assert.equal(presentationNameBadge(player, { detail: true })?.key, "presentation-medium");
  assert.equal(presentationNameDetail(player)?.value, "单场候选");
});

test("conflicts, malformed names and historical mistakes never enter the index", () => {
  const index = buildPresentationNameIndex({
    players: [
      { fideID: "4", suggestedChineseName: "冲突姓名", confidence: "conflict" },
      { fideID: "5", suggestedChineseName: "赛事标题冠军组", confidence: "high" },
      { fideID: "8602980", suggestedChineseName: "居文君", confidence: "high" },
      { fideID: "8608288", suggestedChineseName: "徐翔宇", confidence: "high" }
    ]
  });
  assert.equal(index.size, 0);
});

test("removing a presentation candidate cleanly falls back to registry latin name", () => {
  const player = { fideID: "6", name: "Fallback, Player" };
  assert.equal(resolvePlayerDisplayName(player), "Fallback, Player");
});
