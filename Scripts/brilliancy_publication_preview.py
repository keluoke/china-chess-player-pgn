#!/usr/bin/env python3
"""Read-only aggregate preview of chess-proven brilliancies across both queues.

This process cannot publish. It authenticates the encrypted source and quality
checkpoints, binds each result to the exact candidate body, and reports counts
only. The profile allowlist prevents old pilot evidence from silently acquiring
release authority after a rule or engine upgrade.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Scripts"))
import brilliancy_quality as quality
import brilliancy_publication_gate as gate
from brilliancy_quality_backup import QualityBackup, check_remote_record
from brilliancy_queue import R2Store

PROFILES = {
    "history": {
        "first": "47150673bf84fd4f8c295ea7012157b996a321b70c52488325aa388a2077ac3d",
        "deep": "d68defb945fc30d88ec48aea43da994b00ead36fc4d9c736fff4a964364ccdb3",
    },
    "incremental": {
        "first": "57446fbc5fc61e9d0e0e7c8c634afc8b2df78406c7fbd4169f6f58dde1b81025",
        "deep": "b832bfe86cd6932f718ebd1df7376071cafc29729c14dd3049f4f57bec866529",
    },
}
# An earlier local pilot used another first-pass profile. It is never accepted
# as the full-scan publication proof.
LEGACY_PROFILES = frozenset((
    "2f1836637ceae82309bf74f82ba6bbd537a2b470dbd4a30743fe5efaa45ef4cc",
))


def source_candidates(state: dict, lane: str) -> tuple[dict[str, dict], int]:
    version = state.get("activeVersion")
    if (not isinstance(version, str) or not version or state.get("shards") != 2
            or not isinstance(state.get("records"), dict)):
        raise ValueError("QC_PREVIEW_SOURCE_VERSION_INVALID")
    records = state["records"]
    if lane == "history":
        baseline = state.get("baselineGames")
        if not isinstance(baseline, list) or not baseline:
            raise ValueError("QC_PREVIEW_HISTORY_BASELINE_MISSING")
        incomplete = sum(records.get(game_id, {}).get("status") != "complete"
                         or records.get(game_id, {}).get("version") != version
                         for game_id in baseline)
        if set(records) != set(baseline):
            raise ValueError("QC_PREVIEW_HISTORY_SCOPE_DRIFT")
        if incomplete:
            raise ValueError(f"QC_PREVIEW_HISTORY_INCOMPLETE:{incomplete}")
    else:
        incomplete = sum(row.get("status") != "complete" or row.get("version") != version
                         for row in records.values())
    items: dict[str, dict] = {}
    for row in records.values():
        if row.get("status") != "complete" or row.get("version") != version:
            continue
        for item in row.get("candidates", []):
            candidate_id = item.get("id")
            if not isinstance(candidate_id, str) or not candidate_id.startswith("br-"):
                raise ValueError("QC_PREVIEW_CANDIDATE_ID_INVALID")
            if candidate_id in items and quality.digest(items[candidate_id]) != quality.digest(item):
                raise ValueError("QC_PREVIEW_CANDIDATE_CONFLICT")
            items[candidate_id] = item
    return items, incomplete


def checked_quality_rows(backup: QualityBackup, expected_profile: str) -> dict[str, tuple[str, dict]]:
    state = backup.store.load()
    rows = state.get("records", {})
    backup.checked_records(rows)
    result: dict[str, tuple[str, dict]] = {}
    for key, row in rows.items():
        candidate_id, input_hash, profile, _grade, encrypted = check_remote_record(key, row)
        if profile in LEGACY_PROFILES:
            continue
        if profile != expected_profile:
            raise ValueError("QC_PREVIEW_PROFILE_DRIFT")
        outcome = json.loads(backup.store.cipher.decrypt(
            encrypted[:12], encrypted[12:], (candidate_id + profile).encode()))
        if candidate_id in result:
            raise ValueError("QC_PREVIEW_QUALITY_DUPLICATE")
        result[candidate_id] = (input_hash, outcome)
    return result


def assess_shard(items: dict[str, dict], first: dict, deep: dict) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for candidate_id, item in items.items():
        one, five = first.get(candidate_id), deep.get(candidate_id)
        if one is None or five is None:
            counts["pending-independent-evidence"] += 1
            continue
        expected_hash = quality.digest(item)
        if one[0] != expected_hash or five[0] != expected_hash:
            counts["isolated-input-conflict"] += 1
            continue
        try:
            decision = gate.certify(item, one[1], five[1])
        except ValueError:
            counts["isolated-invalid-evidence"] += 1
        else:
            counts[decision["tier"]] += 1
    return dict(counts)


def inspect(client, bucket: str, encryption_key: str) -> dict:
    if bucket != "chess-data":
        raise ValueError("QC_PREVIEW_BUCKET_INVALID")
    combined: dict[str, str] = {}
    lanes = {}
    for lane in ("history", "incremental"):
        aggregate: Counter[str] = Counter()
        source_count = incomplete_count = 0
        for shard in (0, 1):
            source = R2Store(client, bucket, shard, encryption_key, lane)
            state = source.load()
            items, incomplete = source_candidates(state, lane)
            source_count += len(items)
            incomplete_count += incomplete
            for candidate_id, item in items.items():
                body_hash = quality.digest(item)
                if candidate_id in combined:
                    raise ValueError("QC_PREVIEW_CROSS_LANE_DUPLICATE")
                combined[candidate_id] = body_hash
            first = checked_quality_rows(QualityBackup(client, bucket, shard,
                                        encryption_key, "first" if lane == "history"
                                        else "incremental-first"), PROFILES[lane]["first"])
            deep = checked_quality_rows(QualityBackup(client, bucket, shard,
                                       encryption_key, "deep" if lane == "history"
                                       else "incremental-deep"), PROFILES[lane]["deep"])
            aggregate.update(assess_shard(items, first, deep))
        lanes[lane] = {"candidatePositions": source_count,
                       "pendingSourceGames": incomplete_count,
                       "certifiedEvidenceTiers": dict(aggregate)}
    return {"schemaVersion": 1, "ruleVersion": gate.RULE_VERSION,
            "publicAutoPublish": False, "uniqueCandidatePositions": len(combined),
            "lanes": lanes}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-file", type=Path, required=True)
    args = parser.parse_args()
    key_file = args.key_file
    if (key_file.is_symlink() or not key_file.is_file()
            or key_file.stat().st_uid != os.getuid() or key_file.stat().st_mode & 0o077):
        raise ValueError("QC_PREVIEW_KEY_FILE_EXPOSED")
    key = key_file.read_text().strip()
    if len(base64.b64decode(key, validate=True)) != 32:
        raise ValueError("QC_PREVIEW_KEY_INVALID")
    import boto3
    from botocore.config import Config
    settings = {name: os.environ.get(name, "") for name in
                ("R2_ENDPOINT", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")}
    if not all(settings.values()):
        raise ValueError("QC_PREVIEW_R2_CONFIG_MISSING")
    client = boto3.client("s3", endpoint_url=settings["R2_ENDPOINT"], region_name="auto",
                          aws_access_key_id=settings["R2_ACCESS_KEY_ID"],
                          aws_secret_access_key=settings["R2_SECRET_ACCESS_KEY"],
                          config=Config(connect_timeout=15, read_timeout=120, proxies={},
                                        retries={"total_max_attempts": 2}))
    print(json.dumps(inspect(client, settings["R2_BUCKET"], key),
                     ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
