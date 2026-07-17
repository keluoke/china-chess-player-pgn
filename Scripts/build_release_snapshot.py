#!/usr/bin/env python3
"""Single-entry derived-data rebuild under one atomic snapshot id.

Every derived artifact (registry projections, by-player packs, completeness
report, event details/catalog, search, metrics, leaderboards, API, dashboard)
is rebuilt in dependency order under one exported ``SNAPSHOT_ID``.  Builders
stamp that id into their manifests; ``docs/data/snapshot.json`` records the
snapshot only after every step (including validators) succeeded.

Atomic-switch semantics: this script never partially publishes.  If any step
fails, the process aborts with a non-zero exit and nothing is committed, so
the previously committed snapshot keeps serving.  The Git commit that follows
a successful run is the atomic switch (plan §5.4).

Usage: python3 Scripts/build_release_snapshot.py [--skip-domestic]
       [--skip-registry-aliases]
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
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        print(f"SNAPSHOT ABORTED at: {' '.join(cmd)} (exit {result.returncode})", file=sys.stderr)
        raise SystemExit(result.returncode or 1)
    return {
        "command": " ".join(cmd),
        "status": "ok",
        "seconds": round((dt.datetime.now(dt.timezone.utc) - started).total_seconds(), 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-registry-aliases", action="store_true")
    parser.add_argument("--skip-domestic", action="store_true")
    args = parser.parse_args()

    # One snapshot id for the entire rebuild; children inherit it via env.
    from snapshot_context import snapshot_id
    sid = snapshot_id()
    os.environ["SNAPSHOT_ID"] = sid
    print(f"snapshotId={sid}")

    py = sys.executable
    steps: list[dict] = []

    # --- registry / identity layers ------------------------------------
    if not args.skip_registry_aliases and (ROOT / "docs/data/registry/players.json").is_file():
        steps.append(step([py, "Scripts/apply_aliases_to_registry.py"]))
    steps.append(step([
        py, "Scripts/validate_registry_release.py",
        "--registry", "docs/data/registry",
        "--corrections", "data/community/name-corrections.csv",
    ]))
    if not args.skip_domestic:
        steps.append(step([py, "Scripts/sync_domestic_players.py"], optional_script="Scripts/sync_domestic_players.py"))

    # --- PGN / event fact layers ---------------------------------------
    steps.append(step([py, "Scripts/sync_static_pgn.py"]))
    steps.append(step([py, "Scripts/build_static_player_pgn.py"]))
    # CompletenessReport decides the publishable event set BEFORE any public
    # event projection is rebuilt (publication-gate fix, plan §9.1).
    steps.append(step([py, "Scripts/build_completeness_report.py"], optional_script="Scripts/build_completeness_report.py"))
    steps.append(step([py, "Scripts/build_event_details.py"], optional_script="Scripts/build_event_details.py"))
    steps.append(step([py, "Scripts/build_event_catalog.py"], optional_script="Scripts/build_event_catalog.py"))

    # --- fact-layer projections & maintainer queues --------------------
    steps.append(step([py, "Scripts/build_person_observations.py"], optional_script="Scripts/build_person_observations.py"))
    steps.append(step([py, "Scripts/build_identity_candidates.py"], optional_script="Scripts/build_identity_candidates.py"))
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

    # --- gates: a snapshot that fails validation is never recorded -----
    steps.append(step([py, "Scripts/validate_registry_authority.py"]))
    steps.append(step([py, "Scripts/validate_public_metrics.py"]))
    steps.append(step([py, "Scripts/validate_public_privacy.py"]))

    SNAPSHOT_JSON.write_text(json.dumps({
        "schemaVersion": 1,
        "snapshotId": sid,
        "generatedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "steps": steps,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"snapshotId": sid, "steps": len(steps)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
