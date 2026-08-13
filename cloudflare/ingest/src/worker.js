import {
  HEX64,
  blobKey,
  bytesFromHex,
  canonicalRequest,
  chunkFingerprintText,
  estimateReservation,
  normalizeReleaseChunk,
  normalizeReleaseHeader,
  numericBudget,
  threeWayDecision,
} from "./policy.js";

const JSON_HEADERS = { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" };

function json(payload, status = 200) {
  return new Response(JSON.stringify(payload), { status, headers: JSON_HEADERS });
}

function nowIso() {
  return new Date().toISOString();
}

function dayKey() {
  return nowIso().slice(0, 10);
}

function monthKey() {
  return nowIso().slice(0, 7);
}

function multipartPartKey(runId, sha256, part) {
  return `ingest/multipart-parts/${runId}/${sha256}/${String(part.number).padStart(3, "0")}-${part.sha256}`;
}

function errorCode(error) {
  const text = String(error?.message || error || "INTERNAL_ERROR");
  return /^[A-Z0-9_]+(?::|$)/.test(text) ? text.split(":", 1)[0] : "INTERNAL_ERROR";
}

async function sha256Hex(bytes) {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, "0")).join("");
}

async function verifySignature(request, env, rawBody, uploadHash = null) {
  if (!env.INGEST_HMAC_SECRET) throw new Error("AUTH_SECRET_MISSING");
  const timestamp = request.headers.get("x-chess-timestamp") || "";
  const nonce = request.headers.get("x-chess-nonce") || "";
  const signature = request.headers.get("x-chess-signature") || "";
  const claimedHash = request.headers.get("x-chess-content-sha256") || "";
  const seconds = Number(timestamp);
  if (!Number.isFinite(seconds) || Math.abs(Date.now() / 1000 - seconds) > 300) throw new Error("AUTH_TIMESTAMP_INVALID");
  if (!/^[0-9a-f]{32}$/.test(nonce) || !HEX64.test(signature) || !HEX64.test(claimedHash)) throw new Error("AUTH_HEADER_INVALID");
  if (uploadHash) {
    if (claimedHash !== uploadHash) throw new Error("AUTH_BODY_HASH_INVALID");
  } else {
    const actualHash = await sha256Hex(rawBody);
    if (actualHash !== claimedHash) throw new Error("AUTH_BODY_HASH_INVALID");
  }
  const url = new URL(request.url);
  const canonical = canonicalRequest(request.method, url.pathname, timestamp, nonce, claimedHash);
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(env.INGEST_HMAC_SECRET),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["verify"],
  );
  const valid = await crypto.subtle.verify(
    "HMAC",
    key,
    bytesFromHex(signature),
    new TextEncoder().encode(canonical),
  );
  if (!valid) throw new Error("AUTH_SIGNATURE_INVALID");
  const inserted = await env.DB.prepare(
    "INSERT OR IGNORE INTO used_nonces(nonce, seen_at) VALUES (?1, ?2)",
  ).bind(nonce, Math.floor(Date.now() / 1000)).run();
  if (Number(inserted.meta?.changes || 0) !== 1) throw new Error("AUTH_REPLAYED_NONCE");
  // The signed timestamp is valid for five minutes. Retaining ten minutes is
  // enough to reject every valid replay while keeping this table bounded.
  await env.DB.prepare("DELETE FROM used_nonces WHERE seen_at < ?1")
    .bind(Math.floor(Date.now() / 1000) - 600).run();
}

