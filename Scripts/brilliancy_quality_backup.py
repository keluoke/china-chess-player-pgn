#!/usr/bin/env python3
"""Encrypted R2 backup of local brilliancy quality evidence, fail-closed on drift.

The completed candidate queue is never modified. This object lives under its
separate quality key but shares the queue's authenticated bucket/ETag guards.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sqlite3

from brilliancy_queue import PREFIX, R2Store, new_state


def record_key(candidate_id: str, profile: str) -> str:
    return hashlib.sha256(f"{candidate_id}:{profile}".encode()).hexdigest()


def local_records(db: sqlite3.Connection) -> dict:
    result = {}
    for candidate_id, input_hash, profile, grade, encrypted in db.execute(
            "SELECT candidate_id,input_hash,profile,grade,encrypted FROM results"):
        key = record_key(candidate_id, profile)
        result[key] = {"status": "complete", "version": profile, "candidates": [{
            "candidateId": candidate_id, "inputHash": input_hash, "profile": profile,
            "grade": grade, "encrypted": base64.b64encode(encrypted).decode("ascii"),
        }]}
    return result


def check_remote_record(key: str, row: dict) -> tuple:
    if row.get("status") != "complete" or len(row.get("candidates", [])) != 1:
        raise ValueError("QC_BACKUP_RECORD_INVALID")
    value = row["candidates"][0]
    candidate_id, profile = value.get("candidateId"), value.get("profile")
    if (not isinstance(candidate_id, str) or not candidate_id.startswith("br-")
            or not isinstance(profile, str) or key != record_key(candidate_id, profile)
            or row.get("version") != profile or value.get("grade") not in {"S", "A", "B", "C", "D"}
            or not isinstance(value.get("inputHash"), str) or len(value["inputHash"]) != 64):
        raise ValueError("QC_BACKUP_RECORD_INVALID")
    try:
        encrypted = base64.b64decode(value["encrypted"], validate=True)
    except (KeyError, ValueError) as error:
        raise ValueError("QC_BACKUP_RECORD_INVALID") from error
    if len(encrypted) < 29:
        raise ValueError("QC_BACKUP_RECORD_INVALID")
    return (candidate_id, value["inputHash"], profile, value["grade"], encrypted)


class QualityBackup:
    def __init__(self, client, bucket: str, shard: int, encryption_key: str,
                 lane: str = "first"):
        labels = {
            "first": "quality",
            "deep": "quality-deep",
            "incremental-first": "quality-incremental-first",
            "incremental-deep": "quality-incremental-deep",
        }
        if lane not in labels:
            raise ValueError("QC_BACKUP_LANE_INVALID")
        self.store = R2Store(client, bucket, shard, encryption_key)
        self.store.key = f"{PREFIX}{labels[lane]}-shard-{shard}.bin"
        self.remote = None

    def checked_records(self, records: dict) -> None:
        for key, record in records.items():
            candidate_id, _, profile, grade, encrypted = check_remote_record(key, record)
            try:
                outcome = json.loads(self.store.cipher.decrypt(
                    encrypted[:12], encrypted[12:], (candidate_id + profile).encode()))
            except Exception as error:
                raise ValueError("QC_BACKUP_EVIDENCE_INVALID") from error
            if outcome.get("candidateId") != candidate_id or outcome.get("grade") != grade:
                raise ValueError("QC_BACKUP_EVIDENCE_INVALID")

    def reconcile(self, db: sqlite3.Connection) -> int:
        """Restore remote-only rows; reject different local and remote evidence."""
        remote = self.store.load()
        rows = remote.get("records", {})
        local = local_records(db)
        self.checked_records(rows)
        self.checked_records(local)
        restored = 0
        with db:
            for key, record in rows.items():
                values = check_remote_record(key, record)
                if key in local:
                    if local[key] != record:
                        raise ValueError("QC_BACKUP_REMOTE_CONFLICT")
                    continue
                db.execute("INSERT INTO results VALUES (?,?,?,?,?)", values)
                restored += 1
        self.remote = remote
        return restored

    def sync(self, db: sqlite3.Connection) -> int:
        """Append local results only after authenticating the current remote base."""
        self.reconcile(db)
        local = local_records(db)
        self.checked_records(local)
        if self.remote["records"] == local:
            return 0
        state = new_state(self.store.shard)
        state["records"] = local
        self.store.save(state)
        self.remote = state
        return len(local)
