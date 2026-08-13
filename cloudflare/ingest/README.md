# Cloudflare shadow ingest

This directory contains the isolated, free-tier shadow ingest service described
in `docs/CLOUDFLARE_INGEST_CONTRACT.md`. It must remain in `shadow` mode until
every production cutover gate in that contract has passed.

## Deploy

Use Wrangler while authenticated to the reviewed Cloudflare account. Never put
`INGEST_HMAC_SECRET` in a shell history, manifest, repository file, or log.

```bash
cd cloudflare/ingest
wrangler d1 migrations apply chess-data-ingest-shadow --remote
wrangler deploy
```

The one-time resource setup is deliberately not automated: create the D1
database, R2 bucket, and Queue with the exact names in `wrangler.toml`, review
the returned identifiers, then update only the D1 `database_id` if necessary.
Store the HMAC secret with `wrangler secret put INGEST_HMAC_SECRET`; the local
copy belongs only in macOS Keychain service
`china-chess-cloudflare-ingest-shadow`.

## Verify

```bash
node --check src/policy.js
node --check src/worker.js
node --test test/*.test.mjs
```

From the protected collector workspace, double-write an already generated
immutable outbox without changing its GitHub delivery state:

```bash
bash Scripts/local/refresh.sh shadow-deliver -- <run-id>
```

Re-running the same command is resumable and idempotent. The private outbox gets
a `shadow-delivery.json` copy of the authenticated remote receipt; this file is
not part of the release manifest.

One logical release can contain up to 384 files. Registration and three-way
merge work is resumably chunked at 10 files per HTTP/Queue invocation; chunks
never become visible snapshots. Only the final D1 transaction advances all
path heads and `current_snapshot` together. The signed release header binds an
ordered SHA-256 for every registration chunk, and the Worker recomputes it
before accepting the chunk. Do not raise 384 or either chunk size without
re-deriving every hard budget in the contract.

The logical release and file ceilings are 96 MiB. Files above 16 MiB use fixed
8 MiB authenticated parts (up to 12); Queue assembles one immutable R2 object,
verifies its size/metadata, removes temporary parts, and only then permits merge.
`Scripts/local/cloudflare_baseline.py` prepares a commit-pinned, source-partitioned
root migration outside Git, emits compensating deletes, delivers at most eight
packages per invocation, and performs a paginated bidirectional head comparison.

D1 storage is guarded by the conservative `quota_storage` ledger introduced in
`0003_d1_storage_ledger.sql`; Worker bindings must not rely on unauthorized
SQLite page-size PRAGMAs.