async function reserveQuota(env, reservation) {
  const day = dayKey();
  const month = monthKey();
  await env.DB.prepare("INSERT OR IGNORE INTO quota_daily(day) VALUES (?1)").bind(day).run();
  await env.DB.prepare("INSERT OR IGNORE INTO quota_monthly(month) VALUES (?1)").bind(month).run();
  const daily = await env.DB.prepare(`
    UPDATE quota_daily SET
      releases = releases + ?2,
      worker_requests = worker_requests + ?3,
      d1_rows_read = d1_rows_read + ?4,
      d1_rows_written = d1_rows_written + ?5,
      queue_ops = queue_ops + ?6
    WHERE day = ?1
      AND releases + ?2 <= ?7
      AND worker_requests + ?3 <= ?8
      AND d1_rows_read + ?4 <= ?9
      AND d1_rows_written + ?5 <= ?10
      AND queue_ops + ?6 <= ?11
  `).bind(
    day,
    reservation.releases,
    reservation.workerRequests,
    reservation.d1RowsRead,
    reservation.d1RowsWritten,
    reservation.queueOps,
    numericBudget(env, "MAX_DAILY_RELEASES"),
    numericBudget(env, "MAX_DAILY_WORKER_REQUESTS"),
    numericBudget(env, "MAX_DAILY_D1_ROWS_READ"),
    numericBudget(env, "MAX_DAILY_D1_ROWS_WRITTEN"),
    numericBudget(env, "MAX_DAILY_QUEUE_OPS"),
  ).run();
  if (Number(daily.meta?.changes || 0) !== 1) throw new Error("FREE_TIER_DAILY_BUDGET_EXHAUSTED");
  const monthly = await env.DB.prepare(`
    UPDATE quota_monthly SET
      r2_class_a = r2_class_a + ?2,
      r2_class_b = r2_class_b + ?3,
      storage_reserved_bytes = storage_reserved_bytes + ?4
    WHERE month = ?1
      AND r2_class_a + ?2 <= ?5
      AND r2_class_b + ?3 <= ?6
      AND storage_reserved_bytes + ?4 <= ?7
  `).bind(
    month,
    reservation.r2ClassA,
    reservation.r2ClassB,
    reservation.storageReservedBytes,
    numericBudget(env, "MAX_MONTHLY_R2_CLASS_A"),
    numericBudget(env, "MAX_MONTHLY_R2_CLASS_B"),
    numericBudget(env, "MAX_SHADOW_STORAGE_BYTES"),
  ).run();
  if (Number(monthly.meta?.changes || 0) !== 1) {
    throw new Error("FREE_TIER_MONTHLY_BUDGET_EXHAUSTED");
  }
  const storage = await env.DB.prepare(`
    UPDATE quota_storage SET d1_reserved_bytes = d1_reserved_bytes + ?2,
      updated_at = CURRENT_TIMESTAMP
    WHERE key = ?1 AND d1_reserved_bytes + ?2 <= ?3
  `).bind(
    "ingest",
    reservation.d1StorageReservedBytes,
    numericBudget(env, "MAX_D1_STORAGE_BYTES"),
  ).run();
  if (Number(storage.meta?.changes || 0) !== 1) {
    throw new Error("FREE_TIER_D1_STORAGE_BUDGET_EXHAUSTED");
  }
}

async function takeRequestBudget(env) {
  const day = dayKey();
  await env.DB.prepare("INSERT OR IGNORE INTO quota_daily(day) VALUES (?1)").bind(day).run();
  const result = await env.DB.prepare(`
    UPDATE quota_daily SET worker_requests = worker_requests + 1
    WHERE day = ?1 AND worker_requests + 1 <= ?2
  `).bind(day, numericBudget(env, "MAX_DAILY_WORKER_REQUESTS")).run();
  if (Number(result.meta?.changes || 0) !== 1) throw new Error("FREE_TIER_WORKER_REQUEST_BUDGET_EXHAUSTED");
}

async function takeD1ReadBudget(env, rows) {
  const day = dayKey();
  await env.DB.prepare("INSERT OR IGNORE INTO quota_daily(day) VALUES (?1)").bind(day).run();
  const result = await env.DB.prepare(`
    UPDATE quota_daily SET d1_rows_read=d1_rows_read+?2
    WHERE day=?1 AND d1_rows_read+?2<=?3
  `).bind(day, rows, numericBudget(env, "MAX_DAILY_D1_ROWS_READ")).run();
  if (Number(result.meta?.changes || 0) !== 1) throw new Error("FREE_TIER_D1_READ_BUDGET_EXHAUSTED");
}

async function registerRelease(request, env, rawBody) {
  const payload = JSON.parse(new TextDecoder().decode(rawBody));
  const manifest = normalizeReleaseHeader(payload, env);
  const existing = await env.DB.prepare("SELECT status,manifest_sha256 FROM releases WHERE run_id = ?1").bind(manifest.runId).first();
  if (existing) {
    const existingHash = String(existing.manifest_sha256 || "");
    if (!existingHash && ["complete", "conflict", "failed"].includes(String(existing.status))) {
      return json({ ok: true, runId: manifest.runId, status: existing.status, idempotent: true, legacy: true });
    }
    if (!existingHash) throw new Error("RELEASE_LEGACY_STATE_UNRESUMABLE");
    if (existingHash !== manifest.manifestSha256) {
      throw new Error("RELEASE_MANIFEST_HASH_MISMATCH");
    }
    return json({ ok: true, runId: manifest.runId, status: existing.status, idempotent: true });
  }
  await reserveQuota(env, estimateReservation(manifest, env));
  const timestamp = nowIso();
  await env.DB.prepare(`
    INSERT INTO releases(
      run_id,status,command,base_commit,source_json,expected_files,expected_bytes,
      manifest_sha256,expected_upserts,expected_chunks,chunk_hashes_json,
      expected_multipart_files,expected_upload_parts,created_at,updated_at
    ) VALUES (?1,'registering',?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?13)
  `).bind(
    manifest.runId,
    manifest.command,
    manifest.baseCommit,
    JSON.stringify(manifest.source),
    manifest.expectedFiles,
    manifest.expectedBytes,
    manifest.manifestSha256,
    manifest.expectedUpserts,
    manifest.expectedChunks,
    JSON.stringify(manifest.chunkSha256s),
    manifest.expectedMultipartFiles,
    manifest.expectedUploadParts,
    timestamp,
  ).run();
  return json({
    ok: true,
    mode: env.SERVICE_MODE,
    runId: manifest.runId,
    status: "registering",
    files: manifest.expectedFiles,
    bytes: manifest.expectedBytes,
    chunks: manifest.expectedChunks,
  }, 201);
}

