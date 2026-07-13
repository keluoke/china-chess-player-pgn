#!/usr/bin/env python3
"""CI guard: public JSON must not expose raw affiliation/contact fields.

Domestic and youth event records can include minors. Public projections may
carry province/city-level ``publicLocation`` but raw club/school/contact values
stay in maintainer-only sources outside the deployed tree.
"""

from __future__ import annotations

import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
PUBLIC_ROOTS = (REPO_ROOT / "docs" / "data", REPO_ROOT / "docs" / "api")
FORBIDDEN_KEYS = {"club", "school", "contact", "phone", "email", "wechat"}


def offending_keys(node, path="$"):
    if isinstance(node, dict):
        for key, value in node.items():
            if key in FORBIDDEN_KEYS and value not in (None, "", [], {}):
                yield f"{path}.{key}"
            yield from offending_keys(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from offending_keys(item, f"{path}[{index}]")


def main() -> int:
    roots = [root for root in PUBLIC_ROOTS if root.exists()]
    if not roots:
        print("public JSON roots not built; nothing to validate")
        return 0
    failures: list[str] = []
    files_checked = 0
    for root in roots:
        for path in sorted(root.rglob("*.json")):
            files_checked += 1
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as error:
                failures.append(f"{path.relative_to(REPO_ROOT)}: invalid JSON ({error})")
                continue
            hits = list(offending_keys(payload))
            if hits:
                rel = path.relative_to(REPO_ROOT)
                failures.append(f"{rel}: {len(hits)} private field(s), e.g. {hits[0]}")
    if failures:
        print("PRIVACY CHECK FAILED — private affiliation/contact fields on the public surface:")
        for line in failures[:20]:
            print(f"  - {line}")
        print("Rebuild public projections after reducing raw values to publicLocation.")
        return 1
    print(f"privacy check OK: {files_checked} public JSON files contain no private fields")
    return 0


if __name__ == "__main__":
    sys.exit(main())
