import assert from "node:assert/strict";
import test from "node:test";

import {
  blobKey,
  canonicalRequest,
  chunkFingerprintText,
  estimateReservation,
  isAllowedReleasePath,
  normalizeManifest,
  normalizeReleaseChunk,
  normalizeReleaseHeader,
  threeWayDecision,
} from "../src/policy.js";

const env = {
  SERVICE_MODE: "shadow",
  MAX_RELEASE_FILES: "384",
  MAX_REGISTER_CHUNK_FILES: "10",
  MAX_MERGE_CHUNK_FILES: "10",
  MAX_RELEASE_BYTES: String(64 * 1024 * 1024),
  MAX_FILE_BYTES: String(16 * 1024 * 1024),
};

test("release paths preserve machine/manual boundary", () => {
  assert.equal(isAllowedReleasePath("data/generated/chess-results-event-details/tnr12345.json"), true);
  assert.equal(isAllowedReleasePath("docs/data/registry/shards/00.json"), true);
  assert.equal(isAllowedReleasePath("data/manual/event.csv"), false);
  assert.equal(isAllowedReleasePath("data/community/name-corrections.csv"), false);
  assert.equal(isAllowedReleasePath("data/generated/chess-results-event-details/raw.html"), false);
  assert.equal(isAllowedReleasePath("../data/generated/chess-results-event-details/x.json"), false);
});

test("manifest normalization applies free-tier limits", () => {
  const hash = "a".repeat(64);
  const manifest = normalizeManifest({
    schemaVersion: 1,
    runId: "20260812-120000-deadbeef",
    command: "event-queue",
    baseCommit: "b".repeat(40),
    source: { source: "Chess-Results", releasePolicy: "full-data" },
    files: [{
      path: "data/generated/chess-results-event-details/tnr12345.json",
      operation: "upsert",
      sha256: hash,
      baseSha256: null,
      bytes: 12,
    }],
  }, env);
  assert.equal(manifest.totalBytes, 12);
  assert.equal(manifest.files[0].blobKey, blobKey(hash));
  assert.equal(estimateReservation(manifest).storageReservedBytes, 262156);
  assert.throws(() => normalizeManifest({ ...manifest, files: [{ ...manifest.files[0], bytes: 17 * 1024 * 1024 }] }, env), /FREE_TIER_RELEASE_OBJECT_LIMIT/);
  assert.doesNotThrow(() => normalizeManifest({
    ...manifest,
    files: Array.from({ length: 13 }, (_, index) => ({
      ...manifest.files[0],
      path: `data/generated/chess-results-event-details/tnr${10000 + index}.json`,
    })),
  }, env));
  assert.throws(() => normalizeManifest({
    ...manifest,
    files: Array.from({ length: 385 }, (_, index) => ({
      ...manifest.files[0],
      path: `data/generated/chess-results-event-details/tnr${10000 + index}.json`,
    })),
  }, env), /FREE_TIER_RELEASE_FILE_LIMIT/);
});

test("large logical release uses bounded registration and merge chunks", () => {
  const chunkFiles = Array.from({ length: 10 }, (_, index) => ({
    path: `data/generated/chess-results-event-details/tnr${10000 + index}.json`,
    operation: "upsert",
    sha256: "a".repeat(64),
    baseSha256: null,
    bytes: 1,
  }));
  const header = normalizeReleaseHeader({
    schemaVersion: 2,
    runId: "20260812-120000-deadbeef",
    command: "event-queue",
    baseCommit: "b".repeat(40),
    source: { source: "Chess-Results", releasePolicy: "full-data" },
    manifestSha256: "c".repeat(64),
    expectedFiles: 50,
    expectedBytes: 50,
    expectedUpserts: 50,
    expectedChunks: 5,
    chunkSha256s: Array.from({ length: 5 }, () => "d".repeat(64)),
  }, env);
  assert.equal(header.expectedFiles, 50);
  assert.equal(estimateReservation(header, env).queueOps, 18);
  assert.equal(estimateReservation(header, env).d1RowsWritten, 620);
  assert.equal(estimateReservation(header, env).d1StorageReservedBytes, 275456);
  const release = {
    source_json: JSON.stringify(header.source),
    manifest_sha256: header.manifestSha256,
    expected_chunks: header.expectedChunks,
    chunk_hashes_json: JSON.stringify(header.chunkSha256s),
  };
  const chunk = normalizeReleaseChunk({
    schemaVersion: 1,
    manifestSha256: header.manifestSha256,
    chunkSha256: header.chunkSha256s[0],
    chunkIndex: 0,
    files: chunkFiles,
  }, release, env);
  assert.equal(chunk.files.length, 10);
  assert.equal(chunkFingerprintText(chunk.files), JSON.stringify(chunkFiles.map((item) => [
    item.path, item.operation, item.sha256, item.baseSha256, item.bytes,
  ])));
  assert.throws(() => normalizeReleaseChunk({
    schemaVersion: 1,
    manifestSha256: header.manifestSha256,
    chunkSha256: header.chunkSha256s[0],
    chunkIndex: 0,
    files: Array.from({ length: 11 }, (_, index) => ({
      path: `data/generated/chess-results-event-details/tnr${20000 + index}.json`,
      operation: "upsert",
      sha256: "a".repeat(64),
      baseSha256: null,
      bytes: 1,
    })),
  }, release, env), /FREE_TIER_RELEASE_FILE_LIMIT/);
});

test("source license and path coupling is enforced", () => {
  const item = {
    path: "data/generated/chess-results-event-details/tnr12345.json",
    operation: "upsert",
    sha256: "a".repeat(64),
    baseSha256: null,
    bytes: 12,
  };
  assert.throws(() => normalizeManifest({
    schemaVersion: 1,
    runId: "20260812-120000-deadbeef",
    source: { source: "Lichess Broadcasts", releasePolicy: "cc-by-sa-4.0" },
    files: [item],
  }, env), /RELEASE_LICENSE_MISSING/);
  assert.throws(() => normalizeManifest({
    schemaVersion: 1,
    runId: "20260812-120000-deadbeef",
    source: { source: "Chess-Results", releasePolicy: "full-data" },
    files: [{ ...item, path: "docs/data/registry/players.json" }],
  }, env), /RELEASE_SOURCE_PATH_MISMATCH/);
});

test("three-way merge is fail-closed", () => {
  assert.equal(threeWayDecision("base", "base", "new", "upsert"), "apply");
  assert.equal(threeWayDecision("base", "new", "new", "upsert"), "idempotent");
  assert.equal(threeWayDecision("base", "current", "base", "upsert"), "skip");
  assert.equal(threeWayDecision("base", "current", "new", "upsert"), "conflict");
  assert.equal(threeWayDecision("base", "base", null, "delete"), "delete");
});

test("canonical authentication input is stable", () => {
  assert.equal(
    canonicalRequest("post", "/v1/releases", 123, "f".repeat(32), "a".repeat(64)),
    `POST\n/v1/releases\n123\n${"f".repeat(32)}\n${"a".repeat(64)}`,
  );
});

test("chunk fingerprint is stable across Python client and Worker", async () => {
  const text = chunkFingerprintText([{
    path: "data/generated/event-completeness-report.json",
    operation: "upsert",
    sha256: "a".repeat(64),
    baseSha256: null,
    bytes: 12,
  }]);
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  const hex = [...new Uint8Array(digest)]
    .map((value) => value.toString(16).padStart(2, "0")).join("");
  assert.equal(hex, "c8fde42f02e3c321071ca0f1dfdb482da54456e459a93c81279de1e536712f91");
});