async function registerChunk(env, runId, chunkIndex, rawBody) {
  const release = await env.DB.prepare(`
    SELECT status,source_json,manifest_sha256,expected_files,expected_bytes,expected_chunks,
      expected_multipart_files,expected_upload_parts,registered_files,registered_bytes,
      registered_chunks,registered_multipart_files,registered_upload_parts,chunk_hashes_json
    FROM releases WHERE run_id=?1
  `).bind(runId).first();
  if (!release) throw new Error("RELEASE_NOT_FOUND");
  const payload = JSON.parse(new TextDecoder().decode(rawBody));
  if (Number(payload.chunkIndex) !== Number(chunkIndex)) throw new Error("RELEASE_CHUNK_INDEX_INVALID");
  const chunk = normalizeReleaseChunk(payload, release, env);
  const actualChunkSha256 = await sha256Hex(new TextEncoder().encode(chunkFingerprintText(chunk.files)));
  if (actualChunkSha256 !== chunk.chunkSha256) throw new Error("RELEASE_CHUNK_HASH_MISMATCH");
  const chunkSha256 = await sha256Hex(rawBody);
  const existing = await env.DB.prepare(`
    SELECT chunk_sha256 FROM release_chunks WHERE run_id=?1 AND chunk_index=?2
  `).bind(runId, chunk.chunkIndex).first();
  if (existing) {
    if (String(existing.chunk_sha256) !== chunkSha256) throw new Error("RELEASE_CHUNK_MISMATCH");
    return json({ ok: true, runId, chunkIndex: chunk.chunkIndex, status: release.status, idempotent: true });
  }
  if (!["registering", "registered"].includes(String(release.status))) throw new Error("RELEASE_REGISTRATION_CLOSED");
  const nextFiles = Number(release.registered_files) + chunk.files.length;
  const nextBytes = Number(release.registered_bytes) + chunk.totalBytes;
  const nextChunks = Number(release.registered_chunks) + 1;
  const nextMultipartFiles = Number(release.registered_multipart_files) + chunk.multipartFiles;
  const nextUploadParts = Number(release.registered_upload_parts) + chunk.uploadParts;
  if (
    nextFiles > Number(release.expected_files)
    || nextBytes > Number(release.expected_bytes)
    || nextChunks > Number(release.expected_chunks)
    || nextMultipartFiles > Number(release.expected_multipart_files)
    || nextUploadParts > Number(release.expected_upload_parts)
  ) throw new Error("RELEASE_REGISTRATION_OVERFLOW");
  const timestamp = nowIso();
  await env.DB.batch([
    ...chunk.files.map((item) => env.DB.prepare(`
      INSERT INTO release_files(
        run_id,path,operation,candidate_sha256,base_sha256,bytes,blob_key,uploaded,
        upload_mode,expected_parts,parts_json
      ) VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11)
    `).bind(
      runId,
      item.path,
      item.operation,
      item.sha256,
      item.baseSha256,
      item.bytes,
      item.blobKey,
      item.operation === "delete" ? 1 : 0,
      item.uploadMode || "single",
      item.expectedParts || 0,
      JSON.stringify(item.multipart?.parts || []),
    )),
    env.DB.prepare(`
      INSERT INTO release_chunks(run_id,chunk_index,chunk_sha256,files,bytes,created_at)
      VALUES (?1,?2,?3,?4,?5,?6)
    `).bind(runId, chunk.chunkIndex, chunkSha256, chunk.files.length, chunk.totalBytes, timestamp),
    env.DB.prepare(`
      UPDATE releases SET registered_files=?2,registered_bytes=?3,registered_chunks=?4,
        registered_multipart_files=?5,registered_upload_parts=?6,status=?7,updated_at=?8
      WHERE run_id=?1
    `).bind(
      runId,
      nextFiles,
      nextBytes,
      nextChunks,
      nextMultipartFiles,
      nextUploadParts,
      nextChunks === Number(release.expected_chunks) ? "registered" : "registering",
      timestamp,
    ),
  ]);
  return json({
    ok: true,
    runId,
    chunkIndex: chunk.chunkIndex,
    status: nextChunks === Number(release.expected_chunks) ? "registered" : "registering",
    registeredFiles: nextFiles,
    registeredChunks: nextChunks,
  }, 201);
}

