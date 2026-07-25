#!/usr/bin/env python3
"""Gate: every public derived manifest must reference ONE snapshotId.

Runs as the final step of ``build_release_snapshot.py`` (which exports
``SNAPSHOT_ID``). If any derived public manifest carries a different id, the
snapshot aborts and nothing is committed — the previous snapshot keeps
serving as a whole; mixed references never publish (review §3.1).

Standalone runs (no SNAPSHOT_ID env) verify mutual consistency between the
manifests themselves.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

# Derived public manifests that must share one snapshot id. Registry
# manifests are collector inputs, not derived outputs, and are excluded.
MANIFEST_GLOBS = (
    "data/index/manifest.json",
    "data/index/by-player/manifest.json",
    "data/index/event-details/manifest.json",
    "data/index/public-events.json",
    "data/index/player-participation/manifest.json",
    "data/index/player-participation/buckets/*.json",
    "api/v1/manifest.json",
    "api/v2/manifest.json",
    "api/v2/rankings/official/current/*/*.json",
    "data/snapshot.json",
    "data/registry/domestic/manifest.json",
    "data/registry/domestic/presentation-groups.json",
    "data/identity/presentation-names.json",
    "data/search-bootstrap.json",
    "data/search-bootstrap-domestic.json",
    "data/search/domestic-routing.json",
    "data/search/domestic/*.json",
)


def collect() -> dict[str, str]:
    found: dict[str, str] = {}
    for pattern in MANIFEST_GLOBS:
        for path in sorted(DOCS.glob(pattern)):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                found[str(path.relative_to(ROOT))] = f"<unreadable: {error}>"
                continue
            sid = str(payload.get("snapshotId") or "").strip()
            found[str(path.relative_to(ROOT))] = sid or "<missing>"
    return found


def main() -> int:
    expected = os.environ.get("SNAPSHOT_ID", "").strip()
    found = collect()
    if not found:
        print("no derived manifests found; nothing to validate")
        return 0
    reference = expected or next(
        (sid for sid in found.values() if sid and not sid.startswith("<")), ""
    )
    mismatched = {
        path: sid for path, sid in found.items()
        if sid != reference
    }
    if mismatched:
        print("SNAPSHOT CONSISTENCY FAILED — mixed snapshot references:", file=sys.stderr)
        print(f"  expected: {reference or '<none>'}", file=sys.stderr)
        for path, sid in sorted(mismatched.items()):
            print(f"  - {path}: {sid}", file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "snapshotId": reference, "manifests": len(found)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
