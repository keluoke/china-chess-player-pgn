CREATE TABLE IF NOT EXISTS quota_storage (
  key TEXT PRIMARY KEY,
  d1_reserved_bytes INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT
);

-- The deployed database was about 116 KiB before this migration.  Start at
-- 4 MiB so pre-ledger rows and SQLite overhead are conservatively covered.
-- Reservations are intentionally never refunded without an audited cleanup.
INSERT OR IGNORE INTO quota_storage(key,d1_reserved_bytes,updated_at)
VALUES ('ingest',4194304,CURRENT_TIMESTAMP);