async function uploadFile(request, env, runId, sha256) {
  if (!HEX64.test(sha256)) throw new Error("RELEASE_HASH_INVALID");
  const row = await env.DB.prepare(`
    SELECT bytes, blob_key, uploaded, upload_mode FROM release_files
    WHERE run_id = ?1 AND candidate_sha256 = ?2 AND operation = 'upsert'
  `).bind(runId, sha256).first();
  if (!row) throw new Error("RELEASE_FILE_UNDECLARED");
  if (String(row.upload_mode) !== "single") throw new Error("RELEASE_MULTIPART_REQUIRED");
  const length = Number(request.headers.get("content-length"));
  if (!Number.isSafeInteger(length) || length !== Number(row.bytes)) throw new Error("RELEASE_SIZE_MISMATCH");
  if (Number(row.uploaded) === 1) return json({ ok: true, runId, sha256, idempotent: true });
  const object = await env.DATA.put(String(row.blob_key), request.body, {
    sha256: bytesFromHex(sha256),
    customMetadata: { sha256, runId },
    httpMetadata: { contentType: "application/octet-stream", cacheControl: "public, max-age=31536000, immutable" },
  });
  if (!object || object.size !== length) throw new Error("R2_UPLOAD_VERIFY_FAILED");
  await env.DB.prepare(`
    UPDATE release_files SET uploaded = 1 WHERE run_id = ?1 AND candidate_sha256 = ?2
  `).bind(runId, sha256).run();
  return json({ ok: true, runId, sha256, bytes: length }, 201);
}

async function uploadFilePart(request, env, runId, sha256, partNumber, partSha256) {
  if (!HEX64.test(sha256) || !HEX64.test(partSha256)) throw new Error("RELEASE_HASH_INVALID");
  const row = await env.DB.prepare(`
    SELECT bytes,upload_mode,expected_parts,parts_json FROM release_files
    WHERE run_id=?1 AND candidate_sha256=?2 AND operation='upsert'
  `).bind(runId, sha256).first();
  if (!row || String(row.upload_mode) !== "multipart") throw new Error("RELEASE_FILE_UNDECLARED");
  let parts;
  try {
    parts = JSON.parse(String(row.parts_json || "[]"));
  } catch {
    throw new Error("RELEASE_MULTIPART_INVALID");
  }
  const expected = parts.find((part) => Number(part.number) === partNumber);
  if (!expected || expected.sha256 !== partSha256) throw new Error("RELEASE_MULTIPART_PART_UNDECLARED");
  const length = Number(request.headers.get("content-length"));
  if (!Number.isSafeInteger(length) || length !== Number(expected.bytes)) throw new Error("RELEASE_SIZE_MISMATCH");
  const existing = await env.DB.prepare(`
    SELECT part_sha256,bytes,part_key FROM release_file_parts
    WHERE run_id=?1 AND candidate_sha256=?2 AND part_number=?3
  `).bind(runId, sha256, partNumber).first();
  if (existing) {
    if (existing.part_sha256 !== partSha256 || Number(existing.bytes) !== length) {
      throw new Error("RELEASE_MULTIPART_PART_MISMATCH");
    }
    const object = await env.DATA.head(String(existing.part_key));
    if (!object || object.size !== length || object.customMetadata?.sha256 !== partSha256) {
      throw new Error("R2_MULTIPART_PART_VERIFY_FAILED");
    }
    return json({ ok: true, runId, sha256, partNumber, idempotent: true });
  }
  const partKey = multipartPartKey(runId, sha256, expected);
  const object = await env.DATA.put(partKey, request.body, {
    sha256: bytesFromHex(partSha256),
    customMetadata: { sha256: partSha256, logicalSha256: sha256, runId, partNumber: String(partNumber) },
    httpMetadata: { contentType: "application/octet-stream", cacheControl: "no-store" },
  });
  if (!object || object.size !== length) throw new Error("R2_MULTIPART_PART_VERIFY_FAILED");
  await env.DB.prepare(`
    INSERT INTO release_file_parts(
      run_id,candidate_sha256,part_number,part_sha256,bytes,part_key,uploaded_at
    ) VALUES (?1,?2,?3,?4,?5,?6,?7)
  `).bind(runId, sha256, partNumber, partSha256, length, partKey, nowIso()).run();
  return json({ ok: true, runId, sha256, partNumber, bytes: length }, 201);
}

