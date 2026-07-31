function json(body, status = 200, headers = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "access-control-allow-origin": "*",
      "cache-control": status === 200
        ? "public, max-age=300, s-maxage=86400"
        : "public, max-age=60",
      ...headers
    }
  });
}

export async function onRequestGet(context) {
  const path = String(context.params.path || "");
  const match = path.match(/^fide-(\d+)\.json$/);
  if (!match) return json({ error: "not_found" }, 404);

  const fideID = match[1];
  const bucket = (Number.parseInt(fideID, 10) % 256).toString(16).padStart(2, "0");
  const url = new URL(`/api/v1/player-buckets/${bucket}.json`, context.request.url);
  const response = await context.env.ASSETS.fetch(url);
  if (!response.ok) return json({ error: "bucket_unavailable" }, 502);

  let payload;
  try {
    payload = await response.json();
  } catch {
    return json({ error: "bucket_invalid" }, 502);
  }
  const player = payload?.players?.[fideID];
  if (!player) return json({ error: "player_not_found", fideID }, 404);
  return json(player);
}
