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

The 12-file package ceiling is intentional. It keeps the worst-case merge at
43 D1 queries, below the Workers Free limit of 50 queries per invocation. Split
larger outboxes before shadow delivery; never raise the ceiling without first
implementing and reviewing a resumable multi-message merge state machine.
