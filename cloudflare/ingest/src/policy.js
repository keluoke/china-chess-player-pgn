export const HEX64 = /^[0-9a-f]{64}$/;
export const RUN_ID = /^[0-9]{8}-[0-9]{6}-[0-9a-f]{8}$/;

export const PUBLIC_RELEASE_PREFIXES = Object.freeze([
  "docs/data/registry/",
  "docs/data/bulk/",
  "data/generated/federation-snapshots/",
  "data/generated/transfer-candidates.json",
  "data/generated/chess-results-event-details/",
  "data/generated/chess-results-event-pgn/",
  "data/generated/pgn-source-attempts/",
  "docs/data/pgn/chess-results/",
  "data/generated/person-observations.csv",
  "data/generated/person-observations.meta.json",
  "data/generated/pgn-collection-status.json",
  "data/generated/event-completeness-report.json",
  "data/generated/pgn-supplement-queue.json",
  "data/generated/r2-object-receipts/events--chess-results.json",
]);

const FORBIDDEN_PREFIXES = Object.freeze([
  "data/community/",
  "data/manual/",
  "data/incoming/",
  "data/generated/chess-results-event-snapshots/",
]);

const RAW_SUFFIXES = Object.freeze([".html", ".html.gz", ".warc", ".warc.gz"]);

const CHESS_RESULTS_PREFIXES = Object.freeze([
  "data/generated/chess-results-event-details/",
  "data/generated/chess-results-event-pgn/",
  "data/generated/pgn-source-attempts/",
  "docs/data/pgn/chess-results/",
  "data/generated/person-observations.csv",
  "data/generated/person-observations.meta.json",
  "data/generated/pgn-collection-status.json",
  "data/generated/event-completeness-report.json",
  "data/generated/pgn-supplement-queue.json",
  "data/generated/r2-object-receipts/events--chess-results.json",
]);

function within(path, prefixes) {
  return prefixes.some((prefix) => path === prefix || path.startsWith(prefix));
}

function validateSource(source, files, mode) {
  const name = String(source?.source || "");
  const policy = String(source?.releasePolicy || "");
  if (name === "Chess-Results") {
    if (!['full-data', 'authorized'].includes(policy) || files.some((item) => !within(item.path, CHESS_RESULTS_PREFIXES))) {
      throw new Error("RELEASE_SOURCE_PATH_MISMATCH");
    }
    return;
  }
  if (name === "FIDE Rating List") {
    const prefixes = ["docs/data/registry/", "data/generated/federation-snapshots/", "data/generated/transfer-candidates.json"];
    if (policy !== "factual-registry-projection" || files.some((item) => !within(item.path, prefixes))) {
      throw new Error("RELEASE_SOURCE_PATH_MISMATCH");
    }
    return;
  }
  if (name === "Lichess Broadcasts") {
    if (
      policy !== "cc-by-sa-4.0"
      || source?.licenseURL !== "https://creativecommons.org/licenses/by-sa/4.0/"
      || !source?.attributionURL
      || files.some((item) => !within(item.path, ["docs/data/bulk/"]))
    ) throw new Error("RELEASE_LICENSE_MISSING");
    return;
  }
  if (name === "R2 Object Storage") {
    if (policy !== "verified-public-object-replication") throw new Error("RELEASE_SOURCE_METADATA_INVALID");
    return;
  }
  if (mode === "shadow" && name === "shadow-drill" && policy === "test-only") return;
  throw new Error("RELEASE_SOURCE_UNSUPPORTED");
}

