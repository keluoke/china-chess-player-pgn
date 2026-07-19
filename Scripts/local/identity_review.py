#!/usr/bin/env python3
"""Read the repo-external identity workbench without changing review data."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Scripts"))
from source_policy import local_state_root  # noqa: E402


def read_json(path: pathlib.Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--type", choices=("chinese-name", "domestic-fide-link", "domestic-domestic-link"))
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--show", help="candidateID: show the full evidence card")
    args = parser.parse_args()
    workbench = local_state_root() / "identity-workbench"
    queue = read_json(workbench / "review-queue.json", [])
    if args.show:
        for filename in ("chinese-name-candidates.json", "fide-link-candidates.json", "identity-candidates.json"):
            for row in read_json(workbench / filename, []):
                candidate_id = row.get("candidateID") or (
                    f"fide-link-{row.get('domesticID')}-{row.get('candidateFideID')}"
                    if row.get("domesticID") and row.get("candidateFideID") else ""
                )
                if candidate_id == args.show:
                    print(json.dumps(row, ensure_ascii=False, indent=2))
                    return 0
        raise SystemExit(f"candidate not found: {args.show}")
    if args.type:
        queue = [row for row in queue if row.get("reviewType") == args.type]
    selected = queue[:max(0, args.limit)]
    print(json.dumps({
        "workbench": str(workbench),
        "totals": dict(Counter(row.get("reviewType", "unknown") for row in queue)),
        "showing": len(selected),
        "candidates": selected,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
