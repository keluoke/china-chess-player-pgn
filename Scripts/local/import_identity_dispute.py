#!/usr/bin/env python3
"""Import downloaded identity dispute contributions into presentation-disputes.csv."""

import argparse
import csv
import datetime as dt
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
DISPUTES_CSV = ROOT / "data" / "community" / "presentation-disputes.csv"

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=pathlib.Path)
    args = parser.parse_args()

    if not args.file.exists():
        raise SystemExit(f"文件不存在: {args.file}")

    payload = json.loads(args.file.read_text(encoding="utf-8"))
    if payload.get("type") != "identity-dispute":
        raise SystemExit("非 identity-dispute 类型的贡献 JSON")

    group_id = payload.get("groupID")
    member_ids = payload.get("memberIDs") or []
    notes = payload.get("notes") or ""

    if not group_id:
        raise SystemExit("缺失 groupID")

    # CSV headers
    headers = ["group_id", "member_id", "disputed_at", "notes"]
    rows = []
    if DISPUTES_CSV.exists():
        with DISPUTES_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)

    today = dt.date.today().isoformat()
    added = 0
    for mid in member_ids:
        # Avoid duplicates
        if any(r.get("group_id") == group_id and r.get("member_id") == mid for r in rows):
            continue
        rows.append({
            "group_id": group_id,
            "member_id": mid,
            "disputed_at": today,
            "notes": notes,
        })
        added += 1

    # Write back
    DISPUTES_CSV.parent.mkdir(parents=True, exist_ok=True)
    with DISPUTES_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    print(f"Successfully imported identity dispute: added {added} records for group {group_id}")
    return 0

if __name__ == "__main__":
    import sys
    sys.exit(main())
