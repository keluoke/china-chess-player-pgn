#!/usr/bin/env python3
"""Run the frozen brilliancy history on this Mac, uploading durable R2 checkpoints."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from upload_bulk_to_r2 import load_secrets

CODE_ROOT = Path(__file__).resolve().parents[2]
PRIVATE = Path.home() / 'Library/Application Support/ChinaChessPlayerPGN/brilliancies'
COLLECTOR_SECRETS = CODE_ROOT.parent / 'kimi/.secrets.local'
DEFAULT_SNAPSHOT = CODE_ROOT.parent / 'kimi-brilliancy-history'
ENGINE = PRIVATE / 'stockfish-16-source/src/stockfish'
CHILDREN: list[subprocess.Popen] = []


def stop_children(signum, frame):
    for child in CHILDREN:
        if child.poll() is None:
            child.terminate()
    raise SystemExit(128 + signum)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument('--engine', type=Path, default=ENGINE)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    PRIVATE.mkdir(parents=True, exist_ok=True)
    lock = (PRIVATE / 'history.lock').open('w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('HISTORY_ALREADY_RUNNING')
    if not (args.root / 'docs/data/snapshot.json').is_file():
        raise SystemExit('HISTORY_SNAPSHOT_MISSING')
    if not args.engine.is_file() or not os.access(args.engine, os.X_OK):
        raise SystemExit('STOCKFISH_16_MISSING')
    source_secrets = load_secrets(COLLECTOR_SECRETS)
    secrets = {name: source_secrets.get(name) for name in
               ('R2_ENDPOINT', 'R2_ACCESS_KEY_ID', 'R2_SECRET_ACCESS_KEY', 'R2_BUCKET')}
    key_path = PRIVATE / 'queue-encryption.key'
    if not key_path.is_file() or (key_path.stat().st_mode & 0o077):
        raise SystemExit('HISTORY_ENCRYPTION_KEY_MISSING_OR_EXPOSED')
    secrets['BRILLIANCY_QUEUE_KEY'] = key_path.read_text().strip()
    for name in ('R2_ENDPOINT', 'R2_ACCESS_KEY_ID', 'R2_SECRET_ACCESS_KEY', 'R2_BUCKET'):
        if not secrets.get(name):
            raise SystemExit(f'HISTORY_CONFIG_MISSING:{name}')
    env = os.environ.copy()
    env.update(secrets)
    signal.signal(signal.SIGTERM, stop_children)
    signal.signal(signal.SIGINT, stop_children)
    failures = 0
    while True:
        CHILDREN.clear()
        logs = []
        for shard in range(2):
            output = PRIVATE / f'history-shard-{shard}.json'
            log = (PRIVATE / f'history-shard-{shard}.log').open('w')
            logs.append(log)
            CHILDREN.append(subprocess.Popen([
                sys.executable, str(CODE_ROOT / 'Scripts/brilliancy_queue.py'),
                '--lane', 'history', '--root', str(args.root), '--shard', str(shard),
                '--engine', str(args.engine), '--seconds', '3300', '--max-games', '10000',
                '--summary', str(output),
            ], env=env, stdout=log, stderr=subprocess.STDOUT))
        codes = [child.wait() for child in CHILDREN]
        for log in logs:
            log.close()
        if any(codes):
            failures += 1
            print(json.dumps({'historyError': codes, 'attempt': failures}), flush=True)
            if args.once:
                raise SystemExit(1)
            # Transient R2 disconnects can happen after a checkpoint PUT.
            # Each child resumes from the last authenticated R2 state, so a
            # long retry delay only strands the historical scan.
            time.sleep(min(300, 60 * 2 ** min(failures, 3)))
            continue
        failures = 0
        rows = [json.loads((PRIVATE / f'history-shard-{shard}.json').read_text()) for shard in range(2)]
        total = {field: sum(row[field] for row in rows) for field in
                 ('eligibleGames', 'completedGames', 'pendingGames', 'candidatePositions', 'errorsThisRun')}
        print(json.dumps({'history': total, 'snapshot': rows[0]['inputSnapshot']}), flush=True)
        if total['pendingGames'] == 0:
            print('HISTORY_COMPLETE_AND_UPLOADED', flush=True)
            return
        if args.once:
            return
        time.sleep(5)


if __name__ == '__main__':
    main()
