#!/usr/bin/env python3
"""Plan and run full local capture for four reviewed Chess-Results series.

The source list is deliberately offline: it is built only from the committed
public event catalogue and the reviewed chess-association master mapping.  It
never discovers TNRs on the network, and it delegates every actual source
request to the policy-enforced ``Scripts/local/refresh.sh event-queue`` entry
point in batches of at most ten events.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
CENTER = ROOT / "local-data-center"
PUBLIC_EVENTS = ROOT / "docs" / "data" / "index" / "public-events.json"
DETAILS = ROOT / "data" / "generated" / "chess-results-event-details"
MASTER_GROUPS = ROOT / "data" / "community" / "master-tournament-groups.csv"
PLAN_PATH = CENTER / "collection" / "target-plan.json"
MISSING_PATH = CENTER / "collection" / "missing-tnr.csv"
LOG_PATH = CENTER / "collection" / "capture.log"
RUN_STATE_PATH = CENTER / "collection" / "run-state.json"
TASK_OVERRIDES_PATH = CENTER / "collection" / "task-overrides.json"
CAPTURE_STATE_PATH = (
    Path.home() / "Library" / "Application Support" / "ChinaChessPlayerPGN" / "chess-results" / "capture-state.json"
)
EVENT_PGN_ARCHIVE = ROOT / "data" / "generated" / "chess-results-event-pgn"
LOCK = ROOT / ".git" / "index.lock"
SERIES = {
    "chess-association-master": "全国国际象棋棋协大师赛",
    "lichengzhi-cup": "全国国际象棋青少年锦标赛（个人）暨李成智杯",
    "world-youth": "世界青少年国际象棋锦标赛",
    "asian-youth": "亚洲青少年国际象棋锦标赛",
}
TNR = re.compile(r"(?:tnr)?(\d{4,9})", re.IGNORECASE)


def read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def normalize_tnr(value: Any) -> str:
    match = TNR.search(str(value or ""))
    return match.group(1) if match else ""


def canonical_source_url(tournament_id: str) -> str:
    return f"https://chess-results.com/tnr{tournament_id}.aspx?lan=1"


def task_overrides() -> dict[str, Any]:
    payload = read_json(TASK_OVERRIDES_PATH, {})
    return payload if isinstance(payload, dict) else {}


def rows() -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    payload = read_json(PUBLIC_EVENTS, {})
    selected = [item for item in payload.get("events", []) if item.get("series") in SERIES]
    master_by_tnr: dict[str, dict[str, str]] = {}
    with MASTER_GROUPS.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            tnr = normalize_tnr(row.get("tournament_id"))
            if tnr:
                master_by_tnr[tnr] = row
    targets: dict[str, dict[str, Any]] = {}
    missing: list[dict[str, str]] = []
    overrides = task_overrides()
    excluded = {str(item) for item in (overrides.get("excluded") or {})}
    today = dt.date.today().isoformat()
    for item in selected:
        tnr = normalize_tnr(item.get("tournamentID"))
        series = str(item.get("series") or "")
        if not tnr:
            missing.append({
                "series": SERIES.get(series, series),
                "seriesKey": series,
                "date": str(item.get("date") or ""),
                "displayName": str(item.get("displayName") or item.get("name") or ""),
                "eventID": str(item.get("id") or ""),
                "reason": "catalog-event-has-no-tnr",
            })
            continue
        if tnr in excluded:
            continue
        existing = targets.get(tnr)
        record = {
            "tournamentID": tnr,
            "series": series,
            "seriesLabel": SERIES[series],
            "date": str(item.get("date") or ""),
            "displayName": str(item.get("displayName") or item.get("name") or f"tnr{tnr}"),
            "catalogDetailStatus": str(item.get("detailStatus") or "missing-detail"),
            "existingRecord": (DETAILS / f"tnr{tnr}.json").exists(),
            "future": bool(item.get("date") and str(item["date"]) > today),
            "sourceURL": str(item.get("url") or ""),
            "masterMapping": bool(tnr in master_by_tnr),
        }
        # One TNR is one source target.  If a catalogue duplication exists,
        # preserve the most useful display metadata rather than requesting it
        # twice.
        if existing is None or (not existing.get("date") and record.get("date")):
            targets[tnr] = record
    for tnr, item in (overrides.get("additions") or {}).items():
        tnr = normalize_tnr(tnr)
        if not tnr or tnr in excluded:
            continue
        info = item if isinstance(item, dict) else {}
        targets.setdefault(tnr, {
            "tournamentID": tnr,
            "series": "manual-review",
            "seriesLabel": "人工补充待抓任务",
            "date": str(info.get("date") or ""),
            "displayName": str(info.get("displayName") or f"人工补充 tnr{tnr}"),
            "catalogDetailStatus": "manual-addition",
            "existingRecord": (DETAILS / f"tnr{tnr}.json").exists(),
            "future": False,
            "sourceURL": str(info.get("sourceURL") or canonical_source_url(tnr)),
            "masterMapping": False,
        })
    result = sorted(targets.values(), key=lambda item: (item["future"], item["series"], item["date"], item["tournamentID"]))
    missing.sort(key=lambda item: (item["seriesKey"], item["date"], item["displayName"]))
    return result, missing


def write_plan() -> dict[str, Any]:
    targets, missing = rows()
    by_series: dict[str, dict[str, int]] = {}
    for series in SERIES:
        group = [item for item in targets if item["series"] == series]
        by_series[series] = {
            "knownTnr": len(group),
            "readyNow": sum(not item["future"] for item in group),
            "future": sum(item["future"] for item in group),
            "existingStructuredRecord": sum(item["existingRecord"] for item in group),
        }
    plan = {
        "schemaVersion": 1,
        "generatedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "purpose": "Offline target plan for maintainer-local, full-data Chess-Results capture.",
        "series": SERIES,
        "totals": {
            "knownTnr": len(targets),
            "readyNow": sum(not item["future"] for item in targets),
            "future": sum(item["future"] for item in targets),
            "existingStructuredRecord": sum(item["existingRecord"] for item in targets),
            "missingTnr": len(missing),
        },
        "bySeries": by_series,
        "targets": targets,
    }
    PLAN_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLAN_PATH.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with MISSING_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["series", "seriesKey", "date", "displayName", "eventID", "reason"])
        writer.writeheader()
        writer.writerows(missing)
    return plan


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def latest_run_outcome() -> dict[str, Any]:
    runs = Path.home() / "Library" / "Application Support" / "ChinaChessPlayerPGN" / "runs"
    candidates = sorted(runs.glob("*/run.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not candidates:
        return {}
    payload = read_json(candidates[0], {})
    return {
        "runId": payload.get("runId"),
        "result": payload.get("result"),
        "errorCode": payload.get("errorCode"),
        "message": payload.get("message"),
        "finishedAt": payload.get("finishedAt"),
    }


def retry_after_for(targets: list[str]) -> str | None:
    """Return the latest active *transient source* backoff in a batch.

    ``nextRetryAt`` is also used as the review date for quarantined structural
    failures such as an empty or unsupported event.  Those targets are already
    isolated by the collector and must not hold the unrelated targets in a
    long-running batch hostage.  Only ``retry-wait`` represents an actual
    source/network retry deadline.
    """
    now = dt.datetime.now(dt.timezone.utc)
    events = read_json(CAPTURE_STATE_PATH, {}).get("events", {})
    active: list[str] = []
    for tournament_id in targets:
        event = events.get(str(tournament_id), {})
        if event.get("status") != "retry-wait":
            continue
        value = str(event.get("nextRetryAt") or "")
        if not value:
            continue
        try:
            retry_at = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
        if retry_at.astimezone(dt.timezone.utc) > now:
            active.append(value)
    return max(active) if active else None


def retry_ready_targets(allowed_targets: set[str]) -> list[str]:
    """Return planned TNRs whose persisted source backoff has elapsed.

    A documented partial batch must not silently strand its failed target
    while the long-running series runner advances through later batches.  The
    next normal batch first gives each due, planned retry a cache-reusing
    chance; targets still in backoff remain untouched.
    """
    now = dt.datetime.now(dt.timezone.utc)
    events = read_json(CAPTURE_STATE_PATH, {}).get("events", {})
    ready: list[str] = []
    for tournament_id in sorted(allowed_targets, key=lambda value: int(value)):
        event = events.get(tournament_id, {})
        if event.get("status") != "retry-wait":
            continue
        value = str(event.get("nextRetryAt") or "")
        try:
            retry_at = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
        if retry_at.astimezone(dt.timezone.utc) <= now:
            ready.append(tournament_id)
    return ready


def run_due_retries(
    state: dict[str, Any],
    *,
    allowed_targets: set[str],
    batch_index: int,
    log: Any,
) -> int | None:
    """Run at most one due transient-retry group through the safe local entrypoint.

    Returns ``None`` when no retry is due, ``0`` after a documented successful
    retry (including partial exit code 4), and the nonzero launcher exit code
    when the retry itself could not be started.  This is intentionally usable
    after the normal plan is exhausted: complete main batches must not strand
    their PGN/source retries forever.
    """
    due_retries = retry_ready_targets(allowed_targets)[:10]
    if not due_retries:
        return None
    label = f"before batch {batch_index}" if batch_index else "after the completed plan"
    log.write(f"retry {label}: {' '.join(due_retries)}\n")
    log.flush()
    state.update({
        "status": "running", "currentBatch": batch_index or None,
        "currentTargets": due_retries, "pid": os.getpid(),
        "updatedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
    })
    write_json(RUN_STATE_PATH, state)
    command = ["bash", "Scripts/local/refresh.sh", "event-queue", "--no-push", "--", *due_retries]
    process = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=False)
    if process.returncode not in (0, 4):
        log.write(f"STOP retry {label}: refresh exited {process.returncode}; inspect this log before retrying.\n")
        state.update({
            "status": "stopped", "currentBatch": None, "pid": None,
            "lastOutcome": latest_run_outcome(),
            "updatedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        })
        write_json(RUN_STATE_PATH, state)
        return process.returncode
    # local-data-center (second control plane) retired 2026-07; the
    # CompletenessReport is the single completeness surface now.
    subprocess.run([sys.executable, "Scripts/build_completeness_report.py"], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
    return 0


def source_explicitly_omits_pgn(tournament_id: str) -> bool:
    """Check the locally captured pairing evidence for a genuine PGN source gap."""
    payload = read_json(DETAILS / f"tnr{tournament_id}.json", {})
    pairings: list[dict[str, Any]] = []
    for round_payload in payload.get("rounds") or []:
        for pairing in round_payload.get("pairings") or []:
            white = pairing.get("white") or {}
            black = pairing.get("black") or {}
            names = {str(white.get("name") or "").strip().casefold(), str(black.get("name") or "").strip().casefold()}
            if names & {"bye", "not paired"}:
                continue
            if white.get("playerNo") and black.get("playerNo"):
                pairings.append(pairing)
    return bool(pairings) and not any(
        bool(pairing.get("hasPGN")) or bool(str(pairing.get("pgnURL") or "").strip())
        for pairing in pairings
    )


def batch_capture_completed(targets: list[str], run_id: Any) -> bool:
    """Return true only when this exact run completed every target locally.

    ``refresh.sh event-queue`` performs collection before local release and
    delivery.  A failure in a later release step must never send the target
    runner back to the source for an already captured batch.  Checking both
    the status and the private run ID avoids mistaking an old successful
    capture for this run's result.
    """
    if not run_id or not targets:
        return False
    events = read_json(CAPTURE_STATE_PATH, {}).get("events", {})
    marker = f"/runs/{run_id}"
    for tournament_id in targets:
        event = events.get(str(tournament_id), {})
        if event.get("status") != "complete" or marker not in str(event.get("runPrivateRoot") or ""):
            return False
        # Event pages finish before complete PGN archives.  A missing archive
        # only advances when the captured pairing pages are themselves proof
        # that the source publishes no PGN; this is a recorded source gap,
        # never an empty synthetic archive.
        archive = EVENT_PGN_ARCHIVE / f"tnr{tournament_id}.pgn"
        if (not archive.is_file() or archive.stat().st_size == 0) and not source_explicitly_omits_pgn(tournament_id):
            return False
    return True


def recover_completed_batch(state: dict[str, Any], batches: list[list[dict[str, Any]]]) -> bool:
    """Advance a checkpoint whose collection completed before release failed."""
    if state.get("status") != "stopped":
        return False
    index = int(state.get("nextBatchIndex") or 0)
    saved_targets = [str(item) for item in state.get("currentTargets") or []]
    # A plan can gain newly scheduled events between days.  Prefer the saved
    # target IDs over a numerical batch index, whose boundaries may then have
    # shifted, so a finished batch is never accidentally replayed.
    for candidate, batch in enumerate(batches):
        if [item["tournamentID"] for item in batch] == saved_targets:
            index = candidate
            break
    if not (0 <= index < len(batches)):
        return False
    targets = [item["tournamentID"] for item in batches[index]]
    outcome = dict(state.get("lastOutcome") or {})
    if not batch_capture_completed(targets, outcome.get("runId")):
        return False
    state.update({
        "nextBatchIndex": index + 1,
        "completedBatches": max(int(state.get("completedBatches") or 0), index + 1),
        "currentBatch": None,
        "currentTargets": [],
        "pid": None,
        "captureCompletedDespiteReleaseFailure": True,
        "lastOutcome": {
            "runId": outcome.get("runId"),
            "result": "capture-complete-release-blocked",
            "errorCode": outcome.get("errorCode"),
            "message": f"第 {index + 1} 批 {len(targets)} 个 TNR 已采集完成；后续发布步骤异常已记录，不会重复回抓本批。",
            "finishedAt": outcome.get("finishedAt"),
        },
        "updatedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
    })
    return True


def legacy_resume_batch() -> int:
    """Recover the interrupted batch from old logs written before run-state."""
    try:
        text = LOG_PATH.read_text(encoding="utf-8")
    except OSError:
        return 0
    matches = list(re.finditer(r"STOP batch (\d+):", text))
    return max(0, int(matches[-1].group(1)) - 1) if matches else 0


def run_state(targets: list[dict[str, Any]], batches: list[list[dict[str, Any]]]) -> tuple[dict[str, Any], int]:
    ids = [item["tournamentID"] for item in targets]
    signature = hashlib.sha256("\n".join(ids).encode()).hexdigest()
    saved = read_json(RUN_STATE_PATH, {})
    # A backoff timestamp is an eligibility guard, not a terminal state.  If a
    # previous process exited while waiting, clear that stale marker as soon as
    # the deadline has passed so a later resume is not visually or logically
    # locked by an old checkpoint.
    if saved.get("status") == "waiting" and not retry_after_for([str(item) for item in saved.get("currentTargets") or []]):
        saved.update({
            "status": "stopped",
            "nextRetryAt": None,
            "lastOutcome": {
                "result": "ready",
                "message": "来源退避期已结束；可以从保存的批次继续。",
            },
            "updatedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        })
        write_json(RUN_STATE_PATH, saved)
    if saved.get("status") == "pending":
        saved.update(targetSignature=signature, targetCount=len(targets), batchCount=len(batches))
        write_json(RUN_STATE_PATH, saved)
        return saved, 0
    # Reconcile before comparing signatures: a day boundary can legitimately
    # add planned TNRs, while the exact failed batch remains recoverable.
    if saved.get("status") == "stopped" and recover_completed_batch(saved, batches):
        saved.update(targetSignature=signature, targetCount=len(targets), batchCount=len(batches))
        write_json(RUN_STATE_PATH, saved)
        return saved, max(0, min(int(saved.get("nextBatchIndex") or 0), len(batches)))
    # A fully consumed plan is still a valid checkpoint: later launches may
    # have only due PGN/source retries to run.  Omitting ``completed`` here
    # fell through to legacy log recovery, which could rewind a finished plan
    # to an unrelated historical STOP marker.
    if saved.get("targetSignature") == signature and saved.get("status") in {"running", "stopped", "waiting", "completed"}:
        if saved.get("status") == "stopped" and not saved.get("lastOutcome"):
            saved["lastOutcome"] = latest_run_outcome()
        if recover_completed_batch(saved, batches):
            write_json(RUN_STATE_PATH, saved)
        next_batch = max(0, min(int(saved.get("nextBatchIndex") or 0), len(batches)))
        return saved, next_batch
    # A persisted checkpoint for another task signature must never inherit a
    # numerical position from the shared historical log.  For example, a new
    # 16-batch reviewer import could otherwise see an old "STOP batch 16"
    # from the main campaign and be falsely marked complete without making a
    # single source request.  Legacy recovery is only safe before any
    # checkpoint has ever been persisted.
    resumed_batch = legacy_resume_batch() if not saved else 0
    return {
        "schemaVersion": 1,
        "targetSignature": signature,
        "targetCount": len(targets),
        "batchCount": len(batches),
        "createdAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "status": "stopped" if resumed_batch else "pending",
        "nextBatchIndex": resumed_batch,
        "completedBatches": resumed_batch,
        "lastOutcome": latest_run_outcome() if resumed_batch else None,
        "note": "A batch is advanced only after refresh.sh returns success or documented partial success.",
    }, resumed_batch


def selected_targets(
    plan: dict[str, Any], *, refresh_existing: bool, include_future: bool, manual_only: bool,
) -> list[dict[str, Any]]:
    return [
        item for item in plan["targets"]
        if (include_future or not item["future"])
        and (refresh_existing or not item["existingRecord"])
        and (not manual_only or item.get("series") == "manual-review")
    ]


def run(plan: dict[str, Any], *, refresh_existing: bool, include_future: bool, manual_only: bool, limit: int) -> int:
    if LOCK.exists():
        raise SystemExit(
            f"GIT_INDEX_LOCK_STALE: 检测到 {LOCK}。请先确认没有 git 进程后删除该残留锁，再重新启动一键抓取。"
        )
    selected = selected_targets(
        plan, refresh_existing=refresh_existing, include_future=include_future, manual_only=manual_only,
    )
    if limit:
        selected = selected[:limit]
    selected_ids = {str(item["tournamentID"]) for item in selected}
    batches = [selected[index:index + 10] for index in range(0, len(selected), 10)]
    state, start_batch = run_state(selected, batches)
    state["taskScope"] = "manual-only" if manual_only else "full-plan"
    write_json(RUN_STATE_PATH, state)
    if start_batch >= len(batches):
        with LOG_PATH.open("a", encoding="utf-8") as log:
            log.write(f"\n[{dt.datetime.now().isoformat(timespec='seconds')}] plan complete; checking due retries\n")
            retry_result = run_due_retries(state, allowed_targets=selected_ids, batch_index=0, log=log)
            if retry_result not in (None, 0):
                return retry_result
        state.update(
            status="completed", nextBatchIndex=len(batches), completedBatches=len(batches),
            currentBatch=None, currentTargets=[], pid=None,
            updatedAt=dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        )
        write_json(RUN_STATE_PATH, state)
        print(json.dumps({"status": "completed", "batches": len(batches), "retried": retry_result == 0}, ensure_ascii=False))
        return 0
    with LOG_PATH.open("a", encoding="utf-8") as log:
        log.write(f"\n[{dt.datetime.now().isoformat(timespec='seconds')}] targets={len(selected)} batches={len(batches)} startBatch={start_batch + 1} refreshExisting={refresh_existing} includeFuture={include_future}\n")
        for batch_index in range(start_batch, len(batches)):
            index = batch_index + 1
            batch = batches[batch_index]
            ids = [item["tournamentID"] for item in batch]
            retry_at = retry_after_for(ids)
            if retry_at:
                message = f"第 {index} 批处于来源退避期，将在 {retry_at} 后才允许续抓。"
                log.write(f"WAIT batch {index}: {message}\n")
                state.update({
                    "status": "waiting", "nextBatchIndex": batch_index, "currentBatch": None,
                    "currentTargets": ids, "pid": None, "nextRetryAt": retry_at,
                    "lastOutcome": {"result": "waiting", "errorCode": "SOURCE_CIRCUIT_OPEN", "message": message},
                    "updatedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
                })
                write_json(RUN_STATE_PATH, state)
                return 0
            # Retry elapsed transient failures before consuming another normal
            # batch.  This keeps the full-TNR objective convergent while still
            # respecting the per-target retry deadline in capture-state.
            retry_result = run_due_retries(state, allowed_targets=selected_ids, batch_index=index, log=log)
            if retry_result not in (None, 0):
                return retry_result
            state.update({
                "status": "running", "nextBatchIndex": batch_index, "currentBatch": index,
                "currentTargets": ids, "pid": os.getpid(),
                "updatedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
            })
            write_json(RUN_STATE_PATH, state)
            # Existing structured events are reused by the collector.  A
            # blanket --overwrite here turned a resume into needless source
            # traffic; explicit parser repairs use offline --replay instead.
            # The local data-centre run is intentionally offline-first: keep
            # the validated manifest and local commit, but never make GitHub
            # delivery part of a long-running source capture.  Delivery is a
            # separate, explicit maintenance decision.
            command = ["bash", "Scripts/local/refresh.sh", "event-queue", "--no-push", "--", *ids]
            log.write(f"batch {index}/{len(batches)}: {' '.join(ids)}\n")
            log.flush()
            process = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=False)
            # Exit 4 is the collector's documented partial-batch outcome: it
            # has checkpointed successful events and isolated the failures.
            if process.returncode not in (0, 4):
                log.write(f"STOP batch {index}: refresh exited {process.returncode}; inspect this log before retrying.\n")
                state.update({
                    "status": "stopped", "nextBatchIndex": batch_index, "currentBatch": None,
                    "currentTargets": ids, "pid": None, "lastOutcome": latest_run_outcome(),
                    "updatedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
                })
                write_json(RUN_STATE_PATH, state)
                return process.returncode
            subprocess.run([sys.executable, "Scripts/build_completeness_report.py"], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
            state.update({
                "status": "running", "nextBatchIndex": batch_index + 1, "completedBatches": batch_index + 1,
                "currentBatch": None, "currentTargets": [], "pid": os.getpid(), "lastOutcome": latest_run_outcome(),
                "updatedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
            })
            write_json(RUN_STATE_PATH, state)
    state.update({"status": "completed", "nextBatchIndex": len(batches), "completedBatches": len(batches), "pid": None, "updatedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()})
    write_json(RUN_STATE_PATH, state)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", action="store_true", help="only regenerate the offline target plan and missing-TNR list")
    parser.add_argument("--status", action="store_true", help="print the persisted batch progress without making source requests")
    parser.add_argument("--run", action="store_true", help="capture planned ready targets through refresh.sh in batches of ten")
    parser.add_argument("--refresh-existing", action="store_true", help="re-capture targets that already have a structured record")
    parser.add_argument("--include-future", action="store_true", help="also request TNRs whose scheduled date is in the future")
    parser.add_argument("--only-manual", action="store_true", help="only capture reviewer-added local task rows")
    parser.add_argument("--limit", type=int, default=0, help="cap targets for a controlled test run")
    args = parser.parse_args()
    plan = write_plan()
    print(json.dumps(plan["totals"], ensure_ascii=False))
    if args.status:
        selected = selected_targets(
            plan, refresh_existing=args.refresh_existing, include_future=args.include_future, manual_only=args.only_manual,
        )
        if args.limit:
            selected = selected[:args.limit]
        batches = [selected[index:index + 10] for index in range(0, len(selected), 10)]
        state, _ = run_state(selected, batches)
        write_json(RUN_STATE_PATH, state)
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return 0
    if args.run:
        return run(
            plan, refresh_existing=args.refresh_existing, include_future=args.include_future,
            manual_only=args.only_manual, limit=args.limit,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
