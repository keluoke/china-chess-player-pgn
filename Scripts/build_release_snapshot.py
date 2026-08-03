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
import hashlib
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SNAPSHOT_JSON = ROOT / "docs" / "data" / "snapshot.json"


def file_fact(path: pathlib.Path) -> dict:
    if not path.is_file():
        return {"path": str(path.relative_to(ROOT)), "present": False}
    raw = path.read_bytes()
    fact = {
        "path": str(path.relative_to(ROOT)),
        "present": True,
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    if path.suffix == ".json":
        try:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                fact["schemaVersion"] = payload.get("schemaVersion")
                if isinstance(payload.get("events"), list):
                    fact["eventCount"] = len(payload["events"])
                elif isinstance(payload.get("events"), dict):
                    fact["eventCount"] = len(payload["events"])
            elif isinstance(payload, list):
                fact["rowCount"] = len(payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            fact["schemaVersion"] = "invalid"
    return fact


def input_facts() -> list[dict]:
    paths = [
        ROOT / "docs/data/registry/players.json",
        ROOT / "docs/data/registry/manifest.json",
        ROOT / "data/generated/person-observations.csv",
        ROOT / "data/generated/person-observations.meta.json",
        ROOT / "data/generated/event-completeness-report.json",
        ROOT / "data/generated/chess-results-player-events.csv",
        ROOT / "data/generated/chess-results-player-name-map.csv",
        ROOT / "data/generated/pgn-collection-status.json",
        ROOT / "data/generated/r2-object-receipts/events--chess-results.json",
        ROOT / "data/manual/domestic-player-sightings.csv",
        ROOT / "data/manual/player-identity-links.csv",
        ROOT / "data/manual/presentation-disputes.csv",
    ]
    return [file_fact(path) for path in paths]


def snapshot_document(snapshot_id: str, generated_at: str, facts: list[dict], steps: list[dict]) -> dict:
    return {
        "schemaVersion": 3,
        "snapshotId": snapshot_id,
        "generatedAt": generated_at,
        "producerVersion": "build-release-snapshot-v3",
        "inputs": facts,
        "steps": steps,
    }


def atomic_snapshot_bytes(raw: bytes | None) -> None:
    """Atomically install snapshot bytes, or remove a candidate when absent."""
    if raw is None:
        SNAPSHOT_JSON.unlink(missing_ok=True)
        return
    SNAPSHOT_JSON.parent.mkdir(parents=True, exist_ok=True)
    temporary = SNAPSHOT_JSON.with_name(f".{SNAPSHOT_JSON.name}.{os.getpid()}.tmp")
    temporary.write_bytes(raw)
    os.replace(temporary, SNAPSHOT_JSON)


def write_snapshot(payload: dict) -> None:
    atomic_snapshot_bytes(
        (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )


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
    steps.append(step([py, "Scripts/build_pgn_collection_status.py"], optional_script="Scripts/build_pgn_collection_status.py"))
    # CompletenessReport decides the publishable event set BEFORE any public
    # event projection or identity layer consumes event facts.
    steps.append(step([py, "Scripts/build_completeness_report.py"], optional_script="Scripts/build_completeness_report.py"))
    # The event roster projection resolves same-event FIDE IDs used by the
    # display-only identity candidate layer below.
    steps.append(step([py, "Scripts/build_event_details.py"], optional_script="Scripts/build_event_details.py"))

    # --- identity layers (observations BEFORE domestic sync, review §3.2) --
    steps.append(step([py, "Scripts/build_person_observations.py"], optional_script="Scripts/build_person_observations.py"))
    if not args.skip_domestic:
        steps.append(step([py, "Scripts/sync_domestic_players.py"], optional_script="Scripts/sync_domestic_players.py"))
        # Embedded FIDE-labelled roster rows act as an offline truth set.
        # A precision or hard-conflict regression aborts the whole snapshot
        # before any downstream search/API projection is rebuilt.
        steps.append(step([
            py, "Scripts/validate_identity_clustering.py",
        ], optional_script="Scripts/validate_identity_clustering.py"))
        steps.append(step([py, "Scripts/build_domestic_progressions.py"], optional_script="Scripts/build_domestic_progressions.py"))

    # --- public event projections --------------------------------------
    steps.append(step([py, "Scripts/build_event_catalog.py"], optional_script="Scripts/build_event_catalog.py"))
    steps.append(step([py, "Scripts/build_master_series_summary.py"], optional_script="Scripts/build_master_series_summary.py"))
    steps.append(step([py, "Scripts/build_player_participation.py"], optional_script="Scripts/build_player_participation.py"))

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

    # The consistency gate reads the canonical path, so install a candidate
    # there temporarily. If the gate or final write fails, restore the exact
    # previous snapshot bytes; a failed rebuild must never leave a new,
    # unverified snapshot id in the worktree.
    facts = input_facts()
    generated_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    previous_snapshot = SNAPSHOT_JSON.read_bytes() if SNAPSHOT_JSON.is_file() else None
    try:
        write_snapshot(snapshot_document(sid, generated_at, facts, steps))
        steps.append(step([py, "Scripts/validate_snapshot_consistency.py"]))
        write_snapshot(snapshot_document(sid, generated_at, facts, steps))
    except BaseException:
        atomic_snapshot_bytes(previous_snapshot)
        raise
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
