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
  const files = payload.files;
  const maxFiles = numericBudget(env, "MAX_RELEASE_FILES");
  const maxReleaseBytes = numericBudget(env, "MAX_RELEASE_BYTES");
  const maxFileBytes = numericBudget(env, "MAX_FILE_BYTES");
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
    if (!['upsert', 'delete'].includes(operation)) throw new Error("RELEASE_OPERATION_INVALID");
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
    totalBytes += bytes;
    if (totalBytes > maxReleaseBytes) throw new Error("FREE_TIER_RELEASE_BYTE_LIMIT");
    return { path, operation, sha256, baseSha256, bytes, blobKey: blobKey(sha256) };
  });
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

export function threeWayDecision(base, current, candidate, operation) {
  const normalizedCurrent = current ?? null;
  const normalizedBase = base ?? null;
  const normalizedCandidate = operation === "delete" ? null : candidate;
  if (normalizedCurrent === normalizedCandidate) return "idempotent";
  if (normalizedCurrent === normalizedBase) return operation === "delete" ? "delete" : "apply";
  if (normalizedCandidate === normalizedBase) return "skip";
  return "conflict";
}

export function estimateReservation(manifest) {
  const upserts = manifest.files.filter((item) => item.operation === "upsert").length;
  const count = manifest.files.length;
  return {
    releases: 1,
    workerRequests: 0,
    d1RowsRead: count * 4 + 50,
    d1RowsWritten: count * 3 + 50,
    queueOps: 3,
    r2ClassA: upserts + 2,
    r2ClassB: upserts,
    storageReservedBytes: manifest.totalBytes + 262144,
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
