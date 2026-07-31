const DATA_ORIGIN = "https://data.chessdb.aigclabs.cc";
const SAFE_PGN_PATH = /^(?:[A-Za-z0-9._-]+\/)*[A-Za-z0-9._-]+\.pgn$/;

function requestedPath(params) {
  const value = Array.isArray(params?.path) ? params.path.join("/") : String(params?.path || "");
  return SAFE_PGN_PATH.test(value) && !value.split("/").includes("..") ? value : "";
}

async function proxyPGN(context, headOnly = false) {
  const path = requestedPath(context.params);
  if (!path) {
    return new Response("invalid PGN path", {
      status: 400,
      headers: { "Cache-Control": "no-store" }
    });
  }

  const upstreamURL = new URL(`/data/pgn/${path}`, DATA_ORIGIN);
  upstreamURL.search = new URL(context.request.url).search;
  const upstream = await fetch(upstreamURL, {
    method: headOnly ? "HEAD" : "GET",
    cf: { cacheEverything: true, cacheTtl: 86400 }
  });
  if (!upstream.ok) {
    return new Response("PGN unavailable", {
      status: upstream.status === 404 ? 404 : 502,
      headers: { "Cache-Control": "no-store" }
    });
  }

  const headers = {
    "Content-Type": "application/x-chess-pgn; charset=utf-8",
    "Cache-Control": "public, max-age=86400, stale-while-revalidate=604800",
    "Access-Control-Allow-Origin": "*",
    "X-Content-Type-Options": "nosniff"
  };
  const etag = upstream.headers.get("etag");
  if (etag) headers.ETag = etag;
  return new Response(headOnly ? null : upstream.body, { status: 200, headers });
}

export function onRequestGet(context) {
  return proxyPGN(context, false);
}

export function onRequestHead(context) {
  return proxyPGN(context, true);
}