function normalizeFiles(files, env, maxFiles) {
  const maxReleaseBytes = numericBudget(env, "MAX_RELEASE_BYTES");
  const maxFileBytes = numericBudget(env, "MAX_FILE_BYTES");
  const maxSingleUploadBytes = numericBudget(env, "MAX_SINGLE_UPLOAD_BYTES");
  const multipartPartBytes = numericBudget(env, "MULTIPART_PART_BYTES");
  const maxMultipartParts = numericBudget(env, "MAX_MULTIPART_PARTS");
  if (!Array.isArray(files) || files.length < 1 || files.length > maxFiles) {
    throw new Error("FREE_TIER_RELEASE_FILE_LIMIT");
  }
  const seen = new Set();
  let totalBytes = 0;
  const normalized = files.map((item) => {
    const path = String(item?.path || "");
    const operation = String(item?.operation || "");
    if (!isAllowedReleasePath(path) || seen.has(path)) throw new Error("RELEASE_PATH_FORBIDDEN");
    seen.add(path);
    if (!["upsert", "delete"].includes(operation)) throw new Error("RELEASE_OPERATION_INVALID");
    const baseSha256 = item.baseSha256 == null ? null : String(item.baseSha256);
    if (baseSha256 !== null && !HEX64.test(baseSha256)) throw new Error("RELEASE_BASE_HASH_INVALID");
    if (operation === "delete") {
      if (item.sha256 != null || Number(item.bytes || 0) !== 0) throw new Error("RELEASE_DELETE_INVALID");
      return { path, operation, sha256: null, baseSha256, bytes: 0, blobKey: null };
    }
    const sha256 = String(item.sha256 || "");
    const bytes = Number(item.bytes);
    if (!HEX64.test(sha256) || !Number.isSafeInteger(bytes) || bytes < 0 || bytes > maxFileBytes) {
      throw new Error("FREE_TIER_RELEASE_OBJECT_LIMIT");
    }
    let multipart = null;
    if (bytes > maxSingleUploadBytes) {
      const supplied = item.multipart;
      const parts = Array.isArray(supplied?.parts) ? supplied.parts : [];
      const expectedParts = Math.ceil(bytes / multipartPartBytes);
      if (
        Number(supplied?.partSize) !== multipartPartBytes
        || parts.length !== expectedParts
        || parts.length < 2
        || parts.length > maxMultipartParts
      ) throw new Error("RELEASE_MULTIPART_INVALID");
      let multipartBytes = 0;
      const normalizedParts = parts.map((part, index) => {
        const number = Number(part?.number);
        const partSha256 = String(part?.sha256 || "");
        const partBytes = Number(part?.bytes);
        const expectedBytes = index === parts.length - 1
          ? bytes - multipartPartBytes * index
          : multipartPartBytes;
        if (
          number !== index + 1
          || !HEX64.test(partSha256)
          || !Number.isSafeInteger(partBytes)
          || partBytes !== expectedBytes
          || (index < parts.length - 1 && partBytes < 5 * 1024 * 1024)
        ) throw new Error("RELEASE_MULTIPART_INVALID");
        multipartBytes += partBytes;
        return { number, sha256: partSha256, bytes: partBytes };
      });
      if (multipartBytes !== bytes) throw new Error("RELEASE_MULTIPART_INVALID");
      multipart = { partSize: multipartPartBytes, parts: normalizedParts };
    } else if (item.multipart != null) {
      throw new Error("RELEASE_MULTIPART_UNNECESSARY");
    }
    totalBytes += bytes;
    if (totalBytes > maxReleaseBytes) throw new Error("FREE_TIER_RELEASE_BYTE_LIMIT");
    return {
      path,
      operation,
      sha256,
      baseSha256,
      bytes,
      blobKey: blobKey(sha256),
      uploadMode: multipart ? "multipart" : "single",
      multipart,
      expectedParts: multipart?.parts.length || 0,
    };
  });
  return { files: normalized, totalBytes };
}

export function numericBudget(env, key) {
  const value = Number(env[key]);
  if (!Number.isSafeInteger(value) || value <= 0) {
    throw new Error(`BUDGET_CONFIG_INVALID:${key}`);
  }
  return value;
}

export function isAllowedReleasePath(path) {
  if (typeof path !== "string" || path.length < 1 || path.length > 512) return false;
  if (path.startsWith("/") || path.includes("\\") || path.split("/").includes("..")) return false;
  if (FORBIDDEN_PREFIXES.some((prefix) => path === prefix.slice(0, -1) || path.startsWith(prefix))) return false;
  if (RAW_SUFFIXES.some((suffix) => path.toLowerCase().endsWith(suffix))) return false;
  return PUBLIC_RELEASE_PREFIXES.some((prefix) => path === prefix || path.startsWith(prefix));
}

export function blobKey(sha256) {
  if (!HEX64.test(sha256)) throw new Error("RELEASE_HASH_INVALID");
  return `ingest/blobs/sha256/${sha256.slice(0, 2)}/${sha256}`;
}

export function normalizeManifest(payload, env) {
  if (!payload || payload.schemaVersion !== 1 || !RUN_ID.test(String(payload.runId || ""))) {
    throw new Error("RELEASE_MANIFEST_INVALID");
  }
  const maxFiles = numericBudget(env, "MAX_RELEASE_FILES");
  const { files: normalized, totalBytes } = normalizeFiles(payload.files, env, maxFiles);
  const baseCommit = payload.baseCommit == null ? null : String(payload.baseCommit);
  if (baseCommit !== null && !/^[0-9a-f]{40,64}$/.test(baseCommit)) {
    throw new Error("RELEASE_BASE_COMMIT_INVALID");
  }
  const source = payload.source && typeof payload.source === "object" ? payload.source : {};
  validateSource(source, normalized, String(env.SERVICE_MODE || "shadow"));
  return {
    schemaVersion: 1,
    runId: String(payload.runId),
    command: String(payload.command || "unknown").slice(0, 64),
    baseCommit,
    source,
    files: normalized,
    totalBytes,
  };
}

