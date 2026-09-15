const DATA_ORIGIN = "https://data.chessdb.aigclabs.cc";
const fail = (message, status) => new Response(message, { status, headers: { "Cache-Control": "no-store" } });

async function proxy({ request }, headOnly = false) {
  const params = new URL(request.url).searchParams;
  const tournamentID = params.get("tnr") || params.get("event") || "";
  const objectName = /^\d+$/.test(tournamentID) ? `tnr${tournamentID}` : tournamentID;
  const requestedHash = params.get("sha") || "";
  if (requestedHash && !/^(?:[0-9a-f]{16}|[0-9a-f]{64})$/.test(requestedHash)) return fail("invalid PGN version", 400);
  if (!/^(?:\d{1,12}|event-[0-9a-f]{24})$/.test(tournamentID)) return fail("invalid tournament id", 400);
  try {
    const manifest = await fetch(new URL("/data/index/event-pgn-objects.json", request.url), {
      cf: { cacheEverything: true, cacheTtl: 60 }
    });
    let object = null;
    if (manifest.ok) {
      object = (await manifest.json())?.events?.[tournamentID];
      if (!object) return fail("PGN unavailable", 404);
      const sha = String(object.sha256 || "");
      const key = `events/chess-results/objects/sha256/${sha.slice(0, 2)}/${sha}.pgn`;
      if (!/^[0-9a-f]{64}$/.test(sha) || object.key !== key
          || object.publicURL !== `${DATA_ORIGIN}/${key}`
          || object.path !== `events/chess-results/${objectName}.pgn`
          || !Number.isSafeInteger(object.bytes) || object.bytes <= 0) {
        return fail("PGN object metadata mismatch", 503);
      }
      if (requestedHash && !sha.startsWith(requestedHash)) return fail("PGN snapshot changed; reload event", 409);
    } else {
      return fail("PGN index unavailable", 503);
    }
    const url = object.publicURL;
    const upstream = await fetch(url, { method: headOnly ? "HEAD" : "GET",
      cf: { cacheEverything: true, cacheTtl: 31536000 } });
    if (!upstream.ok) return fail("PGN unavailable", upstream.status === 404 ? 404 : 502);
    if (object && Number(upstream.headers.get("content-length")) !== object.bytes) {
      return fail("PGN object length mismatch", 502);
    }
    const headers = {
      "Content-Type": "application/x-chess-pgn; charset=utf-8",
      "Content-Disposition": `inline; filename="${objectName}.pgn"`,
      "Cache-Control": "public, max-age=60, must-revalidate",
      "X-Content-Type-Options": "nosniff",
    };
    if (object) headers.ETag = `"sha256-${object.sha256}"`;
    return new Response(headOnly ? null : upstream.body, { status: 200, headers });
  } catch {
    return fail("PGN upstream unavailable", 502);
  }
}

export const onRequestGet = context => proxy(context);
export const onRequestHead = context => proxy(context, true);
