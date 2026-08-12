import assert from "node:assert/strict";
import test from "node:test";

import {
  blobKey,
  canonicalRequest,
  estimateReservation,
  isAllowedReleasePath,
  normalizeManifest,
  threeWayDecision,
} from "../src/policy.js";

const env = {
  SERVICE_MODE: "shadow",
  MAX_RELEASE_FILES: "12",
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
  assert.throws(() => normalizeManifest({
    ...manifest,
    files: Array.from({ length: 13 }, (_, index) => ({
      ...manifest.files[0],
      path: `data/generated/chess-results-event-details/tnr${10000 + index}.json`,
    })),
  }, env), /FREE_TIER_RELEASE_FILE_LIMIT/);
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
