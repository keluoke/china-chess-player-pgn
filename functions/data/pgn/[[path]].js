const DATA_ORIGIN = "https://data.chessdb.aigclabs.cc";
const SAFE_PGN_PATH = /^(?:[A-Za-z0-9._-]+\/)*[A-Za-z0-9._-]+\.pgn$/;
const BY_PLAYER_PATH = /^by-player\/fide-(\d+)\/([A-Za-z0-9._-]+)\.pgn$/;
const CONTENT_OBJECT_PATH = /^\/data\/pgn\/objects\/sha256\/([0-9a-f]{2})\/([0-9a-f]{64})\.pgn$/;
const SHA256 = /^[0-9a-f]{64}$/;

function requestedPath(params) {
  const value = Array.isArray(params?.path) ? params.path.join("/") : String(params?.path || "");
  return SAFE_PGN_PATH.test(value) && !value.split("/").includes("..") ? value : "";
}

async function resolveByPlayerObject(request, path) {
  const match = BY_PLAYER_PATH.exec(path);
  if (!match) return null;
  const fideID = match[1];
  const bucket = (Number.parseInt(fideID, 10) % 256).toString(16).padStart(2, "0");
  const bucketURL = new URL(`/data/index/by-player-buckets/${bucket}.json`, request.url);
  const response = await fetch(bucketURL, { cf: { cacheEverything: true, cacheTtl: 300 } });
  if (!response.ok) throw new Error("player PGN index unavailable");
  const payload = await response.json();
  const detail = payload?.players?.[fideID];
  const logicalPath = `data/pgn/${path}`;
  const item = detail?.packages?.find((entry) => entry?.pgnPath === logicalPath);
  if (!item) return { missing: true };
  const publicURL = new URL(String(item.publicURL || ""));
  const objectMatch = CONTENT_OBJECT_PATH.exec(publicURL.pathname);
  const sha256 = String(item.sha256 || "");
  const bytes = Number(item.pgnBytes);
  if (
    publicURL.origin !== DATA_ORIGIN
    || publicURL.username
    || publicURL.password
    || publicURL.search
    || publicURL.hash
    || !objectMatch
    || !SHA256.test(sha256)
    || objectMatch[1] !== sha256.slice(0, 2)
    || objectMatch[2] !== sha256
    || !Number.isSafeInteger(bytes)
    || bytes <= 0
  ) {
    throw new Error("player PGN object metadata mismatch");
  }
  return {
    url: publicURL,
    sha256,
    bytes
  };
}

async function proxyPGN(context, headOnly = false) {
  const path = requestedPath(context.params);
  if (!path) {
    return new Response("invalid PGN path", {
      status: 400,
      headers: { "Cache-Control": "no-store" }
    });
  }

  let resolved;
  try {
    resolved = await resolveByPlayerObject(context.request, path);
  } catch {
    return new Response("PGN index unavailable", {
      status: 503,
      headers: { "Cache-Control": "no-store" }
    });
  }
  if (resolved?.missing) {
    return new Response("PGN unavailable", {
      status: 404,
      headers: { "Cache-Control": "no-store" }
    });
  }
  const upstreamURL = resolved?.url || new URL(`/data/pgn/${path}`, DATA_ORIGIN);
  if (!resolved) upstreamURL.search = new URL(context.request.url).search;
  const upstream = await fetch(upstreamURL, {
    method: headOnly ? "HEAD" : "GET",
    cf: { cacheEverything: true, cacheTtl: resolved ? 31536000 : 60 }
  });
  if (!upstream.ok) {
    return new Response("PGN unavailable", {
      status: upstream.status === 404 ? 404 : 502,
      headers: { "Cache-Control": "no-store" }
    });
  }
  const upstreamLength = Number(upstream.headers.get("content-length"));
  if (resolved && upstreamLength !== resolved.bytes) {
    return new Response("PGN object metadata mismatch", {
      status: 502,
      headers: { "Cache-Control": "no-store" }
    });
  }

  const headers = {
    "Content-Type": "application/x-chess-pgn; charset=utf-8",
    // This response is a mutable logical path even when its current upstream
    // object is immutable. Keep the browser TTL short so a later snapshot can
    // resolve the same player/package path to new content.
    "Cache-Control": "public, max-age=60, must-revalidate",
    "Access-Control-Allow-Origin": "*",
    "X-Content-Type-Options": "nosniff"
  };
  const etag = upstream.headers.get("etag");
  if (resolved?.sha256) headers.ETag = `"sha256-${resolved.sha256}"`;
  else if (etag) headers.ETag = etag;
  if (resolved?.bytes) headers["Content-Length"] = String(resolved.bytes);
  return new Response(headOnly ? null : upstream.body, { status: 200, headers });
}

export function onRequestGet(context) {
  return proxyPGN(context, false);
}

export function onRequestHead(context) {
  return proxyPGN(context, true);
}
