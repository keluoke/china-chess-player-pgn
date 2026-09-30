#!/usr/bin/env python3
"""Install the user-scoped, resumable local brilliancy history worker."""
from __future__ import annotations

import os
from pathlib import Path
import plistlib
import subprocess
import sys

LABEL = 'cc.aigclabs.chessdb.brilliancy-history'
ROOT = Path(__file__).resolve().parents[2]
PRIVATE = Path.home() / 'Library/Application Support/ChinaChessPlayerPGN/brilliancies'
PLIST = Path.home() / 'Library/LaunchAgents' / f'{LABEL}.plist'


def main():
    PRIVATE.mkdir(parents=True, exist_ok=True)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    job = {
        'Label': LABEL,
        'ProgramArguments': [sys.executable, str(ROOT / 'Scripts/local/run_brilliancy_history.py')],
        'WorkingDirectory': str(ROOT),
        'RunAtLoad': True,
        'KeepAlive': {'SuccessfulExit': False},
        'ThrottleInterval': 120,
        'StandardOutPath': str(PRIVATE / 'history-agent.out.log'),
        'StandardErrorPath': str(PRIVATE / 'history-agent.err.log'),
    }
    PLIST.write_bytes(plistlib.dumps(job))
    os.chmod(PLIST, 0o644)
    domain = f'gui/{os.getuid()}'
    subprocess.run(['launchctl', 'bootout', f'{domain}/{LABEL}'], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['launchctl', 'bootstrap', domain, str(PLIST)], check=True)
    print(f'LOCAL_HISTORY_AGENT_INSTALLED: {LABEL}')


if __name__ == '__main__':
    main()
