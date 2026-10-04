#!/usr/bin/env python3
"""Keep two local quality shards running until all candidates are reviewed."""
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

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = Path.home() / "Library/Application Support/ChinaChessPlayerPGN/brilliancies"
WORKER = ROOT / "Scripts/local/run_brilliancy_quality.py"
CHILDREN: list[subprocess.Popen] = []
HARD_ERRORS = ("QC_BACKUP_REMOTE_CONFLICT", "QC_CHECKPOINT_SCHEMA_INVALID",
               "QC_PRIVATE_MODE_REQUIRED", "QC_PRIVATE_SYMLINK_FORBIDDEN",
               "QUEUE_STORAGE_BUDGET_EXCEEDED", "QC_HISTORY_INCOMPLETE",
               "QC_ENGINE_EVIDENCE_INCOMPLETE", "QC_ENGINE_PV_ILLEGAL",
               "QC_FIRST_INCOMPLETE", "QC_DEEP_PRIORITY_MODE_INVALID")
TRANSIENT_ERRORS = ("ReadTimeoutError", "ConnectTimeoutError", "EndpointConnectionError",
                    "ConnectionClosedError", "IncompleteReadError", "SlowDown",
                    "ServiceUnavailable", "ConnectionError", "TimeoutError")


def stop_children(signum, frame):
    del frame
    for child in CHILDREN:
        if child.poll() is None:
            child.terminate()
    raise SystemExit(128 + signum)


def write_status(value: dict, lane: str = "first") -> None:
    path = PRIVATE / ("quality-full.json" if lane == "first" else "quality-deep-full.json")
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    handle = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def private_log(path: Path):
    if path.is_symlink():
        raise ValueError("QC_PRIVATE_LOG_SYMLINK")
    descriptor = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    if os.fstat(descriptor).st_mode & 0o077:
        os.close(descriptor)
        raise ValueError("QC_PRIVATE_LOG_MODE")
    return os.fdopen(descriptor, "w", encoding="utf-8")


def run_batch(lane: str = "first") -> tuple[list[int], list[dict], list[str]]:
    CHILDREN.clear()
    logs = []
    paths = []
    for shard in (0, 1):
        label = "quality-full" if lane == "first" else "quality-deep-full"
        path = PRIVATE / f"{label}-shard-{shard}.log"
        output = private_log(path)
        paths.append(path)
        logs.append(output)
        command = [sys.executable, str(WORKER), "--shard", str(shard),
                   "--sample-size", "0", "--nodes",
                   "1000000" if lane == "first" else "5000000",
                   "--max-new", "1000" if lane == "first" else "100",
                   "--backup-every", "1000" if lane == "first" else "25"]
        if lane == "deep":
            command += ["--lane", "deep", "--prioritize-from-first"]
        CHILDREN.append(subprocess.Popen(command, cwd=ROOT, stdout=output,
                                         stderr=subprocess.STDOUT))
    codes = [child.wait() for child in CHILDREN]
    for output in logs:
        output.close()
    tails = [path.read_text(encoding="utf-8")[-8000:] for path in paths]
    summaries = []
    for code, body in zip(codes, tails):
        summary = None
        if code == 0:
            for line in reversed(body.splitlines()):
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict) and "graded" in value:
                    summary = value
                    break
        summaries.append(summary)
    return codes, summaries, tails


def main(lane: str = "first") -> int:
    if lane not in {"first", "deep"}:
        raise ValueError("QC_FULL_LANE_INVALID")
    PRIVATE.mkdir(parents=True, exist_ok=True)
    lock_path = PRIVATE / ("quality-full.lock" if lane == "first"
                           else "quality-deep-full.lock")
    if lock_path.is_symlink():
        raise ValueError("QC_FULL_LOCK_SYMLINK")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(descriptor, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("QC_FULL_ALREADY_RUNNING")
        signal.signal(signal.SIGTERM, stop_children)
        signal.signal(signal.SIGINT, stop_children)
        failures = 0
        while True:
            codes, summaries, tails = run_batch(lane)
            if any(code != 0 or summary is None for code, summary in zip(codes, summaries)):
                reason = "hard-error" if any(token in tail for tail in tails for token in HARD_ERRORS) else "retryable-error"
                if reason == "retryable-error" and not any(
                        token in tail for tail in tails for token in TRANSIENT_ERRORS):
                    reason = "unknown-error"
                if reason != "retryable-error":
                    write_status({"status": "halted", "reason": reason, "exitCodes": codes}, lane)
                    print(json.dumps({"qualityFull": "halted", "lane": lane,
                                      "reason": reason}), flush=True)
                    return 0  # Launchd must not loop on a deterministic conflict.
                failures += 1
                write_status({"status": "retrying", "attempt": failures,
                              "exitCodes": codes}, lane)
                time.sleep(min(600, 60 * 2 ** min(failures, 3)))
                continue
            failures = 0
            total = sum(row["sampleSize"] for row in summaries)
            graded = sum(row["graded"] for row in summaries)
            if any(row["r2Backup"] != "synced" for row in summaries):
                write_status({"status": "halted", "reason": "r2-backup-not-synced"}, lane)
                return 0
            value = {"status": "complete" if graded == total else "running",
                     "candidatePositions": total, "graded": graded,
                     "byShard": [{"shard": row["shard"], "graded": row["graded"],
                                  "total": row["sampleSize"]} for row in summaries],
                     "publicAutoPublish": False}
            write_status(value, lane)
            print(json.dumps({"qualityFull": value, "lane": lane}, ensure_ascii=False), flush=True)
            if graded == total:
                return 0
            time.sleep(5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lane", choices=("first", "deep"), default="first")
    raise SystemExit(main(parser.parse_args().lane))