async function commitRelease(env, runId) {
  const release = await env.DB.prepare(`
    SELECT status,expected_files,expected_bytes,expected_upserts,expected_chunks,
      expected_multipart_files,expected_upload_parts,registered_files,registered_bytes,
      registered_chunks,registered_multipart_files,registered_upload_parts
    FROM releases WHERE run_id = ?1
  `).bind(runId).first();
  if (!release) throw new Error("RELEASE_NOT_FOUND");
  if (["assembling", "queued", "processing", "complete", "conflict"].includes(String(release.status))) {
    return json({ ok: true, runId, status: release.status, idempotent: true });
  }
  if (
    Number(release.registered_files) !== Number(release.expected_files)
    || Number(release.registered_bytes) !== Number(release.expected_bytes)
    || Number(release.registered_chunks) !== Number(release.expected_chunks)
    || Number(release.registered_multipart_files) !== Number(release.expected_multipart_files)
    || Number(release.registered_upload_parts) !== Number(release.expected_upload_parts)
  ) throw new Error("RELEASE_REGISTRATION_INCOMPLETE");
  const registered = await env.DB.prepare(`
    SELECT COUNT(*) AS files,
      COALESCE(SUM(bytes),0) AS bytes,
      COALESCE(SUM(CASE WHEN operation='upsert' THEN 1 ELSE 0 END),0) AS upserts
    FROM release_files WHERE run_id=?1
  `).bind(runId).first();
  if (
    Number(registered?.files) !== Number(release.expected_files)
    || Number(registered?.bytes) !== Number(release.expected_bytes)
    || Number(registered?.upserts) !== Number(release.expected_upserts)
  ) throw new Error("RELEASE_REGISTRATION_MISMATCH");
  const parts = await env.DB.prepare(`
    SELECT COUNT(*) AS uploaded FROM release_file_parts WHERE run_id=?1
  `).bind(runId).first();
  const expectedParts = await env.DB.prepare(`
    SELECT COALESCE(SUM(expected_parts),0) AS expected FROM (
      SELECT candidate_sha256,MAX(expected_parts) AS expected_parts
      FROM release_files WHERE run_id=?1 AND upload_mode='multipart'
      GROUP BY candidate_sha256
    )
  `).bind(runId).first();
  if (Number(parts?.uploaded || 0) !== Number(expectedParts?.expected || 0)) {
    throw new Error("RELEASE_MULTIPART_UPLOAD_INCOMPLETE");
  }
  const missing = await env.DB.prepare(`
    SELECT COUNT(*) AS count FROM release_files WHERE run_id = ?1 AND uploaded = 0
  `).bind(runId).first();
  const unassembled = await env.DB.prepare(`
    SELECT COUNT(*) AS count FROM release_files
    WHERE run_id=?1 AND upload_mode='multipart' AND uploaded=0
  `).bind(runId).first();
  const assembling = Number(unassembled?.count || 0) > 0;
  if (!assembling && Number(missing?.count || 0) !== 0) throw new Error("RELEASE_UPLOAD_INCOMPLETE");
  const status = assembling ? "assembling" : "queued";
  await env.DB.prepare("UPDATE releases SET status=?2,updated_at=?3 WHERE run_id=?1")
    .bind(runId, status, nowIso()).run();
  await env.RELEASE_QUEUE.send({ schemaVersion: 2, runId, phase: assembling ? "assemble" : "merge" });
  return json({ ok: true, runId, status }, 202);
}

async function receipt(env, runId) {
  const release = await env.DB.prepare(`
    SELECT run_id,status,expected_files,expected_bytes,expected_chunks,registered_files,
      registered_bytes,registered_chunks,snapshot_id,error_code,error_detail,receipt_key,created_at,updated_at
    FROM releases WHERE run_id=?1
  `).bind(runId).first();
  if (!release) throw new Error("RELEASE_NOT_FOUND");
  return json({ ok: true, mode: env.SERVICE_MODE, ...release });
}

async function quotaStatus(env) {
  const daily = await env.DB.prepare("SELECT * FROM quota_daily WHERE day=?1").bind(dayKey()).first();
  const monthly = await env.DB.prepare("SELECT * FROM quota_monthly WHERE month=?1").bind(monthKey()).first();
  return json({ ok: true, mode: env.SERVICE_MODE, daily: daily || {}, monthly: monthly || {} });
}

async function listHeads(env, url) {
  const after = String(url.searchParams.get("after") || "");
  const requested = Number(url.searchParams.get("limit") || 200);
  if (after.length > 512 || !Number.isSafeInteger(requested) || requested < 1 || requested > 200) {
    throw new Error("RELEASE_HEAD_CURSOR_INVALID");
  }
  await takeD1ReadBudget(env, requested + 1);
  const rows = (await env.DB.prepare(`
    SELECT path,sha256,deleted,snapshot_id,updated_at FROM path_heads
    WHERE path>?1 ORDER BY path LIMIT ?2
  `).bind(after, requested).all()).results || [];
  return json({
    ok: true,
    mode: env.SERVICE_MODE,
    heads: rows,
    nextAfter: rows.length === requested ? rows[rows.length - 1].path : null,
  });
}

