#!/usr/bin/env python3
"""Read-only preflight for the maintainer-local collection workstation."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
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


def system_proxy_candidates() -> list[str]:
    """macOS system proxy candidates for GitHub delivery (never for sources)."""
    if not shutil.which("scutil"):
        return []
    result = subprocess.run(["scutil", "--proxies"], capture_output=True, text=True)
    if result.returncode != 0:
        return []
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            values[key.strip()] = value.strip()
    candidates: list[str] = []
    if values.get("HTTPSEnable") == "1" and values.get("HTTPSProxy"):
        candidates.append(f"http://{values['HTTPSProxy']}:{values.get('HTTPSPort', '443')}")
    if values.get("SOCKSEnable") == "1" and values.get("SOCKSProxy"):
        candidates.append(f"socks5h://{values['SOCKSProxy']}:{values.get('SOCKSPort', '1080')}")
    return candidates


def github_probe_url() -> str:
    """Origin's Git smart HTTP endpoint (not the github.com landing page)."""
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"], cwd=REPO_ROOT, capture_output=True, text=True
    )
    remote = result.stdout.strip()
    match = re.search(r"github\.com[/:]([^/]+/[^/]+?)(?:\.git)?$", remote)
    if not match:
        return "https://github.com/info/refs"
    return f"https://github.com/{match.group(1)}.git/info/refs?service=git-upload-pack"


def github_smart_http_probe(url: str, proxy: str | None) -> dict[str, Any]:
    """Probe Git smart HTTP with curl so 5xx/gateway pages never count as ok.

    curl is required because socks5h proxies are not supported by urllib and
    ``--fail``-style status inspection avoids misjudging error pages. The
    proxy is passed explicitly per-invocation and never enters the source
    scraper environment.
    """
    command = ["curl", "-s", "-o", "/dev/null", "--max-time", "8", "-w", "%{http_code}", url]
    if proxy:
        command[1:1] = ["-x", proxy]
    result = subprocess.run(command, capture_output=True, text=True)
    code = (result.stdout or "").strip()
    ok = result.returncode == 0 and code in {"200", "301", "401"}
    return {"route": proxy or "direct", "ok": ok, "httpStatus": code or None}


def github_api_auth_probe() -> dict[str, Any]:
    if not shutil.which("gh"):
        return {"ok": False, "code": "GH_CLI_MISSING", "message": "未安装 gh CLI；API 兜底投递不可用。"}
    result = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True, timeout=20)
    return {"ok": result.returncode == 0, "message": (result.stderr or result.stdout).strip()[:300]}


def github_checks() -> dict[str, Any]:
    url = github_probe_url()
    routes = [github_smart_http_probe(url, None)]
    candidates = list(dict.fromkeys(
        [*system_proxy_candidates(), os.environ.get("GITHUB_PROXY", "")]
    ))
    for proxy in candidates:
        if proxy:
            routes.append(github_smart_http_probe(url, proxy))
    return {
        "probeURL": url,
        "routes": routes,
        "apiAuth": github_api_auth_probe(),
        "deliverable": any(item["ok"] for item in routes),
    }


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
    index_lock = REPO_ROOT / ".git" / "index.lock"
    if index_lock.exists():
        git_alive = subprocess.run(["pgrep", "-x", "git"], capture_output=True).returncode == 0
        age = int(time.time() - index_lock.stat().st_mtime)
        checks["gitIndexLock"] = {"path": str(index_lock), "ageSeconds": age, "gitProcessAlive": git_alive}
        if not git_alive:
            errors.append({
                "code": "GIT_INDEX_LOCK_STALE",
                "message": f".git/index.lock 已存在 {age} 秒且没有活跃的 git 进程；提交/发布会失败。确认无 git 操作后删除：rm '{index_lock}'",
            })
        else:
            warnings.append({
                "code": "GIT_INDEX_LOCK_ACTIVE",
                "message": "存在 .git/index.lock 且有 git 进程在运行；等待其结束后再发布。",
            })
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
        # Source probes stay direct (residential IP); GitHub probes below may
        # use a proxy but that proxy never enters the source environment.
        for name, url in probes:
            try:
                checks["network"].append(network_probe(name, url))
            except Exception as exc:  # noqa: BLE001
                code = getattr(exc, "code", "SOURCE_NETWORK_FAILURE")
                checks["network"].append({"provider": name, "ok": False, "code": code, "message": str(exc)})
                errors.append({"code": str(code), "message": f"{name}: {exc}"})
        try:
            github = github_checks()
        except Exception as exc:  # noqa: BLE001
            github = {"routes": [], "deliverable": False, "error": str(exc)}
        checks["github"] = github
        if not github.get("deliverable"):
            warnings.append({
                "code": "GITHUB_DELIVERY_ROUTE_UNAVAILABLE",
                "message": "GitHub smart HTTP 直连与代理路线均不可用；采集不受影响，发布会留在 outbox 等待 deliver。",
            })
        if not (github.get("apiAuth") or {}).get("ok"):
            warnings.append({
                "code": "GITHUB_API_AUTH_UNAVAILABLE",
                "message": "gh CLI 未登录或缺失；Git 路线全部失败时无法使用 API 兜底投递。",
            })

    checks["ok"] = not errors
    checks["errors"] = errors
    checks["warnings"] = warnings
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
