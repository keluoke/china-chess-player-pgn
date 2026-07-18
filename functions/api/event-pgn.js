const DATA_ORIGIN = "https://data.chessdb.aigclabs.cc";

export async function onRequestGet({ request }) {
  const url = new URL(request.url);
  const tournamentID = url.searchParams.get("tnr") || "";
  if (!/^\d{1,12}$/.test(tournamentID)) {
    return new Response("invalid tournament id", { status: 400 });
  }

  const upstream = await fetch(`${DATA_ORIGIN}/events/chess-results/tnr${tournamentID}.pgn`, {
    cf: { cacheEverything: true, cacheTtl: 86400 }
  });
  if (!upstream.ok) {
    return new Response("PGN unavailable", {
      status: upstream.status === 404 ? 404 : 502,
      headers: { "Cache-Control": "no-store" }
    });
  }
  return new Response(upstream.body, {
    status: 200,
    headers: {
      "Content-Type": "application/x-chess-pgn; charset=utf-8",
      "Content-Disposition": `inline; filename="tnr${tournamentID}.pgn"`,
      "Cache-Control": "public, max-age=86400, stale-while-revalidate=604800",
      "X-Content-Type-Options": "nosniff"
    }
  });
}