async function handleFetch(request, env) {
  const url = new URL(request.url);
  const uploadMatch = url.pathname.match(/^\/v1\/releases\/([0-9]{8}-[0-9]{6}-[0-9a-f]{8})\/files\/([0-9a-f]{64})$/);
  const partMatch = url.pathname.match(/^\/v1\/releases\/([0-9]{8}-[0-9]{6}-[0-9a-f]{8})\/files\/([0-9a-f]{64})\/parts\/([0-9]+)\/([0-9a-f]{64})$/);
  const chunkMatch = url.pathname.match(/^\/v1\/releases\/([0-9]{8}-[0-9]{6}-[0-9a-f]{8})\/chunks\/([0-9]+)$/);
  const commitMatch = url.pathname.match(/^\/v1\/releases\/([0-9]{8}-[0-9]{6}-[0-9a-f]{8})\/commit$/);
  const receiptMatch = url.pathname.match(/^\/v1\/releases\/([0-9]{8}-[0-9]{6}-[0-9a-f]{8})$/);
  try {
    if (request.method === "PUT" && partMatch) {
      await verifySignature(request, env, new Uint8Array(), partMatch[4]);
      await takeRequestBudget(env);
      return await uploadFilePart(request, env, partMatch[1], partMatch[2], Number(partMatch[3]), partMatch[4]);
    }
    if (request.method === "PUT" && uploadMatch) {
      await verifySignature(request, env, new Uint8Array(), uploadMatch[2]);
      await takeRequestBudget(env);
      return await uploadFile(request, env, uploadMatch[1], uploadMatch[2]);
    }
    const rawBody = request.method === "GET" ? new Uint8Array() : new Uint8Array(await request.arrayBuffer());
    await verifySignature(request, env, rawBody);
    await takeRequestBudget(env);
    if (request.method === "POST" && url.pathname === "/v1/releases") return await registerRelease(request, env, rawBody);
    if (request.method === "POST" && chunkMatch) return await registerChunk(env, chunkMatch[1], Number(chunkMatch[2]), rawBody);
    if (request.method === "POST" && commitMatch) return await commitRelease(env, commitMatch[1]);
    if (request.method === "GET" && receiptMatch) return await receipt(env, receiptMatch[1]);
    if (request.method === "GET" && url.pathname === "/v1/quota") return await quotaStatus(env);
    if (request.method === "GET" && url.pathname === "/v1/heads") return await listHeads(env, url);
    return json({ ok: false, error: "NOT_FOUND" }, 404);
  } catch (error) {
    const code = errorCode(error);
    // Keep authenticated payloads and secrets out of logs, but retain the
    // provider error message needed to diagnose shadow D1/R2 failures.
    console.error("ingest request failed", { code, message: String(error?.message || error).slice(0, 1000) });
    const status = code.startsWith("AUTH_") ? 401
      : code === "RELEASE_NOT_FOUND" ? 404
      : code.startsWith("FREE_TIER_") ? 429
      : code === "INTERNAL_ERROR" ? 500
      : 400;
    return json({ ok: false, error: code }, status);
  }
}

async function writeTerminalReceipt(env, runId, status, details) {
  const key = `ingest/receipts/${runId}.json`;
  const payload = { schemaVersion: 1, mode: env.SERVICE_MODE, runId, status, ...details, recordedAt: nowIso() };
  await env.DATA.put(key, JSON.stringify(payload), {
    httpMetadata: { contentType: "application/json", cacheControl: "no-store" },
  });
  return { key, payload };
}

async function recordConflict(env, runId, conflicts) {
  const unique = [...new Set(conflicts)].sort().slice(0, 32);
  const terminal = await writeTerminalReceipt(env, runId, "conflict", {
    errorCode: "RELEASE_BASE_CONFLICT",
    conflicts: unique,
  });
  await env.DB.prepare(`
    UPDATE releases SET status='conflict',error_code='RELEASE_BASE_CONFLICT',error_detail=?2,
      receipt_key=?3,updated_at=?4 WHERE run_id=?1
  `).bind(runId, JSON.stringify(unique), terminal.key, nowIso()).run();
}

