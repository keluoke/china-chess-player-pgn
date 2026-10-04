#!/usr/bin/env python3
"""Install the deep historical quality pass after the first pass is certified."""
from __future__ import annotations

import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys

LABEL = "cc.aigclabs.chessdb.brilliancy-quality-deep-full"
ROOT = Path(__file__).resolve().parents[2]
PRIVATE = Path.home() / "Library/Application Support/ChinaChessPlayerPGN/brilliancies"
PLIST = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"


def certified_first_pass(path: Path) -> None:
    if (path.is_symlink() or not path.is_file() or path.stat().st_uid != os.getuid()
            or path.stat().st_mode & 0o077):
        raise ValueError("QC_FIRST_SUMMARY_MISSING_OR_EXPOSED")
    summary = json.loads(path.read_text(encoding="utf-8"))
    shards = summary.get("byShard", [])
    if (summary.get("status") != "complete"
            or not isinstance(summary.get("candidatePositions"), int)
            or summary["candidatePositions"] <= 0
            or summary.get("graded") != summary["candidatePositions"]
            or len(shards) != 2 or {row.get("shard") for row in shards} != {0, 1}
            or any(row.get("graded") != row.get("total") for row in shards)
            or summary.get("publicAutoPublish") is not False):
        raise ValueError("QC_FIRST_NOT_COMPLETE")


def main() -> int:
    certified_first_pass(PRIVATE / "quality-full.json")
    branch = subprocess.run(["git", "symbolic-ref", "-q", "--short", "HEAD"],
                            cwd=ROOT, text=True, capture_output=True)
    if branch.returncode == 0 or subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT,
            text=True, capture_output=True, check=True).stdout.strip():
        raise ValueError("QC_PINNED_CLEAN_WORKTREE_REQUIRED")
    PRIVATE.mkdir(parents=True, exist_ok=True)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    for name in ("quality-deep-full-agent.out.log", "quality-deep-full-agent.err.log"):
        path = PRIVATE / name
        if path.is_symlink():
            raise ValueError("QC_PRIVATE_LOG_SYMLINK")
        descriptor = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        os.close(descriptor)
        if path.stat().st_mode & 0o077:
            raise ValueError("QC_PRIVATE_LOG_MODE")
    job = {
        "Label": LABEL,
        "ProgramArguments": [sys.executable,
                             str(ROOT / "Scripts/local/run_brilliancy_quality_all.py"),
                             "--lane", "deep"],
        "WorkingDirectory": str(ROOT),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 120,
        "StandardOutPath": str(PRIVATE / "quality-deep-full-agent.out.log"),
        "StandardErrorPath": str(PRIVATE / "quality-deep-full-agent.err.log"),
    }
    PLIST.write_bytes(plistlib.dumps(job))
    os.chmod(PLIST, 0o644)
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", f"{domain}/{LABEL}"], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["launchctl", "bootstrap", domain, str(PLIST)], check=True)
    print(f"LOCAL_QUALITY_DEEP_FULL_AGENT_INSTALLED: {LABEL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
