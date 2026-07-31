import assert from "node:assert/strict";
import fs from "node:fs/promises";

const source = await fs.readFile(new URL("../../functions/data/pgn/[[path]].js", import.meta.url), "utf8");
const moduleURL = `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
const { onRequestGet, onRequestHead } = await import(moduleURL);

const calls = [];
globalThis.fetch = async (url, options) => {
  calls.push({ url: String(url), options });
  return new Response('[Event "Test"]\n\n1. e4 *\n', {
    status: 200,
    headers: { etag: '"abc123"' }
  });
};

const getResponse = await onRequestGet({
  params: { path: "by-player/fide-8622388/all.pgn" },
  request: new Request("https://4chess.cc/data/pgn/by-player/fide-8622388/all.pgn?sha=40ac2c4f1873200d")
});
assert.equal(getResponse.status, 200);
assert.equal(getResponse.headers.get("content-type"), "application/x-chess-pgn; charset=utf-8");
assert.equal(getResponse.headers.get("access-control-allow-origin"), "*");
assert.match(await getResponse.text(), /^\[Event/);
assert.equal(calls[0].url, "https://data.chessdb.aigclabs.cc/data/pgn/by-player/fide-8622388/all.pgn?sha=40ac2c4f1873200d");
assert.equal(calls[0].options.method, "GET");

const headResponse = await onRequestHead({
  params: { path: ["by-player", "fide-8622388", "all.pgn"] },
  request: new Request("https://4chess.cc/data/pgn/by-player/fide-8622388/all.pgn")
});
assert.equal(headResponse.status, 200);
assert.equal(await headResponse.text(), "");
assert.equal(calls[1].options.method, "HEAD");

const invalidResponse = await onRequestGet({
  params: { path: "../secret.pgn" },
  request: new Request("https://4chess.cc/data/pgn/../secret.pgn")
});
assert.equal(invalidResponse.status, 400);
assert.equal(calls.length, 2);

console.log("PGN compatibility proxy tests passed");
