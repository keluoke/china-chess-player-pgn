#!/usr/bin/env python3
"""Single-entry derived-data rebuild under one atomic snapshot id.

Every derived artifact is rebuilt in dependency order (review §3.2) under one
exported ``SNAPSHOT_ID``:

    completeness → person observations → domestic identity → projections
    → aggregates → API → validators → snapshot-consistency gate

Builders report structured outcomes: their stdout JSON may carry ``skipped``
(kept committed output) or ``reprojected`` (manifest re-derived from committed
public files); both are recorded verbatim in ``docs/data/snapshot.json`` —
never silently upgraded to "built" (review §3.1).

Atomic-switch semantics: if any step fails — including the final check that
every public derived manifest references this snapshot id — the process
aborts with a non-zero exit and nothing is committed, so the previously
committed snapshot keeps serving as a whole.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SNAPSHOT_JSON = ROOT / "docs" / "data" / "snapshot.json"


def step(cmd: list[str], *, optional_script: str | None = None) -> dict:
    """Run one build step; abort the snapshot on failure."""
    if optional_script and not (ROOT / optional_script).is_file():
        return {"command": " ".join(cmd), "status": "skipped-missing"}
    started = dt.datetime.now(dt.timezone.utc)
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    if result.returncode != 0:
        print(f"SNAPSHOT ABORTED at: {' '.join(cmd)} (exit {result.returncode})", file=sys.stderr)
        raise SystemExit(result.returncode or 1)
    status = "built"
    detail = None
    # Builders speak a structured convention on their last stdout line.
    for line in reversed(result.stdout.strip().splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            break
        if "skipped" in payload:
            status, detail = "skipped", payload.get("skipped")
        elif "reprojected" in payload:
            status, detail = "reprojected", f"manifest re-derived ({payload.get('reprojected')} events)"
        break
    return {
        "command": " ".join(cmd),
        "status": status,
        **({"detail": detail} if detail else {}),
        "seconds": round((dt.datetime.now(dt.timezone.utc) - started).total_seconds(), 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-registry-aliases", action="store_true")
    parser.add_argument("--skip-domestic", action="store_true")
    args = parser.parse_args()

    from snapshot_context import snapshot_id
    sid = snapshot_id()
    os.environ["SNAPSHOT_ID"] = sid
    print(f"snapshotId={sid}")

    py = sys.executable
    steps: list[dict] = []

    # --- registry layer -------------------------------------------------
    if not args.skip_registry_aliases and (ROOT / "docs/data/registry/players.json").is_file():
        steps.append(step([py, "Scripts/apply_aliases_to_registry.py"]))
    steps.append(step([
        py, "Scripts/validate_registry_release.py",
        "--registry", "docs/data/registry",
        "--corrections", "data/community/name-corrections.csv",
    ]))

    # --- PGN / event fact layers ---------------------------------------
    steps.append(step([py, "Scripts/sync_static_pgn.py"]))
    steps.append(step([py, "Scripts/build_static_player_pgn.py"]))
    # CompletenessReport decides the publishable event set BEFORE any public
    # event projection or identity layer consumes event facts.
    steps.append(step([py, "Scripts/build_completeness_report.py"], optional_script="Scripts/build_completeness_report.py"))

    # --- identity layers (observations BEFORE domestic sync, review §3.2) --
    steps.append(step([py, "Scripts/build_person_observations.py"], optional_script="Scripts/build_person_observations.py"))
    if not args.skip_domestic:
        steps.append(step([py, "Scripts/sync_domestic_players.py"], optional_script="Scripts/sync_domestic_players.py"))
        steps.append(step([py, "Scripts/build_domestic_progressions.py"], optional_script="Scripts/build_domestic_progressions.py"))

    # --- public event projections --------------------------------------
    steps.append(step([py, "Scripts/build_event_details.py"], optional_script="Scripts/build_event_details.py"))
    steps.append(step([py, "Scripts/build_event_catalog.py"], optional_script="Scripts/build_event_catalog.py"))

    # --- maintainer queues / audits ------------------------------------
    steps.append(step([py, "Scripts/archive_rating_observations.py"], optional_script="Scripts/archive_rating_observations.py"))
    steps.append(step([py, "Scripts/build_domestic_event_queue.py"], optional_script="Scripts/build_domestic_event_queue.py"))
    steps.append(step([py, "Scripts/build_data_quality_audit.py"], optional_script="Scripts/build_data_quality_audit.py"))
    steps.append(step([py, "Scripts/reconcile_pgn_sources.py", "--write-audit"], optional_script="Scripts/reconcile_pgn_sources.py"))

    # --- user-facing aggregates ----------------------------------------
    steps.append(step([py, "Scripts/build_search_bootstrap.py"]))
    steps.append(step([py, "Scripts/build_public_metrics.py"]))
    steps.append(step([py, "Scripts/build_leaderboards.py"]))
    steps.append(step([py, "Scripts/build_api.py"], optional_script="Scripts/build_api.py"))
    steps.append(step([py, "Scripts/build_changelog.py"], optional_script="Scripts/build_changelog.py"))
    steps.append(step([py, "Scripts/build_dashboard.py"], optional_script="Scripts/build_dashboard.py"))

    # --- gates ----------------------------------------------------------
    steps.append(step([py, "Scripts/validate_registry_authority.py"]))
    steps.append(step([py, "Scripts/validate_public_metrics.py"]))
    steps.append(step([py, "Scripts/validate_public_privacy.py"]))

    # Snapshot is recorded FIRST (so the consistency gate can include it),
    # then verified: every public derived manifest must reference this id.
    SNAPSHOT_JSON.write_text(json.dumps({
        "schemaVersion": 2,
        "snapshotId": sid,
        "generatedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "steps": steps,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    steps.append(step([py, "Scripts/validate_snapshot_consistency.py"]))

    # Re-record with the gate outcome included.
    SNAPSHOT_JSON.write_text(json.dumps({
        "schemaVersion": 2,
        "snapshotId": sid,
        "generatedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "steps": steps,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "snapshotId": sid,
        "steps": len(steps),
        "built": sum(1 for s in steps if s["status"] == "built"),
        "skipped": sum(1 for s in steps if s["status"].startswith("skipped")),
        "reprojected": sum(1 for s in steps if s["status"] == "reprojected"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
