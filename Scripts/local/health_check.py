#!/usr/bin/env python3
"""Read-only preflight for the maintainer-local collection workstation."""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from typing import Any

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR.parent))

from source_http import fetch_bytes  # noqa: E402
from source_policy import local_state_root  # noqa: E402


def git_dirty(paths: list[str]) -> list[str]:
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all", "--", *paths],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    return [line[3:] for line in result.stdout.splitlines() if len(line) > 3][:20]


def valid_fide_caches() -> list[dict[str, Any]]:
    cache = pathlib.Path.home() / "Library" / "Caches" / "ChinaChessPlayerPGN" / "fide"
    result: list[dict[str, Any]] = []
    for path in sorted(cache.glob("**/*.zip"), reverse=True):
        valid = False
        error = ""
        try:
            with zipfile.ZipFile(path) as archive:
                valid = archive.testzip() is None and any(
                    name.lower().endswith((".xml", ".txt", ".csv", ".tsv"))
                    for name in archive.namelist()
                )
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
        result.append({"path": str(path), "bytes": path.stat().st_size, "valid": valid, "error": error})
    return result[:5]


def network_probe(name: str, url: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "ChinaChessPlayerPGN/HealthCheck", "Accept": "text/html,application/xhtml+xml"},
    )
    body, final_url, headers = fetch_bytes(
        request,
        timeout=20,
        retries=0,
        expected_types=("text/html", "application/xhtml+xml"),
    )
    return {
        "provider": name,
        "ok": True,
        "bytes": len(body),
        "contentType": headers.get_content_type(),
        "finalURL": final_url,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="skip provider connectivity probes")
    args = parser.parse_args()

    state_root = local_state_root()
    state_root.mkdir(parents=True, exist_ok=True)
    state_root.chmod(0o700)
    usage = shutil.disk_usage(state_root)
    registry_dirty = git_dirty([
        "docs/data/registry",
        "data/generated/federation-snapshots",
        "data/generated/transfer-candidates.json",
        "data/generated/local-release-manifest.json",
    ])
    bulk_dirty = git_dirty(["docs/data/bulk", "data/generated/local-release-manifest.json"])
    checks: dict[str, Any] = {
        "repo": str(REPO_ROOT),
        "stateRoot": str(state_root),
        "freeBytes": usage.free,
        "releasePaths": {"registryDirty": registry_dirty, "lichessDirty": bulk_dirty},
        "fideCaches": valid_fide_caches(),
        "network": [],
    }
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    if usage.free < 2 * 1024**3:
        errors.append({"code": "DISK_SPACE_LOW", "message": "本地运行区可用空间不足 2 GiB。"})
    if registry_dirty:
        errors.append({"code": "DIRTY_RELEASE_PATH", "message": "FIDE 发布路径已有未提交修改。"})
    if bulk_dirty:
        errors.append({"code": "DIRTY_RELEASE_PATH", "message": "Lichess 发布路径已有未提交修改。"})
    if not any(item.get("valid") for item in checks["fideCaches"]):
        warnings.append({
            "code": "FIDE_LAST_GOOD_MISSING",
            "message": "没有可用的 FIDE last-good；下次 registry 必须成功完成新下载。",
        })

    if not args.offline:
        probes = [
            ("Chess-Results", "https://chess-results.com/"),
            ("FIDE", "https://ratings.fide.com/download_lists.phtml"),
            ("Lichess", "https://database.lichess.org/"),
        ]
        for name, url in probes:
            try:
                checks["network"].append(network_probe(name, url))
            except Exception as exc:  # noqa: BLE001
                code = getattr(exc, "code", "SOURCE_NETWORK_FAILURE")
                checks["network"].append({"provider": name, "ok": False, "code": code, "message": str(exc)})
                errors.append({"code": str(code), "message": f"{name}: {exc}"})

    checks["ok"] = not errors
    checks["errors"] = errors
    checks["warnings"] = warnings
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
