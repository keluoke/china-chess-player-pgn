#!/usr/bin/env python3
"""Import downloaded identity dispute contributions into presentation-disputes.csv."""

import argparse
import csv
import datetime as dt
import hashlib
import itertools
import json
import pathlib
import re
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
DISPUTES_CSV = ROOT / "data" / "manual" / "presentation-disputes.csv"
HEADERS = [
    "pair_hash", "member_a", "member_b", "status", "group_id",
    "submitted_at", "reason", "reviewed_by", "reviewed_at", "notes",
]


def clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def pair_hash(a: str, b: str) -> str:
    left, right = sorted([a, b])
    return hashlib.sha256(f"{left}|{right}".encode("utf-8")).hexdigest()[:16]


def import_dispute(payload: dict[str, Any], target: pathlib.Path = DISPUTES_CSV) -> dict[str, Any]:
    if payload.get("type") != "identity-dispute":
        raise ValueError("非 identity-dispute 类型的贡献 JSON")

    group_id = clean(payload.get("groupID"))
    member_ids = sorted({clean(value) for value in payload.get("memberIDs") or [] if clean(value)})
    if not group_id:
        raise ValueError("缺失 groupID")
    if len(member_ids) < 2:
        raise ValueError("身份质疑至少需要两个 memberID")
    scope = clean(payload.get("scope")) or "pair"
    if len(member_ids) > 2 and scope != "whole-group":
        raise ValueError("多成员质疑必须显式选择 scope=whole-group")
    if any(not re.fullmatch(r"(?:domestic-[0-9a-f]+|fide-\d+)", value) for value in member_ids):
        raise ValueError("memberID 格式非法")

    rows: list[dict[str, str]] = []
    if target.exists():
        with target.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = [{key: clean(row.get(key)) for key in HEADERS} for row in csv.DictReader(handle)]

    # A contribution against a two-member group creates one tombstone.  For a
    # larger display group, the user is disputing the group as presented, so
    # block every internal pair; transitive closure must not silently rebuild
    # the same card through a third member.
    existing = {clean(row.get("pair_hash")) for row in rows}
    submitted_at = clean(payload.get("created_at")) or dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    notes = clean(payload.get("notes"))
    added = 0
    for member_a, member_b in itertools.combinations(member_ids, 2):
        hash_value = pair_hash(member_a, member_b)
        supplied_hash = clean(payload.get("pairHash"))
        if len(member_ids) == 2 and supplied_hash and supplied_hash != hash_value:
            raise ValueError("pairHash 与 memberIDs 不匹配")
        if hash_value in existing:
            continue
        rows.append({
            "pair_hash": hash_value,
            "member_a": member_a,
            "member_b": member_b,
            "status": "disputed",
            "group_id": group_id,
            "submitted_at": submitted_at,
            "reason": "user-identity-dispute",
            "reviewed_by": "",
            "reviewed_at": "",
            "notes": notes,
        })
        existing.add(hash_value)
        added += 1

    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADERS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return {"groupID": group_id, "members": len(member_ids), "pairsAdded": added}

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=pathlib.Path)
    args = parser.parse_args()

    if not args.file.exists():
        raise SystemExit(f"文件不存在: {args.file}")

    payload = json.loads(args.file.read_text(encoding="utf-8"))
    try:
        result = import_dispute(payload)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    import sys
    sys.exit(main())
