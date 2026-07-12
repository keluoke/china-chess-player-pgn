#!/usr/bin/env python3
"""Refresh the observable community contribution funnel from GitHub metadata."""

from __future__ import annotations

import csv
import datetime as dt
import json
import os
import pathlib
import ssl
import urllib.parse
import urllib.request

try:
    import certifi
except ImportError:  # GitHub Actions uses the system CA bundle.
    certifi = None

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "data" / "contribution-funnel.json"
MAIN_REPO = "keluoke/china-chess-player-pgn"
TOOL_REPO = "keluoke/china-chess-contributor"


def api(path: str) -> dict | list:
    request = urllib.request.Request("https://api.github.com" + path)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    request.add_header("User-Agent", "china-chess-contribution-funnel")
    if os.environ.get("GH_TOKEN"):
        request.add_header("Authorization", f"Bearer {os.environ['GH_TOKEN']}")
    context = ssl.create_default_context(cafile=certifi.where() if certifi else None)
    with urllib.request.urlopen(request, timeout=60, context=context) as response:
        return json.loads(response.read().decode("utf-8"))


def search(query: str) -> int:
    result = api("/search/issues?q=" + urllib.parse.quote(query))
    return int(result.get("total_count", 0)) if isinstance(result, dict) else 0


def ingested_submissions() -> int:
    path = ROOT / "data" / "community" / "contributors.csv"
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return sum(int(row.get("submissions") or 0) for row in csv.DictReader(handle))


def main() -> int:
    releases = api(f"/repos/{TOOL_REPO}/releases?per_page=100")
    downloads = sum(int(asset.get("download_count") or 0) for release in releases for asset in release.get("assets", []))
    payload = {
        "schemaVersion": 1,
        "generatedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "definitions": {
            "toolDownloads": "独立工具仓库全部 Release 附件的累计下载次数",
            "successfulCaptures": "正文带 cr-contrib 标记、已成功生成载荷的 PR 数",
            "pullRequests": "社区数据贡献 PR 总数",
            "ingested": "contributors.csv 中已审核入库的提交数",
            "webIssues": "网页向导直接创建的数据贡献 Issue 数",
        },
        "totals": {
            "toolDownloads": downloads,
            "successfulCaptures": search(f'repo:{MAIN_REPO} is:pr in:body "由 cr-contrib"'),
            "pullRequests": search(f'repo:{MAIN_REPO} is:pr in:title "社区数据贡献"'),
            "ingested": ingested_submissions(),
            "webIssues": search(f'repo:{MAIN_REPO} is:issue in:title "[数据贡献]"'),
        },
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(payload["totals"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
