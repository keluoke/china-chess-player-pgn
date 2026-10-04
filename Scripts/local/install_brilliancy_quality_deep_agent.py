#!/usr/bin/env python3
"""Run the 600-position deep quality calibration as two resumable launchd jobs."""
from __future__ import annotations

import os
from pathlib import Path
import plistlib
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = Path.home() / "Library/Application Support/ChinaChessPlayerPGN/brilliancies"
AGENTS = Path.home() / "Library/LaunchAgents"
LABEL = "cc.aigclabs.chessdb.brilliancy-quality-deep"


def job(shard: int) -> dict:
    if shard not in (0, 1):
        raise ValueError("QC_DEEP_SHARD_INVALID")
    return {
        "Label": f"{LABEL}-{shard}",
        "ProgramArguments": [
            sys.executable, str(ROOT / "Scripts/local/run_brilliancy_quality.py"),
            "--lane", "deep", "--shard", str(shard),
            "--sample-size", "300", "--nodes", "5000000", "--backup-every", "50",
        ],
        "WorkingDirectory": str(ROOT),
        "RunAtLoad": True,
        "StandardOutPath": str(PRIVATE / f"quality-deep-shard-{shard}.log"),
        "StandardErrorPath": str(PRIVATE / f"quality-deep-shard-{shard}.err.log"),
    }


def main() -> int:
    PRIVATE.mkdir(parents=True, exist_ok=True)
    AGENTS.mkdir(parents=True, exist_ok=True)
    domain = f"gui/{os.getuid()}"
    for shard in (0, 1):
        config = job(shard)
        for name in ("StandardOutPath", "StandardErrorPath"):
            path = Path(config[name])
            if path.is_symlink():
                raise ValueError("QC_DEEP_LOG_SYMLINK")
            descriptor = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
            os.close(descriptor)
            if path.stat().st_mode & 0o077:
                raise ValueError("QC_DEEP_LOG_MODE")
        plist = AGENTS / f"{config['Label']}.plist"
        plist.write_bytes(plistlib.dumps(config))
        os.chmod(plist, 0o644)
        subprocess.run(["launchctl", "bootout", f"{domain}/{config['Label']}"],
                       check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["launchctl", "bootstrap", domain, str(plist)], check=True)
    print("LOCAL_QUALITY_DEEP_SAMPLE_INSTALLED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
