#!/usr/bin/env python3
"""Locally verify community-submitted evidence URLs (residential IP required).

CI cannot reach chess-results.com (datacenter IPs are blocked), so this runs
on the maintainer's machine via `Scripts/local/refresh.sh verify`. It fetches
every evidence/source URL referenced by community files and records
reachability in data/generated/verification-report.json.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import pathlib
import sys
import time
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sync_static_pgn import USER_AGENT, tls_context  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
REPORT = REPO_ROOT / "data" / "generated" / "verification-report.json"

SOURCES = [
    (REPO_ROOT / "data" / "community" / "federation-overrides.csv", "evidence_url"),
    (REPO_ROOT / "data" / "manual" / "domestic-player-sightings.csv", "source_url"),
]


def probe(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=30, context=tls_context()) as resp:
            body = resp.read(4096)
            return "ok" if resp.status == 200 and body else f"http-{resp.status}"
    except Exception as exc:  # noqa: BLE001
        return f"error: {exc}"


def main() -> int:
    results = []
    for path, column in SOURCES:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            for i, row in enumerate(csv.DictReader(fh), start=2):
                url = (row.get(column) or "").strip()
                if not url:
                    continue
                status = probe(url)
                results.append({
                    "file": str(path.relative_to(REPO_ROOT)),
                    "line": i,
                    "id": (row.get("fide_id") or row.get("name") or "").strip(),
                    "url": url,
                    "status": status,
                })
                print(f"{status:<28} {url}")
                time.sleep(0.5)

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(
        json.dumps(
            {
                "generatedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
                "checked": len(results),
                "failed": sum(1 for r in results if r["status"] != "ok"),
                "results": results,
            },
            ensure_ascii=False,
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )
    failed = sum(1 for r in results if r["status"] != "ok")
    print(f"checked={len(results)} failed={failed} -> {REPORT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
