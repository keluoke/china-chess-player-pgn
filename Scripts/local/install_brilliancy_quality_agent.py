#!/usr/bin/env python3
"""Install the user-scoped, resumable local full quality review job."""
from __future__ import annotations

import os
from pathlib import Path
import plistlib
import subprocess
import sys

LABEL = "cc.aigclabs.chessdb.brilliancy-quality"
ROOT = Path(__file__).resolve().parents[2]
PRIVATE = Path.home() / "Library/Application Support/ChinaChessPlayerPGN/brilliancies"
PLIST = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"


def main() -> int:
    PRIVATE.mkdir(parents=True, exist_ok=True)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    job = {
        "Label": LABEL,
        "ProgramArguments": [sys.executable, str(ROOT / "Scripts/local/run_brilliancy_quality_all.py")],
        "WorkingDirectory": str(ROOT),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 120,
        "StandardOutPath": str(PRIVATE / "quality-agent.out.log"),
        "StandardErrorPath": str(PRIVATE / "quality-agent.err.log"),
    }
    PLIST.write_bytes(plistlib.dumps(job))
    os.chmod(PLIST, 0o644)
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", f"{domain}/{LABEL}"], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["launchctl", "bootstrap", domain, str(PLIST)], check=True)
    print(f"LOCAL_QUALITY_AGENT_INSTALLED: {LABEL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
