/**
 * Cloudflare Pages Functions endpoint for /api/v1/brilliancies/...
 *
 * Implements read-only REST API:
 * - GET /api/v1/brilliancies/manifest.json
 * - GET /api/v1/brilliancies/openapi.json
 * - GET /api/v1/brilliancies (query, filter, sort, paginate)
 * - GET /api/v1/brilliancies/:id.json
 * - GET /api/v1/brilliancies/:id.pgn
 */

function jsonResponse(data, status = 200, extraHeaders = {}, clientEtag = null) {
  const headers = {
    "content-type": "application/json; charset=utf-8",
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "GET, HEAD, OPTIONS",
    "access-control-expose-headers": "ETag",
    "cache-control": status === 200 ? "public, max-age=60, stale-while-revalidate=86400" : "no-store",
    ...extraHeaders,
  };
  const etag = extraHeaders.etag;
  if (status === 200 && etag && clientEtag && clientEtag === etag) {
    return new Response(null, { status: 304, headers });
  }
  return new Response(JSON.stringify(data), { status, headers });
}

function parseCursor(cursorStr) {
  try {
    const raw = atob(cursorStr);
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function encodeCursor(obj) {
  return btoa(JSON.stringify(obj));
}

export async function onRequestOptions() {
  return new Response(null, {
    status: 204,
    headers: {
      "access-control-allow-origin": "*",
      "access-control-allow-methods": "GET, HEAD, OPTIONS",
    "access-control-expose-headers": "ETag",
      "access-control-allow-headers": "content-type, if-none-match",
      "cache-control": "public, max-age=86400",
    },
  });
}

export async function onRequestHead(context) {
  const resp = await onRequestGet(context);
  return new Response(null, {
    status: resp.status,
    headers: resp.headers,
  });
}

export async function onRequestGet(context) {
  const url = new URL(context.request.url);
  const pathParam = context.params.path;
  const path = Array.isArray(pathParam) ? pathParam.join("/") : String(pathParam || "");
  const clientEtag = context.request.headers.get("if-none-match");

  // 1. Manifest
  if (path === "manifest.json") {
    const assetUrl = new URL("/data/brilliancies/manifest.json", context.request.url);
    const resp = await context.env.ASSETS.fetch(assetUrl);
    if (!resp.ok) return jsonResponse({ error: "manifest_unavailable" }, 503);
    const etag = resp.headers.get("etag");
    if (etag && clientEtag && clientEtag === etag) {
      return new Response(null, {
        status: 304,
        headers: {
          "access-control-allow-origin": "*",
          "etag": etag,
          "cache-control": "public, max-age=60, stale-while-revalidate=86400",
        },
      });
    }
    const body = await resp.text();
    return new Response(body, {
      status: 200,
      headers: {
        "content-type": "application/json; charset=utf-8",
        "access-control-allow-origin": "*",
        "cache-control": "public, max-age=60, stale-while-revalidate=86400",
        ...(etag ? { etag } : {}),
      },
    });
  }

  // 2. OpenAPI Spec
  if (path === "openapi.json") {
    const assetUrl = new URL("/data/brilliancies/openapi.json", context.request.url);
    const resp = await context.env.ASSETS.fetch(assetUrl);
    if (!resp.ok) return jsonResponse({ error: "openapi_unavailable" }, 503);
    const etag = resp.headers.get("etag");
    if (etag && clientEtag && clientEtag === etag) {
      return new Response(null, {
        status: 304,
        headers: {
          "access-control-allow-origin": "*",
          "etag": etag,
          "cache-control": "public, max-age=86400",
        },
      });
    }
    const body = await resp.text();
    return new Response(body, {
      status: 200,
      headers: {
        "content-type": "application/json; charset=utf-8",
        "access-control-allow-origin": "*",
        "cache-control": "public, max-age=86400",
        ...(etag ? { etag } : {}),
      },
    });
  }

  // 3. Single Detail JSON: :id.json
  const jsonMatch = path.match(/^(br-[0-9a-fA-F]{64})\.json$/);
  if (jsonMatch) {
    const id = jsonMatch[1].toLowerCase();
    const bucket = id.replace("br-", "")[0];
    const shardUrl = new URL(`/data/brilliancies/shards/${bucket}.json`, context.request.url);
    const shardResp = await context.env.ASSETS.fetch(shardUrl);
    if (!shardResp.ok) return jsonResponse({ error: "shard_unavailable" }, 503);

    let shardData;
    try {
      shardData = await shardResp.json();
    } catch {
      return jsonResponse({ error: "shard_invalid" }, 503);
    }

    // Snapshot check if parameter supplied
    const requestedSnapshot = url.searchParams.get("snapshot");
    if (requestedSnapshot && shardData.snapshotId && requestedSnapshot !== shardData.snapshotId) {
      return jsonResponse({ error: "snapshot_changed", currentSnapshot: shardData.snapshotId }, 409);
    }

    const item = shardData?.items?.[id];
    if (!item) {
      return jsonResponse({ error: "not_found", id }, 404);
    }

    // Status check (withdrawn handling)
    if (item.status === "withdrawn") {
      return jsonResponse(
        {
          error: "withdrawn",
          id,
          title: item.title,
          reason: "此条目经复核反驳已撤回展示，保留历史原局引用。",
          game: item.game,
        },
        410
      );
    }

    if (item.status !== "published") return jsonResponse({ error: "not_found" }, 404);
    const itemEtag = `"${shardData.snapshotId}-${id}"`;
    return jsonResponse(item, 200, { etag: itemEtag }, clientEtag);
  }

  // 4. Annotated PGN Snippet: :id.pgn
  const pgnMatch = path.match(/^(br-[0-9a-fA-F]{64})\.pgn$/);
  if (pgnMatch) {
    const id = pgnMatch[1].toLowerCase();
    const bucket = id.replace("br-", "")[0];
    const shardUrl = new URL(`/data/brilliancies/shards/${bucket}.json`, context.request.url);
    const shardResp = await context.env.ASSETS.fetch(shardUrl);
    if (!shardResp.ok) return jsonResponse({ error: "shard_unavailable" }, 503);
    let shardData;
    try { shardData = await shardResp.json(); }
    catch { return jsonResponse({ error: "shard_invalid" }, 503); }
    const requestedSnapshot = url.searchParams.get("snapshot");
    if (requestedSnapshot && requestedSnapshot !== shardData.snapshotId)
      return jsonResponse({ error: "snapshot_changed", currentSnapshot: shardData.snapshotId }, 409);
    const item = shardData?.items?.[id];
    if (!item) return jsonResponse({ error: "not_found" }, 404);
    if (item.status === "withdrawn") return jsonResponse({ error: "withdrawn" }, 410);
    if (item.status !== "published") return jsonResponse({ error: "not_found" }, 404);

    const pgnUrl = new URL(`/data/brilliancies/pgn/${id}.pgn`, context.request.url);
    const pgnResp = await context.env.ASSETS.fetch(pgnUrl);
    if (!pgnResp.ok) return new Response("PGN not found", { status: 404, headers: { "access-control-allow-origin": "*" } });
    const pgnText = await pgnResp.text();
    return new Response(pgnText, {
      status: 200,
      headers: {
        "content-type": "application/x-chess-pgn; charset=utf-8",
        "access-control-allow-origin": "*",
        "cache-control": "public, max-age=60",
        "content-disposition": `inline; filename="${id}.pgn"`,
      },
    });
  }

  // 5. List items: /api/v1/brilliancies (path is empty or "")
  if (path === "" || path === "/") {
    const itemsUrl = new URL("/data/brilliancies/items.json", context.request.url);
    const manifestUrl = new URL("/data/brilliancies/manifest.json", context.request.url);

    const [itemsResp, manifestResp] = await Promise.all([
      context.env.ASSETS.fetch(itemsUrl),
      context.env.ASSETS.fetch(manifestUrl),
    ]);

    if (!itemsResp.ok || !manifestResp.ok) {
      return jsonResponse({ error: "data_unavailable" }, 503);
    }

    let allItems, manifest;
    try {
      allItems = await itemsResp.json();
      manifest = await manifestResp.json();
    } catch {
      return jsonResponse({ error: "data_invalid" }, 503);
    }

    const currentSnapshot = manifest.snapshotId;

    // Validate limit parameter
    const limitRaw = url.searchParams.get("limit");
    let limit = 20;
    if (limitRaw !== null) {
      if (!/^\d+$/.test(limitRaw.trim())) {
        return jsonResponse({ error: "invalid_limit" }, 400);
      }
      limit = parseInt(limitRaw.trim(), 10);
      if (limit < 1 || limit > 100) {
        return jsonResponse({ error: "invalid_limit" }, 400);
      }
    }

    // Validate offset parameter
    const offsetRaw = url.searchParams.get("offset");
    let offset = 0;
    if (offsetRaw !== null) {
      if (!/^\d+$/.test(offsetRaw.trim())) {
        return jsonResponse({ error: "invalid_offset" }, 400);
      }
      offset = parseInt(offsetRaw.trim(), 10);
      if (!Number.isSafeInteger(offset)) return jsonResponse({ error: "invalid_offset" }, 400);
    }

    const filterPlayer = (url.searchParams.get("player") || "").trim().toLowerCase();
    const filterTheme = (url.searchParams.get("theme") || "").trim().toLowerCase();
    const filterEvent = (url.searchParams.get("event") || "").trim().toLowerCase();
    const sort = url.searchParams.get("sort") === "newest" ? "newest" : "featured";
    const cursorParam = url.searchParams.get("cursor");

    if (cursorParam) {
      const parsedCursor = parseCursor(cursorParam);
      if (!parsedCursor || !Number.isSafeInteger(parsedCursor.offset) || parsedCursor.offset < 0) {
        return jsonResponse({ error: "invalid_cursor" }, 400);
      }
      if (parsedCursor.snapshot && parsedCursor.snapshot !== currentSnapshot) {
        return jsonResponse({ error: "snapshot_changed", currentSnapshot }, 409);
      }
      if (parsedCursor.theme !== undefined && parsedCursor.theme !== filterTheme) {
        return jsonResponse({ error: "cursor_filter_mismatch" }, 400);
      }
      if (parsedCursor.player !== undefined && parsedCursor.player !== filterPlayer) {
        return jsonResponse({ error: "cursor_filter_mismatch" }, 400);
      }
      if (parsedCursor.event !== undefined && parsedCursor.event !== filterEvent) {
        return jsonResponse({ error: "cursor_filter_mismatch" }, 400);
      }
      if (parsedCursor.sort !== undefined && parsedCursor.sort !== sort) {
        return jsonResponse({ error: "cursor_filter_mismatch" }, 400);
      }
      offset = parsedCursor.offset;
    }

    const snapshotQuery = url.searchParams.get("snapshot");
    if (snapshotQuery && snapshotQuery !== currentSnapshot) {
      return jsonResponse({ error: "snapshot_changed", currentSnapshot }, 409);
    }

    // Filter items with EXACT matching on player and event
    let filtered = allItems.filter((item) => {
      if (filterPlayer) {
        const whiteId = String(item.white?.playerId || "").toLowerCase().replace(/^fide-/, "");
        const blackId = String(item.black?.playerId || "").toLowerCase().replace(/^fide-/, "");
        const normPlayer = filterPlayer.replace(/^fide-/, "");
        if (whiteId !== normPlayer && blackId !== normPlayer) {
          return false;
        }
      }
      if (filterTheme) {
        const themes = (item.themes || []).map((t) => String(t).toLowerCase());
        if (!themes.includes(filterTheme)) {
          return false;
        }
      }
      if (filterEvent) {
        const eventId = String(item.event?.id || "").toLowerCase();
        if (eventId !== filterEvent) {
          return false;
        }
      }
      return true;
    });

    // Sort
    if (sort === "newest") {
      filtered.sort((a, b) => {
        const dateA = String(a.event?.date || "");
        const dateB = String(b.event?.date || "");
        return dateB.localeCompare(dateA) || a.id.localeCompare(b.id);
      });
    }

    const total = filtered.length;
    const pageItems = filtered.slice(offset, offset + limit);
    const nextOffset = offset + pageItems.length;
    const nextCursor =
      nextOffset < total
        ? encodeCursor({
            snapshot: currentSnapshot,
            offset: nextOffset,
            sort,
            player: filterPlayer,
            theme: filterTheme,
            event: filterEvent,
          })
        : null;

    const listEtag = `"${encodeURIComponent(JSON.stringify([currentSnapshot,filterPlayer,filterTheme,filterEvent,sort,offset,limit]))}"`;

    return jsonResponse(
      {
        schemaVersion: 1,
        snapshotId: currentSnapshot,
        total,
        items: pageItems,
        nextCursor,
      },
      200,
      { etag: listEtag },
      clientEtag
    );
  }

  return jsonResponse({ error: "not_found", path }, 404);
}
