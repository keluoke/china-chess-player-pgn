#!/usr/bin/env python3
"""Checkpoint chess-only quality evidence for new archived games on GitHub.

This worker reads the separately owned incremental candidate queue. It neither
visits PGN sources nor writes public files. Two encrypted R2 quality keys per
shard retain first/deep evidence across ephemeral GitHub runners.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time

import chess
import chess.engine
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Scripts/local"))
import run_brilliancy_quality as runner
import brilliancy_quality as qc
import brilliancy_quality_gate as gate
from brilliancy_quality_backup import QualityBackup
from brilliancy_queue import R2Store

ERROR_RESULTS = ("QC_PLY", "QC_ID", "QC_GAME_MOVE", "QC_FEN", "QC_TARGET",
                 "QC_CONTINUATION", "QC_NO_LEGAL_ACCEPTANCE")


def candidates_from_state(state: dict) -> list[dict]:
    """Use only complete records from the currently certified queue version."""
    version = state.get("activeVersion")
    if not isinstance(version, str) or not version or state.get("shards") != 2:
        raise ValueError("QC_INCREMENTAL_SOURCE_VERSION_INVALID")
    by_id = {}
    hashes = {}
    for record in state.get("records", {}).values():
        if record.get("status") != "complete" or record.get("version") != version:
            continue
        for item in record.get("candidates", []):
            candidate_id = item.get("id")
            if not isinstance(candidate_id, str) or not candidate_id.startswith("br-"):
                raise ValueError("QC_INCREMENTAL_CANDIDATE_ID_INVALID")
            item_hash = qc.digest(item)
            if candidate_id in hashes and hashes[candidate_id] != item_hash:
                raise ValueError("QC_INCREMENTAL_DUPLICATE_CONFLICT")
            hashes[candidate_id] = item_hash
            by_id[candidate_id] = item
    return [by_id[key] for key in sorted(by_id)]


def r2_client():
    import boto3
    from botocore.config import Config
    settings = {name: os.environ.get(name, "") for name in
                ("R2_ENDPOINT", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")}
    if not all(settings.values()) or settings["R2_BUCKET"] != "chess-data":
        raise ValueError("QC_INCREMENTAL_R2_CONFIG_INVALID")
    client = boto3.client("s3", endpoint_url=settings["R2_ENDPOINT"], region_name="auto",
                          aws_access_key_id=settings["R2_ACCESS_KEY_ID"],
                          aws_secret_access_key=settings["R2_SECRET_ACCESS_KEY"],
                          config=Config(connect_timeout=15, read_timeout=120,
                                        retries={"total_max_attempts": 2}))
    return client, settings["R2_BUCKET"]


def quality_profile(nodes: int, engines: list[Path]) -> str:
    return qc.digest({"rule": qc.RULE_VERSION, "nodes": nodes,
                      "engines": [runner.sha256_file(binary) for binary in engines],
                      "reviewCode": runner.sha256_file(Path(qc.__file__)),
                      "chess": chess.__version__})


def review_or_reject(item: dict, engines: list[chess.engine.SimpleEngine], nodes: int) -> dict:
    try:
        return qc.review(item, engines, nodes)
    except ValueError as error:
        if not str(error).startswith(ERROR_RESULTS):
            raise
        return {"candidateId": item["id"], "qualityRuleVersion": qc.RULE_VERSION,
                "grade": "D", "reasons": [str(error)], "engines": []}


def deep_priority(first: dict | None) -> tuple[int, int]:
    """Review promising chess positions first; still revisit every other item."""
    if not first:
        return (2, 0)
    try:
        features = gate._engine_features(first)
    except ValueError:
        return (1, 0)
    promising = (features["offer"] and features["soundCp"] >= -100
                 and (features["marginCp"] >= 70 or features["persistentSacrifice"]))
    return (0 if promising else 1, -features["marginCp"])


def run(args) -> dict:
    key_text = os.environ.get("BRILLIANCY_QUEUE_KEY", "")
    try:
        key = base64.b64decode(key_text, validate=True)
    except ValueError as error:
        raise ValueError("QC_INCREMENTAL_KEY_INVALID") from error
    if len(key) != 32:
        raise ValueError("QC_INCREMENTAL_KEY_INVALID")
    for binary in args.engines:
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise ValueError("QC_INCREMENTAL_ENGINE_MISSING")
    if len({runner.sha256_file(binary) for binary in args.engines}) != 2:
        raise ValueError("QC_INCREMENTAL_ENGINES_NOT_INDEPENDENT")
    client, bucket = r2_client()
    source = R2Store(client, bucket, args.shard, key_text, "incremental").load()
    items = candidates_from_state(source)
    profiles = {"first": quality_profile(1_000_000, args.engines),
                "deep": quality_profile(5_000_000, args.engines)}
    cipher = AESGCM(key)
    started = time.monotonic()
    deadline = started + args.seconds - 180
    processed = Counter()
    with tempfile.TemporaryDirectory(prefix="chessdb-quality-") as directory:
        paths = {lane: Path(directory) / f"{lane}.sqlite3" for lane in profiles}
        with closing(runner.database(paths["first"])) as first_db, closing(
                runner.database(paths["deep"])) as deep_db:
            databases = {"first": first_db, "deep": deep_db}
            backups = {lane: QualityBackup(client, bucket, args.shard, key_text,
                                            f"incremental-{lane}") for lane in profiles}
            for lane in profiles:
                backups[lane].reconcile(databases[lane])
            engines = [chess.engine.SimpleEngine.popen_uci(str(path)) for path in args.engines]
            try:
                for engine in engines:
                    engine.configure({"Threads": 1, "Hash": 64})
                for lane, nodes in (("first", 1_000_000), ("deep", 5_000_000)):
                    db = databases[lane]
                    if lane == "deep":
                        order = sorted(items, key=lambda item: (
                            deep_priority(runner.cached_result(first_db, cipher, item,
                                                                profiles["first"])), item["id"]))
                    else:
                        order = items
                    unsynced = 0
                    try:
                        for item in order:
                            if time.monotonic() >= deadline or sum(processed.values()) >= args.max_new:
                                break
                            if runner.cached_result(db, cipher, item, profiles[lane]) is not None:
                                continue
                            if lane == "deep" and runner.cached_result(
                                    first_db, cipher, item, profiles["first"]) is None:
                                continue
                            outcome = review_or_reject(item, engines, nodes)
                            runner.save_result(db, cipher, item, profiles[lane], outcome)
                            processed[lane] += 1
                            unsynced += 1
                            if unsynced >= args.backup_every:
                                backups[lane].sync(db)
                                unsynced = 0
                    finally:
                        if unsynced:
                            backups[lane].sync(db)
            finally:
                for engine in engines:
                    engine.quit()
            tiers = Counter()
            for item in items:
                first = runner.cached_result(first_db, cipher, item, profiles["first"])
                deep = runner.cached_result(deep_db, cipher, item, profiles["deep"])
                tiers[gate.assess(first, deep)["tier"]] += 1
            return {"schemaVersion": 1, "shard": args.shard,
                    "sourceVersion": source["activeVersion"], "candidateCount": len(items),
                    "processedThisRun": dict(processed), "qualityTiers": dict(tiers),
                    "qualityRuleVersion": gate.RULE_VERSION,
                    "r2Checkpoint": "authenticated", "publicAutoPublish": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shard", type=int, choices=(0, 1), required=True)
    parser.add_argument("--engines", nargs=2, type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=3000)
    parser.add_argument("--max-new", type=int, default=100)
    parser.add_argument("--backup-every", type=int, default=5)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args()
    if not 600 <= args.seconds <= 3300 or args.max_new < 1 or args.backup_every < 1:
        parser.error("QC_INCREMENTAL_BUDGET_INVALID")
    summary = run(args)
    body = json.dumps(summary, ensure_ascii=False, sort_keys=True)
    if args.summary:
        args.summary.write_text(body + "\n", encoding="utf-8")
    print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
