import assert from "node:assert/strict";
import fs from "node:fs/promises";

const source = await fs.readFile(new URL("../../functions/data/pgn/[[path]].js", import.meta.url), "utf8");
const moduleURL = `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
const { onRequestGet, onRequestHead } = await import(moduleURL);

const sha256 = "a".repeat(64);
const logicalPath = "data/pgn/by-player/fide-8622388/all.pgn";
const objectURL = `https://data.chessdb.aigclabs.cc/data/pgn/objects/sha256/aa/${sha256}.pgn`;
const calls = [];
const packageMetadata = {
  pgnPath: logicalPath,
  publicURL: objectURL,
  sha256,
  pgnBytes: 24
};
globalThis.fetch = async (url, options) => {
  calls.push({ url: String(url), options });
  if (String(url).endsWith("/data/index/by-player-buckets/34.json")) {
    return Response.json({ players: { "8622388": { packages: [packageMetadata] } } });
  }
  return new Response('[Event "Test"]\n\n1. e4 *\n', {
    status: 200,
    headers: { etag: '"abc123"', "content-length": "24" }
  });
};

const getResponse = await onRequestGet({
  params: { path: "by-player/fide-8622388/all.pgn" },
  request: new Request("https://4chess.cc/data/pgn/by-player/fide-8622388/all.pgn?sha=40ac2c4f1873200d")
});
assert.equal(getResponse.status, 200);
assert.equal(getResponse.headers.get("content-type"), "application/x-chess-pgn; charset=utf-8");
assert.equal(getResponse.headers.get("access-control-allow-origin"), "*");
assert.equal(getResponse.headers.get("cache-control"), "public, max-age=60, must-revalidate");
assert.equal(getResponse.headers.get("etag"), `"sha256-${sha256}"`);
assert.match(await getResponse.text(), /^\[Event/);
assert.equal(calls[0].url, "https://4chess.cc/data/index/by-player-buckets/34.json");
assert.equal(calls[1].url, objectURL);
assert.equal(calls[1].options.method, "GET");

const headResponse = await onRequestHead({
  params: { path: ["by-player", "fide-8622388", "all.pgn"] },
  request: new Request("https://4chess.cc/data/pgn/by-player/fide-8622388/all.pgn")
});
assert.equal(headResponse.status, 200);
assert.equal(await headResponse.text(), "");
assert.equal(calls[3].url, objectURL);
assert.equal(calls[3].options.method, "HEAD");

const invalidResponse = await onRequestGet({
  params: { path: "../secret.pgn" },
  request: new Request("https://4chess.cc/data/pgn/../secret.pgn")
});
assert.equal(invalidResponse.status, 400);
assert.equal(calls.length, 4);

packageMetadata.publicURL = `${objectURL}?unexpected=1`;
const queryMetadataResponse = await onRequestGet({
  params: { path: "by-player/fide-8622388/all.pgn" },
  request: new Request("https://4chess.cc/data/pgn/by-player/fide-8622388/all.pgn")
});
assert.equal(queryMetadataResponse.status, 503);

packageMetadata.publicURL = objectURL;
packageMetadata.pgnBytes = 25;
const wrongLengthResponse = await onRequestGet({
  params: { path: "by-player/fide-8622388/all.pgn" },
  request: new Request("https://4chess.cc/data/pgn/by-player/fide-8622388/all.pgn")
});
assert.equal(wrongLengthResponse.status, 502);

console.log("PGN compatibility proxy tests passed");
