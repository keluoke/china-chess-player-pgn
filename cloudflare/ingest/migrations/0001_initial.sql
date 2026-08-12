CREATE TABLE IF NOT EXISTS releases (
  run_id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  command TEXT NOT NULL,
  base_commit TEXT,
  source_json TEXT NOT NULL,
  expected_files INTEGER NOT NULL,
  expected_bytes INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  snapshot_id TEXT,
  error_code TEXT,
  error_detail TEXT,
  receipt_key TEXT
);

CREATE TABLE IF NOT EXISTS release_files (
  run_id TEXT NOT NULL,
  path TEXT NOT NULL,
  operation TEXT NOT NULL,
  candidate_sha256 TEXT,
  base_sha256 TEXT,
  bytes INTEGER NOT NULL,
  blob_key TEXT,
  uploaded INTEGER NOT NULL DEFAULT 0,
  decision TEXT,
  current_sha256 TEXT,
  PRIMARY KEY (run_id, path),
  FOREIGN KEY (run_id) REFERENCES releases(run_id)
);

CREATE TABLE IF NOT EXISTS path_heads (
  path TEXT PRIMARY KEY,
  sha256 TEXT,
  deleted INTEGER NOT NULL DEFAULT 0,
  snapshot_id TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshots (
  snapshot_id TEXT PRIMARY KEY,
  parent_snapshot_id TEXT,
  run_id TEXT NOT NULL UNIQUE,
  manifest_key TEXT NOT NULL,
  receipt_key TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS service_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS used_nonces (
  nonce TEXT PRIMARY KEY,
  seen_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS quota_daily (
  day TEXT PRIMARY KEY,
  releases INTEGER NOT NULL DEFAULT 0,
  worker_requests INTEGER NOT NULL DEFAULT 0,
  d1_rows_read INTEGER NOT NULL DEFAULT 0,
  d1_rows_written INTEGER NOT NULL DEFAULT 0,
  queue_ops INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS quota_monthly (
  month TEXT PRIMARY KEY,
  r2_class_a INTEGER NOT NULL DEFAULT 0,
  r2_class_b INTEGER NOT NULL DEFAULT 0,
  storage_reserved_bytes INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS release_files_status_idx
  ON release_files(run_id, uploaded);
CREATE INDEX IF NOT EXISTS releases_status_idx
  ON releases(status, created_at);