export function normalizeReleaseHeader(payload, env) {
  if (!payload || payload.schemaVersion !== 2 || !RUN_ID.test(String(payload.runId || ""))) {
    throw new Error("RELEASE_MANIFEST_INVALID");
  }
  const expectedFiles = Number(payload.expectedFiles);
  const expectedBytes = Number(payload.expectedBytes);
  const expectedUpserts = Number(payload.expectedUpserts);
  const expectedMultipartFiles = Number(payload.expectedMultipartFiles || 0);
  const expectedUploadParts = Number(payload.expectedUploadParts || 0);
  const expectedChunks = Number(payload.expectedChunks);
  const maxFiles = numericBudget(env, "MAX_RELEASE_FILES");
  const maxBytes = numericBudget(env, "MAX_RELEASE_BYTES");
  const chunkFiles = numericBudget(env, "MAX_REGISTER_CHUNK_FILES");
  if (!Number.isSafeInteger(expectedFiles) || expectedFiles < 1 || expectedFiles > maxFiles) {
    throw new Error("FREE_TIER_RELEASE_FILE_LIMIT");
  }
  if (!Number.isSafeInteger(expectedBytes) || expectedBytes < 0 || expectedBytes > maxBytes) {
    throw new Error("FREE_TIER_RELEASE_BYTE_LIMIT");
  }
  if (!Number.isSafeInteger(expectedUpserts) || expectedUpserts < 0 || expectedUpserts > expectedFiles) {
    throw new Error("RELEASE_MANIFEST_INVALID");
  }
  if (
    !Number.isSafeInteger(expectedMultipartFiles)
    || expectedMultipartFiles < 0
    || expectedMultipartFiles > expectedUpserts
    || !Number.isSafeInteger(expectedUploadParts)
    || expectedUploadParts < expectedMultipartFiles * 2
    || expectedUploadParts > expectedMultipartFiles * numericBudget(env, "MAX_MULTIPART_PARTS")
  ) throw new Error("RELEASE_MULTIPART_INVALID");
  if (!Number.isSafeInteger(expectedChunks) || expectedChunks !== Math.ceil(expectedFiles / chunkFiles)) {
    throw new Error("RELEASE_CHUNK_COUNT_INVALID");
  }
  const manifestSha256 = String(payload.manifestSha256 || "");
  if (!HEX64.test(manifestSha256)) throw new Error("RELEASE_HASH_INVALID");
  const chunkSha256s = Array.isArray(payload.chunkSha256s)
    ? payload.chunkSha256s.map((item) => String(item || ""))
    : [];
  if (chunkSha256s.length !== expectedChunks || chunkSha256s.some((item) => !HEX64.test(item))) {
    throw new Error("RELEASE_CHUNK_HASH_INVALID");
  }
  const baseCommit = payload.baseCommit == null ? null : String(payload.baseCommit);
  if (baseCommit !== null && !/^[0-9a-f]{40,64}$/.test(baseCommit)) {
    throw new Error("RELEASE_BASE_COMMIT_INVALID");
  }
  const source = payload.source && typeof payload.source === "object" ? payload.source : {};
  validateSource(source, [], String(env.SERVICE_MODE || "shadow"));
  return {
    schemaVersion: 2,
    runId: String(payload.runId),
    command: String(payload.command || "unknown").slice(0, 64),
    baseCommit,
    source,
    manifestSha256,
    expectedFiles,
    expectedBytes,
    expectedUpserts,
    expectedMultipartFiles,
    expectedUploadParts,
    expectedChunks,
    chunkSha256s,
  };
}

export function chunkFingerprintText(files) {
  return JSON.stringify(files.map((item) => [
    item.path,
    item.operation,
    item.sha256,
    item.baseSha256,
    item.bytes,
    item.multipart ? [
      item.multipart.partSize,
      item.multipart.parts.map((part) => [part.number, part.sha256, part.bytes]),
    ] : null,
  ]));
}

