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
  MAX_RELEASE_BYTES: String(96 * 1024 * 1024),
  MAX_FILE_BYTES: String(96 * 1024 * 1024),
  MAX_SINGLE_UPLOAD_BYTES: String(16 * 1024 * 1024),
  MULTIPART_PART_BYTES: String(8 * 1024 * 1024),
  MAX_MULTIPART_PARTS: "12",
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
  assert.throws(() => normalizeManifest({ ...manifest, files: [{ ...manifest.files[0], bytes: 17 * 1024 * 1024 }] }, env), /RELEASE_MULTIPART_INVALID/);
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
    expectedMultipartFiles: 0,
    expectedUploadParts: 0,
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
    item.path, item.operation, item.sha256, item.baseSha256, item.bytes, null,
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

test("delete-only chunks reserve zero multipart parts", () => {
  const header = normalizeReleaseHeader({
    schemaVersion: 2,
    runId: "20260812-120000-deadbeef",
    command: "baseline-compensating-delete",
    baseCommit: "b".repeat(40),
    source: { source: "Chess-Results", releasePolicy: "full-data" },
    manifestSha256: "c".repeat(64),
    expectedFiles: 1,
    expectedBytes: 0,
    expectedUpserts: 0,
    expectedMultipartFiles: 0,
    expectedUploadParts: 0,
    expectedChunks: 1,
    chunkSha256s: ["d".repeat(64)],
  }, env);
  const release = {
    source_json: JSON.stringify(header.source),
    manifest_sha256: header.manifestSha256,
    expected_chunks: 1,
    chunk_hashes_json: JSON.stringify(header.chunkSha256s),
  };
  const chunk = normalizeReleaseChunk({
    schemaVersion: 1,
    manifestSha256: header.manifestSha256,
    chunkSha256: header.chunkSha256s[0],
    chunkIndex: 0,
    files: [{
      path: "data/generated/chess-results-event-details/tnr12345.json",
      operation: "delete",
      sha256: null,
      baseSha256: "a".repeat(64),
      bytes: 0,
    }],
  }, release, env);
  assert.equal(chunk.multipartFiles, 0);
  assert.equal(chunk.uploadParts, 0);
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

test("multipart logical files are part-bound and quota-reserved", () => {
  const partSize = 8 * 1024 * 1024;
  const totalBytes = 20 * 1024 * 1024;
  const parts = [partSize, partSize, 4 * 1024 * 1024].map((bytes, index) => ({
    number: index + 1,
    sha256: String(index + 1).repeat(64),
    bytes,
  }));
  const manifest = normalizeManifest({
    schemaVersion: 1,
    runId: "20260812-120000-deadbeef",
    command: "baseline-migrate",
    baseCommit: "b".repeat(40),
    source: { source: "Lichess Broadcasts", releasePolicy: "cc-by-sa-4.0", licenseURL: "https://creativecommons.org/licenses/by-sa/4.0/", attributionURL: "https://database.lichess.org/" },
    files: [{
      path: "docs/data/bulk/youth.pgn",
      operation: "upsert",
      sha256: "a".repeat(64),
      baseSha256: "a".repeat(64),
      bytes: totalBytes,
      multipart: { partSize, parts },
    }],
  }, { ...env, MAX_RELEASE_BYTES: String(96 * 1024 * 1024) });
  assert.equal(manifest.files[0].uploadMode, "multipart");
  assert.equal(manifest.files[0].expectedParts, 3);
  const reservation = estimateReservation(manifest, env);
  assert.equal(reservation.queueOps, 12);
  assert.equal(reservation.r2ClassA, 13);
  assert.equal(reservation.r2ClassB, 4);
  assert.throws(() => normalizeManifest({
    ...manifest,
    files: [{ ...manifest.files[0], multipart: { partSize, parts: parts.slice(0, 2) } }],
  }, { ...env, MAX_RELEASE_BYTES: String(96 * 1024 * 1024) }), /RELEASE_MULTIPART_INVALID/);
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
  assert.equal(hex, "97df5f9596b1f6cb87225d0e02ebb9a188e2dcf8caa2acf4513dfc8236fb7463");
});
