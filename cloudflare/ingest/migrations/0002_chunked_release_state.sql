ALTER TABLE releases ADD COLUMN manifest_sha256 TEXT;
ALTER TABLE releases ADD COLUMN expected_upserts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE releases ADD COLUMN expected_chunks INTEGER NOT NULL DEFAULT 0;
ALTER TABLE releases ADD COLUMN registered_files INTEGER NOT NULL DEFAULT 0;
ALTER TABLE releases ADD COLUMN registered_bytes INTEGER NOT NULL DEFAULT 0;
ALTER TABLE releases ADD COLUMN registered_chunks INTEGER NOT NULL DEFAULT 0;
ALTER TABLE releases ADD COLUMN chunk_hashes_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE release_files ADD COLUMN bootstrapped INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS release_chunks (
  run_id TEXT NOT NULL,
  chunk_index INTEGER NOT NULL,
  chunk_sha256 TEXT NOT NULL,
  files INTEGER NOT NULL,
  bytes INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (run_id, chunk_index),
  FOREIGN KEY (run_id) REFERENCES releases(run_id)
);

CREATE INDEX IF NOT EXISTS release_files_merge_idx
  ON release_files(run_id, decision, path);