export function normalizeReleaseChunk(payload, release, env) {
  if (!payload || payload.schemaVersion !== 1) throw new Error("RELEASE_CHUNK_INVALID");
  const manifestSha256 = String(payload.manifestSha256 || "");
  if (!HEX64.test(manifestSha256) || manifestSha256 !== String(release.manifest_sha256 || "")) {
    throw new Error("RELEASE_MANIFEST_HASH_MISMATCH");
  }
  const chunkIndex = Number(payload.chunkIndex);
  if (!Number.isSafeInteger(chunkIndex) || chunkIndex < 0 || chunkIndex >= Number(release.expected_chunks)) {
    throw new Error("RELEASE_CHUNK_INDEX_INVALID");
  }
  const maxChunkFiles = numericBudget(env, "MAX_REGISTER_CHUNK_FILES");
  const { files, totalBytes } = normalizeFiles(payload.files, env, maxChunkFiles);
  const chunkSha256 = String(payload.chunkSha256 || "");
  let expectedHashes;
  try {
    expectedHashes = JSON.parse(String(release.chunk_hashes_json || "[]"));
  } catch {
    throw new Error("RELEASE_CHUNK_HASH_INVALID");
  }
  if (!HEX64.test(chunkSha256) || chunkSha256 !== expectedHashes[chunkIndex]) {
    throw new Error("RELEASE_CHUNK_HASH_MISMATCH");
  }
  const source = JSON.parse(String(release.source_json || "{}"));
  validateSource(source, files, String(env.SERVICE_MODE || "shadow"));
  return {
    schemaVersion: 1,
    manifestSha256,
    chunkSha256,
    chunkIndex,
    files,
    totalBytes,
    multipartFiles: files.filter((item) => item.uploadMode === "multipart").length,
    uploadParts: files.reduce((total, item) => total + item.expectedParts, 0),
  };
}

export function threeWayDecision(base, current, candidate, operation) {
  const normalizedCurrent = current ?? null;
  const normalizedBase = base ?? null;
  const normalizedCandidate = operation === "delete" ? null : candidate;
  if (normalizedCurrent === normalizedCandidate) return "idempotent";
  if (normalizedCurrent === normalizedBase) return operation === "delete" ? "delete" : "apply";
  if (normalizedCandidate === normalizedBase) return "skip";
  return "conflict";
}

export function estimateReservation(manifest, env = {}) {
  const count = manifest.expectedFiles ?? manifest.files.length;
  const upserts = manifest.expectedUpserts
    ?? manifest.files.filter((item) => item.operation === "upsert").length;
  const multipartFiles = manifest.expectedMultipartFiles
    ?? manifest.files.filter((item) => item.uploadMode === "multipart").length;
  const uploadParts = manifest.expectedUploadParts
    ?? manifest.files.reduce((total, item) => total + Number(item.expectedParts || 0), 0);
  const registerChunkFiles = Number(env.MAX_REGISTER_CHUNK_FILES || 10);
  const mergeChunkFiles = Number(env.MAX_MERGE_CHUNK_FILES || 10);
  const registrationChunks = manifest.expectedChunks ?? Math.ceil(count / registerChunkFiles);
  const queueMessages = Math.ceil(count / mergeChunkFiles)
    + (multipartFiles > 0 ? multipartFiles + 2 : 1);
  return {
    releases: 1,
    workerRequests: 0,
    d1RowsRead: count * 4 + 50,
    // Covers file registration, upload flags, staged decisions, path heads,
    // authenticated request nonce/budget rows, chunk rows and polling slack.
    d1RowsWritten: count * 6 + uploadParts * 3 + registrationChunks * 4 + 300,
    // D1 does not authorize page_count/page_size PRAGMAs from a Worker
    // binding. Reserve a deliberately high, never-refunded metadata estimate
    // so the service fails closed before the 128 MiB internal ceiling.
    d1StorageReservedBytes: count * 4096 + uploadParts * 1024 + registrationChunks * 1024 + 65536,
    queueOps: queueMessages * 3,
    r2ClassA: (upserts - multipartFiles) + uploadParts * 3 + multipartFiles * 2 + 2,
    r2ClassB: upserts + uploadParts,
    storageReservedBytes: (manifest.expectedBytes ?? manifest.totalBytes) + 262144,
  };
}

export function canonicalRequest(method, pathname, timestamp, nonce, bodyHash) {
  return [method.toUpperCase(), pathname, String(timestamp), nonce, bodyHash].join("\n");
}

export function bytesFromHex(hex) {
  if (!HEX64.test(hex)) throw new Error("RELEASE_HASH_INVALID");
  const output = new Uint8Array(hex.length / 2);
  for (let index = 0; index < hex.length; index += 2) {
    output[index / 2] = Number.parseInt(hex.slice(index, index + 2), 16);
  }
  return output;
}