async function finalizeRelease(env, runId) {
  const release = await env.DB.prepare("SELECT * FROM releases WHERE run_id=?1").bind(runId).first();
  if (!release || ["complete", "conflict"].includes(String(release.status))) return;
  const rows = (await env.DB.prepare("SELECT * FROM release_files WHERE run_id=?1 ORDER BY path").bind(runId).all()).results || [];
  if (rows.length !== Number(release.expected_files) || rows.some((row) => !row.decision)) {
    throw new Error("RELEASE_MERGE_INCOMPLETE");
  }
  const decisions = rows;
  const conflicts = decisions.filter((item) => item.decision === "conflict").map((item) => item.path);
  const bootstrapped = decisions.filter((item) => Number(item.bootstrapped) === 1).length;
  if (conflicts.length) {
    return await recordConflict(env, runId, conflicts);
  }
  const state = await env.DB.prepare("SELECT value FROM service_state WHERE key='current_snapshot'").first();
  const parentSnapshotId = state?.value || null;
  const snapshotSeed = JSON.stringify({ parentSnapshotId, runId, files: decisions.map((item) => [item.path, item.candidate_sha256, item.operation]) });
  const snapshotId = `${nowIso().replace(/[-:.]/g, "").slice(0, 15)}Z-${(await sha256Hex(new TextEncoder().encode(snapshotSeed))).slice(0, 12)}`;
  const manifestKey = `ingest/snapshots/${snapshotId}/manifest.json`;
  const receiptKey = `ingest/receipts/${runId}.json`;
  const snapshotManifest = {
    schemaVersion: 1,
    mode: env.SERVICE_MODE,
    snapshotId,
    parentSnapshotId,
    runId,
    changes: decisions.map((item) => ({
      path: item.path,
      operation: item.operation,
      sha256: item.candidate_sha256,
      bytes: Number(item.bytes),
      blobKey: item.blob_key,
      decision: item.decision,
    })),
    createdAt: nowIso(),
  };
  await env.DATA.put(manifestKey, JSON.stringify(snapshotManifest), {
    httpMetadata: { contentType: "application/json", cacheControl: "public, max-age=31536000, immutable" },
  });
  const receiptPayload = {
    schemaVersion: 1,
    mode: env.SERVICE_MODE,
    runId,
    status: "complete",
    snapshotId,
    parentSnapshotId,
    manifestKey,
    files: decisions.length,
    applied: decisions.filter((item) => ["apply", "delete"].includes(item.decision)).length,
    idempotent: decisions.filter((item) => item.decision === "idempotent").length,
    skipped: decisions.filter((item) => item.decision === "skip").length,
    bootstrapped,
    recordedAt: nowIso(),
  };
  await env.DATA.put(receiptKey, JSON.stringify(receiptPayload), {
    httpMetadata: { contentType: "application/json", cacheControl: "no-store" },
  });
  const timestamp = nowIso();
  await env.DB.batch([
    // All path heads are derived from staged decisions in one SQL statement.
    // Together with the pointer/snapshot/release rows below, D1 applies this
    // batch transactionally, so no registration or merge chunk is visible as
    // a partial snapshot.
    env.DB.prepare(`
      INSERT INTO path_heads(path,sha256,deleted,snapshot_id,updated_at)
      SELECT path,candidate_sha256,CASE WHEN operation='delete' THEN 1 ELSE 0 END,?2,?3
      FROM release_files
      WHERE run_id=?1 AND (decision IN ('apply','delete') OR bootstrapped=1)
      ON CONFLICT(path) DO UPDATE SET
        sha256=excluded.sha256,deleted=excluded.deleted,
        snapshot_id=excluded.snapshot_id,updated_at=excluded.updated_at
    `).bind(runId, snapshotId, timestamp),
    env.DB.prepare(`
      INSERT INTO snapshots(snapshot_id,parent_snapshot_id,run_id,manifest_key,receipt_key,created_at)
      VALUES (?1,?2,?3,?4,?5,?6)
    `).bind(snapshotId, parentSnapshotId, runId, manifestKey, receiptKey, timestamp),
    env.DB.prepare(`
      INSERT INTO service_state(key,value) VALUES ('current_snapshot',?1)
      ON CONFLICT(key) DO UPDATE SET value=excluded.value
    `).bind(snapshotId),
    env.DB.prepare(`
      UPDATE releases SET status='complete',snapshot_id=?2,receipt_key=?3,updated_at=?4 WHERE run_id=?1
    `).bind(runId, snapshotId, receiptKey, timestamp),
  ]);
}

async function processAssemble(env, runId) {
  const release = await env.DB.prepare("SELECT status FROM releases WHERE run_id=?1").bind(runId).first();
  if (!release || ["complete", "conflict"].includes(String(release.status))) return;
  const row = await env.DB.prepare(`
    SELECT * FROM release_files
    WHERE run_id=?1 AND upload_mode='multipart' AND uploaded=0
    ORDER BY path LIMIT 1
  `).bind(runId).first();
  if (!row) {
    const missing = await env.DB.prepare(`
      SELECT COUNT(*) AS count FROM release_files WHERE run_id=?1 AND uploaded=0
    `).bind(runId).first();
    if (Number(missing?.count || 0) !== 0) throw new Error("RELEASE_UPLOAD_INCOMPLETE");
    await env.DB.prepare("UPDATE releases SET status='queued',updated_at=?2 WHERE run_id=?1")
      .bind(runId, nowIso()).run();
    await env.RELEASE_QUEUE.send({ schemaVersion: 2, runId, phase: "merge" });
    return;
  }
  let parts;
  try {
    parts = JSON.parse(String(row.parts_json || "[]"));
  } catch {
    throw new Error("RELEASE_MULTIPART_INVALID");
  }
  const existing = await env.DATA.head(String(row.blob_key));
  if (!existing || existing.size !== Number(row.bytes) || existing.customMetadata?.sha256 !== row.candidate_sha256) {
    const upload = await env.DATA.createMultipartUpload(String(row.blob_key), {
      customMetadata: { sha256: row.candidate_sha256, runId },
      httpMetadata: { contentType: "application/octet-stream", cacheControl: "public, max-age=31536000, immutable" },
    });
    const uploadedParts = [];
    try {
      for (const part of parts) {
        const recorded = await env.DB.prepare(`
          SELECT part_key,part_sha256,bytes FROM release_file_parts
          WHERE run_id=?1 AND candidate_sha256=?2 AND part_number=?3
        `).bind(runId, row.candidate_sha256, part.number).first();
        if (!recorded || recorded.part_sha256 !== part.sha256 || Number(recorded.bytes) !== Number(part.bytes)) {
          throw new Error("RELEASE_MULTIPART_UPLOAD_INCOMPLETE");
        }
        const object = await env.DATA.get(String(recorded.part_key));
        if (!object || object.size !== Number(part.bytes) || object.customMetadata?.sha256 !== part.sha256) {
          throw new Error("R2_MULTIPART_PART_VERIFY_FAILED");
        }
        uploadedParts.push(await upload.uploadPart(Number(part.number), object.body));
      }
      await upload.complete(uploadedParts);
    } catch (error) {
      try { await upload.abort(); } catch { /* best effort; R2 lifecycle also aborts stale uploads */ }
      throw error;
    }
  }
  const completed = await env.DATA.head(String(row.blob_key));
  if (!completed || completed.size !== Number(row.bytes) || completed.customMetadata?.sha256 !== row.candidate_sha256) {
    throw new Error("R2_MULTIPART_ASSEMBLY_VERIFY_FAILED");
  }
  const recordedParts = (await env.DB.prepare(`
    SELECT part_key FROM release_file_parts WHERE run_id=?1 AND candidate_sha256=?2
  `).bind(runId, row.candidate_sha256).all()).results || [];
  await env.DB.prepare(`
    UPDATE release_files SET uploaded=1 WHERE run_id=?1 AND candidate_sha256=?2
  `).bind(runId, row.candidate_sha256).run();
  if (recordedParts.length) await env.DATA.delete(recordedParts.map((part) => String(part.part_key)));
  await env.RELEASE_QUEUE.send({ schemaVersion: 2, runId, phase: "assemble" });
}

