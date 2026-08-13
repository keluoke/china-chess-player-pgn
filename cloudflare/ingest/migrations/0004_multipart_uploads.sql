ALTER TABLE releases ADD COLUMN expected_multipart_files INTEGER NOT NULL DEFAULT 0;
ALTER TABLE releases ADD COLUMN expected_upload_parts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE releases ADD COLUMN registered_multipart_files INTEGER NOT NULL DEFAULT 0;
ALTER TABLE releases ADD COLUMN registered_upload_parts INTEGER NOT NULL DEFAULT 0;

ALTER TABLE release_files ADD COLUMN upload_mode TEXT NOT NULL DEFAULT 'single';
ALTER TABLE release_files ADD COLUMN expected_parts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE release_files ADD COLUMN parts_json TEXT NOT NULL DEFAULT '[]';

CREATE TABLE IF NOT EXISTS release_file_parts (
  run_id TEXT NOT NULL,
  candidate_sha256 TEXT NOT NULL,
  part_number INTEGER NOT NULL,
  part_sha256 TEXT NOT NULL,
  bytes INTEGER NOT NULL,
  part_key TEXT NOT NULL,
  uploaded_at TEXT NOT NULL,
  PRIMARY KEY (run_id, candidate_sha256, part_number),
  FOREIGN KEY (run_id) REFERENCES releases(run_id)
);

CREATE INDEX IF NOT EXISTS release_file_parts_uploaded_idx
  ON release_file_parts(run_id, candidate_sha256, part_number);
