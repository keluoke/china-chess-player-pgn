#!/usr/bin/env python3
"""Resume a private, encrypted pilot review of the certified brilliancy queue.

Read-only on R2. Results stay in the maintainer's private directory; this
command cannot publish to the site or rewrite the completed source queue.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter, defaultdict
from contextlib import closing
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import time

import chess.engine
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Scripts"))
import brilliancy_quality as qc
from brilliancy_queue import R2Store
from upload_bulk_to_r2 import load_secrets

PRIVATE = Path.home() / "Library/Application Support/ChinaChessPlayerPGN/brilliancies"
SECRETS = ROOT.parent / "kimi/.secrets.local"
STOCKFISH_16 = PRIVATE / "stockfish-16-source/src/stockfish"
STOCKFISH_17 = Path("/opt/homebrew/bin/stockfish")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def stratum(item: dict) -> str:
    ev = item.get("verification", {}).get("evaluation", {})
    cp = ev.get("score", 0) if ev.get("type") == "cp" else 10000
    band = "low" if cp < 150 else "mid" if cp < 400 else "high"
    actual = item.get("actualContinuationUci", [])
    accepted = bool(actual) and actual[0][-2:] == item["move"]["uci"][-2:]
    return f'{item["themes"][0]}:{band}:{"accepted" if accepted else "declined"}'


def stratified_sample(items: list[dict], count: int) -> list[dict]:
    if count <= 0 or count >= len(items):
        return sorted(items, key=lambda x: x["id"])
    groups = defaultdict(list)
    for item in items:
        groups[stratum(item)].append(item)
    if count < len(groups):
        raise ValueError("QC_SAMPLE_SMALLER_THAN_CHESS_STRATA")
    fractions = {k: count * len(rows) / len(items) for k, rows in groups.items()}
    quotas = {k: max(1, math.floor(fractions[k])) for k in groups}
    while sum(quotas.values()) > count:
        key = max((k for k in groups if quotas[k] > 1),
                  key=lambda k: (quotas[k] - fractions[k], quotas[k]))
        quotas[key] -= 1
    while sum(quotas.values()) < count:
        key = max((k for k in groups if quotas[k] < len(groups[k])),
                  key=lambda k: (fractions[k] - quotas[k], len(groups[k]) - quotas[k]))
        quotas[key] += 1
    selected = []
    for key, rows in groups.items():
        rows.sort(key=lambda x: hashlib.sha256((qc.RULE_VERSION + x["id"]).encode()).digest())
        selected.extend(rows[:quotas[key]])
    return sorted(selected, key=lambda x: x["id"])


def load_candidates(shard: int, key: str) -> tuple[list[dict], str]:
    import boto3
    from botocore.config import Config
    values = load_secrets(SECRETS)
    client = boto3.client(
        "s3", endpoint_url=values["R2_ENDPOINT"], region_name="auto",
        aws_access_key_id=values["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=values["R2_SECRET_ACCESS_KEY"],
        config=Config(connect_timeout=15, read_timeout=120,
                      retries={"total_max_attempts": 2}),
    )
    state = R2Store(client, values["R2_BUCKET"], shard, key).load()
    baseline = state.get("baselineGames")
    version = state.get("activeVersion")
    if not baseline or not version:
        raise ValueError("QC_HISTORY_BASELINE_MISSING")
    incomplete = [gid for gid in baseline if state["records"].get(gid, {}).get("status") != "complete"
                  or state["records"][gid].get("version") != version]
    if incomplete:
        raise ValueError(f"QC_HISTORY_INCOMPLETE:{len(incomplete)}")
    return [item for gid in baseline for item in state["records"][gid]["candidates"]], version


def private_file(path: Path) -> None:
    if not path.exists():
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
    if path.stat().st_mode & 0o077:
        raise ValueError(f"QC_PRIVATE_MODE_REQUIRED:{path.name}")


def database(path: Path) -> sqlite3.Connection:
    private_file(path)
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=DELETE")
    db.execute("PRAGMA synchronous=FULL")
    db.execute("CREATE TABLE IF NOT EXISTS results ("
               "candidate_id TEXT PRIMARY KEY, input_hash TEXT NOT NULL, "
               "profile TEXT NOT NULL, grade TEXT NOT NULL, encrypted BLOB NOT NULL)")
    db.commit()
    return db


def save_result(db: sqlite3.Connection, cipher: AESGCM, item: dict,
                profile: str, result: dict) -> None:
    raw = json.dumps(result, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode()
    nonce = os.urandom(12)
    aad = (item["id"] + profile).encode()
    payload = nonce + cipher.encrypt(nonce, raw, aad)
    with db:
        db.execute("INSERT OR REPLACE INTO results VALUES (?,?,?,?,?)",
                   (item["id"], qc.digest(item), profile, result["grade"], payload))


def cached_result(db: sqlite3.Connection, cipher: AESGCM,
                  item: dict, profile: str) -> dict | None:
    row = db.execute("SELECT input_hash,profile,grade,encrypted FROM results WHERE candidate_id=?",
                     (item["id"],)).fetchone()
    if not row or (row[0], row[1]) != (qc.digest(item), profile):
        return None
    blob = row[3]
    result = json.loads(cipher.decrypt(blob[:12], blob[12:],
                                       (item["id"] + profile).encode()))
    if result.get("candidateId") != item["id"] or result.get("grade") != row[2]:
        raise ValueError("QC_ENCRYPTED_RESULT_MISMATCH")
    return result


def run(args) -> dict:
    PRIVATE.mkdir(parents=True, exist_ok=True)
    key_path = PRIVATE / "queue-encryption.key"
    if not key_path.is_file() or key_path.stat().st_mode & 0o077:
        raise ValueError("QC_KEY_MISSING_OR_EXPOSED")
    key_text = key_path.read_text().strip()
    key = base64.b64decode(key_text, validate=True)
    if len(key) != 32:
        raise ValueError("QC_KEY_INVALID")
    for binary in args.engines:
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise ValueError(f"QC_ENGINE_MISSING:{binary.name}")
    profile = qc.digest({"rule": qc.RULE_VERSION, "nodes": args.nodes,
                         "engines": [sha256_file(p) for p in args.engines],
                         "reviewCode": sha256_file(Path(qc.__file__)),
                         "chess": chess.__version__})
    lock = PRIVATE / f"quality-shard-{args.shard}.lock"
    private_file(lock)
    with lock.open("r+") as lock_handle:
        fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        items, source_version = load_candidates(args.shard, key_text)
        selected = stratified_sample(items, args.sample_size)
        db_path = PRIVATE / f"quality-shard-{args.shard}.sqlite3"
        with closing(database(db_path)) as db:
            cipher = AESGCM(key)
            engines = [chess.engine.SimpleEngine.popen_uci(str(p)) for p in args.engines]
            try:
                for engine in engines:
                    engine.configure({"Threads": 1, "Hash": 64})
                completed = skipped = 0
                for item in selected:
                    if cached_result(db, cipher, item, profile) is not None:
                        skipped += 1
                        continue
                    if args.max_new and completed >= args.max_new:
                        break
                    try:
                        outcome = qc.review(item, engines, args.nodes)
                    except ValueError as exc:
                        if not str(exc).startswith(("QC_PLY", "QC_ID", "QC_GAME_MOVE",
                                                    "QC_FEN", "QC_TARGET", "QC_CONTINUATION",
                                                    "QC_NO_LEGAL_ACCEPTANCE")):
                            raise
                        outcome = {"candidateId": item["id"], "qualityRuleVersion": qc.RULE_VERSION,
                                   "grade": "D", "reasons": [str(exc)], "engines": []}
                    save_result(db, cipher, item, profile, outcome)
                    completed += 1
                    if completed % 10 == 0:
                        print(json.dumps({"shard": args.shard, "processedThisRun": completed,
                                          "sampleSize": len(selected), "sourceVersion": source_version}),
                              flush=True)
            finally:
                for engine in engines:
                    engine.quit()
            grades = Counter()
            for item in selected:
                result = cached_result(db, cipher, item, profile)
                if result:
                    grades[result["grade"]] += 1
            summary = {"shard": args.shard, "sampleSize": len(selected), "selectedCandidates": len(items),
                       "sourceVersion": source_version, "profile": profile,
                       "processedThisRun": completed, "reused": skipped,
                       "graded": sum(grades.values()), "grades": dict(sorted(grades.items())),
                       "publicAutoPublish": False}
            print(json.dumps(summary, ensure_ascii=False), flush=True)
            return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shard", type=int, choices=(0, 1), required=True)
    parser.add_argument("--sample-size", type=int, default=300)
    parser.add_argument("--nodes", type=int, default=1_000_000)
    parser.add_argument("--max-new", type=int, default=0)
    parser.add_argument("--engines", nargs=2, type=Path, default=[STOCKFISH_16, STOCKFISH_17])
    args = parser.parse_args()
    if args.sample_size < 0 or args.nodes < 100_000 or args.max_new < 0:
        parser.error("invalid pilot budget")
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
