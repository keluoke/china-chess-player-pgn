#!/usr/bin/env python3
"""Import a reviewer-supplied two-column Chess-Results event CSV locally.

The CSV may use an event title on a line by itself, followed by ``group,url``
rows.  It is converted into the local-only task override store; it never
contacts a source and never writes generated event data.  The target-plan
builder subsequently gives these rows a normal, reviewable capture lifecycle.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
OVERRIDES = ROOT / "local-data-center" / "collection" / "task-overrides.json"
RUN_STATE = ROOT / "local-data-center" / "collection" / "run-state.json"
TNR_URL = re.compile(r"https?://(?:(?:[a-z0-9-]+\.)*)chess-results\.com/tnr(\d{4,9})\.aspx", re.IGNORECASE)


def read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def csv_targets(path: Path) -> dict[str, dict[str, str]]:
    """Return one metadata record per valid Chess-Results URL in a CSV."""
    targets: dict[str, dict[str, str]] = {}
    section = ""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.reader(handle):
            label = str(row[0] if row else "").strip()
            value = str(row[1] if len(row) > 1 else "").strip()
            match = TNR_URL.search(value)
            if match:
                tournament_id = match.group(1)
                title_parts = [part for part in (section, label) if part]
                targets[tournament_id] = {
                    "displayName": " · ".join(title_parts) or f"人工补充 tnr{tournament_id}",
                    "sourceURL": value,
                }
            elif label and not value:
                section = label
    return targets


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def import_targets(
    path: Path, *, overrides_path: Path = OVERRIDES, run_state_path: Path = RUN_STATE,
) -> dict[str, int]:
    targets = csv_targets(path)
    overrides = read_json(overrides_path, {})
    if not isinstance(overrides, dict):
        overrides = {}
    additions = overrides.setdefault("additions", {})
    excluded = overrides.setdefault("excluded", {})
    if not isinstance(additions, dict):
        additions = overrides["additions"] = {}
    if not isinstance(excluded, dict):
        excluded = overrides["excluded"] = {}
    timestamp = now_iso()
    created = 0
    updated = 0
    for tournament_id, item in targets.items():
        previous = additions.get(tournament_id)
        additions[tournament_id] = {
            **(previous if isinstance(previous, dict) else {}),
            **item,
            "addedAt": (previous or {}).get("addedAt") if isinstance(previous, dict) else timestamp,
            "importedAt": timestamp,
            "importLabel": path.name,
        }
        if previous is None:
            created += 1
        else:
            updated += 1
        excluded.pop(tournament_id, None)
    overrides["schemaVersion"] = 1
    atomic_write_json(overrides_path, overrides)
    # A CSV import changes the selected task set.  The shared runner state is
    # deliberately reset here, exactly as the manual-review web UI does, so a
    # completed historical campaign can never skip the newly imported rows.
    atomic_write_json(run_state_path, {
        "schemaVersion": 1,
        "status": "pending",
        "nextBatchIndex": 0,
        "completedBatches": 0,
        "currentBatch": None,
        "currentTargets": [],
        "pid": None,
        "taskScope": "manual-only",
        "nextRetryAt": None,
        "lastOutcome": {
            "result": "tasks-imported",
            "message": f"已从 {path.name} 导入 {len(targets)} 个 TNR；下次运行将从第 1 批开始。",
        },
        "updatedAt": timestamp,
    })
    return {"targets": len(targets), "created": created, "updated": updated}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, help="two-column event/group and Chess-Results URL CSV")
    args = parser.parse_args()
    if not args.csv.is_file():
        raise SystemExit(f"输入 CSV 不存在：{args.csv}")
    print(json.dumps(import_targets(args.csv), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
