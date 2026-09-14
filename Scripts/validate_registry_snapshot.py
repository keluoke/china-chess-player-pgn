#!/usr/bin/env python3
"""Reject deployment of a registry changed after the validated rebuild."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATHS = (
    "docs/data/registry/players.json",
    "docs/data/registry/manifest.json",
)


def validate(root: Path) -> list[str]:
    snapshot = json.loads((root / "docs/data/snapshot.json").read_text())
    outputs = snapshot.get("outputs") or []
    errors = []
    for relative in REGISTRY_PATHS:
        facts = [row for row in outputs if row.get("path") == relative]
        path = root / relative
        if len(facts) != 1 or not facts[0].get("present") or not path.is_file():
            errors.append(f"{relative}: missing unique validated output")
            continue
        raw = path.read_bytes()
        if (facts[0].get("sha256") != hashlib.sha256(raw).hexdigest()
                or facts[0].get("bytes") != len(raw)):
            errors.append(f"{relative}: changed since validated rebuild")
    return errors


def main() -> int:
    try:
        errors = validate(ROOT)
    except (OSError, ValueError, TypeError) as error:
        errors = [str(error)]
    for error in errors:
        print(f"REGISTRY_SNAPSHOT_MISMATCH: {error}")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