async function processMergeChunk(env, runId) {
  const release = await env.DB.prepare("SELECT status FROM releases WHERE run_id=?1").bind(runId).first();
  if (!release || ["complete", "conflict"].includes(String(release.status))) return;
  await env.DB.prepare("UPDATE releases SET status='processing',updated_at=?2 WHERE run_id=?1")
    .bind(runId, nowIso()).run();
  const limit = numericBudget(env, "MAX_MERGE_CHUNK_FILES");
  const rows = (await env.DB.prepare(`
    SELECT * FROM release_files WHERE run_id=?1 AND decision IS NULL ORDER BY path LIMIT ?2
  `).bind(runId, limit).all()).results || [];
  if (!rows.length) return await finalizeRelease(env, runId);
  const decisions = [];
  const conflicts = [];
  for (const row of rows) {
    if (row.operation === "upsert") {
      const object = await env.DATA.head(String(row.blob_key));
      if (!object || object.size !== Number(row.bytes) || object.customMetadata?.sha256 !== row.candidate_sha256) {
        throw new Error(`R2_OBJECT_VERIFY_FAILED:${row.path}`);
      }
    }
    const head = await env.DB.prepare("SELECT sha256,deleted FROM path_heads WHERE path=?1").bind(row.path).first();
    let current = head ? (Number(head.deleted) ? null : head.sha256) : null;
    const base = row.base_sha256;
    const bootstrapped = !head && base != null;
    if (bootstrapped) current = base;
    const decision = threeWayDecision(base, current, row.candidate_sha256, row.operation);
    decisions.push({ ...row, current, decision, bootstrapped });
    if (decision === "conflict") conflicts.push(row.path);
  }
  await env.DB.batch(decisions.map((item) => env.DB.prepare(`
    UPDATE release_files SET decision=?3,current_sha256=?4,bootstrapped=?5 WHERE run_id=?1 AND path=?2
  `).bind(runId, item.path, item.decision, item.current, item.bootstrapped ? 1 : 0)));
  if (conflicts.length) return await recordConflict(env, runId, conflicts);
  await env.RELEASE_QUEUE.send({ schemaVersion: 2, runId, phase: "merge" });
}

export default {
  fetch: handleFetch,
  async queue(batch, env) {
    for (const message of batch.messages) {
      const runId = String(message.body?.runId || "");
      const phase = String(message.body?.phase || "merge");
      try {
        if (phase === "assemble") await processAssemble(env, runId);
        else await processMergeChunk(env, runId);
        message.ack();
      } catch (error) {
        const code = errorCode(error);
        const attempts = Number(message.attempts || 1);
        const terminal = attempts >= 4;
        await env.DB.prepare(`
          UPDATE releases SET status=?2,error_code=?3,error_detail=?4,updated_at=?5 WHERE run_id=?1
        `).bind(
          runId,
          terminal ? "failed" : (phase === "assemble" ? "assembling" : "queued"),
          code,
          String(error?.message || error).slice(0, 1000),
          nowIso(),
        ).run();
        if (terminal) message.ack();
        else message.retry();
      }
    }
  },
};
