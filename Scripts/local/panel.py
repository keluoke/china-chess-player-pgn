#!/usr/bin/env python3
"""Maintainer-local control panel for the policy-enforced refresh entrypoint."""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import re
import secrets
import signal
import socket
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent))

from run_manager import atomic_json, current_payload, outbox_entries, process_alive  # noqa: E402
from source_policy import local_state_root  # noqa: E402
# The panel and the scheduler must share one state projection: the same
# should_skip_target() the collector's queue selection uses decides what the
# panel shows as "可立即抓取", so the displayed pending count always equals
# what the next run will actually attempt.
from sync_chess_results_event import PARSER_VERSION, should_skip_target  # noqa: E402

REFRESH = SCRIPT_DIR / "refresh.sh"
QUEUE_PATH = REPO_ROOT / "data" / "generated" / "audit" / "domestic-event-queue.json"
TOURNAMENTS_PATH = REPO_ROOT / "data" / "generated" / "chess-results-tournaments.json"
COMPLETENESS_PATH = REPO_ROOT / "data" / "generated" / "event-completeness-report.json"
EVENT_DETAIL_ROOT = REPO_ROOT / "data" / "generated" / "chess-results-event-details"
REGISTRY_MANIFEST = REPO_ROOT / "docs" / "data" / "registry" / "manifest.json"
BULK_MANIFEST = REPO_ROOT / "docs" / "data" / "bulk" / "manifest.json"
STATE_ROOT = local_state_root()
CAPTURE_STATE_PATH = STATE_ROOT / "chess-results" / "capture-state.json"
PORT_FILE = STATE_ROOT / "panel.port"
AUTOMATION_PATH = STATE_ROOT / "automation.json"
PING_TOKEN = "china-chess-local-panel-v2"
CSRF_TOKEN = secrets.token_urlsafe(24)
SITE_URL = os.environ.get("CHINA_CHESS_SITE_URL", "https://china-chess-player-pgn.pages.dev").rstrip("/")
REFRESH_DAYS = 30

ALLOWED_COMMANDS = {
    "health", "all", "registry", "event-queue", "candidates",
    "bulk", "bulk-full", "deliver", "push", "receipts", "reindex",
    "recover-events",
}
EXTRA_TOKEN = re.compile(r"^[A-Za-z0-9_.:/=-]{1,200}$")
TNR_TOKEN = re.compile(r"^\d{4,9}$")
children: set[subprocess.Popen[bytes]] = set()
children_lock = threading.Lock()
monitor_stop = threading.Event()

STATUS_LABELS = {
    "complete": "已私有抓取",
    "partial": "部分抓取（可续跑）",
    "retry-wait": "网络失败（等待重试）",
    "quarantined": "已隔离（坏目标）",
    "unsupported": "需解析器支持",
    "failed": "失败",
}

DELIVERED_STATUSES = {
    "pushed", "ingested-to-main", "indexes-rebuilt", "deployed", "online-verified",
}
HUMAN_REQUIRED_DELIVERY_ERRORS = {
    "RELEASE_BASE_CONFLICT",
    "RELEASE_HASH_MISMATCH",
    "RELEASE_MANIFEST_INVALID",
    "RELEASE_PATH_INVALID",
    "API_DELIVERY_BLOCKED",
    "API_DELIVERY_BASELINE_MISSING",
    "API_DELIVERY_TREE_TRUNCATED",
    "GIT_FALLBACK_BASE_MISMATCH",
    "ONLINE_HASH_MISMATCH",
}
TNR_RELEASE_PATH = re.compile(r"(?:^|/)tnr(\d{4,9})(?:[./-]|$)")


def durable_state() -> dict:
    payload = current_payload(40000)
    return {
        **payload,
        "cmd": payload.get("command") or "",
        "startedAt": payload.get("startedAt"),
        "finishedAt": payload.get("finishedAt"),
        "returncode": payload.get("returnCode"),
    }


def start_job(cmd: str, extra: list[str]) -> tuple[bool, str]:
    state = durable_state()
    if state.get("running"):
        return False, f"已有任务在运行：{state.get('command')}（run {state.get('runId')}）"
    if cmd not in ALLOWED_COMMANDS:
        return False, f"命令未列入维护者本地白名单：{cmd}"
    if len(extra) > 20 or any(not EXTRA_TOKEN.fullmatch(token) for token in extra):
        return False, "参数数量过多或包含不安全字符"
    argv = ["bash", str(REFRESH), cmd]
    if extra:
        argv.extend(["--", *extra])
    env = dict(os.environ)
    env.update(PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    launcher_log = STATE_ROOT / "launcher.log"
    launcher_log.parent.mkdir(parents=True, exist_ok=True)
    with launcher_log.open("ab", buffering=0) as output:
        proc = subprocess.Popen(
            argv,
            cwd=REPO_ROOT,
            stdout=output,
            stderr=output,
            start_new_session=True,
            env=env,
        )
    try:
        return_code = proc.wait(timeout=0.2)
    except subprocess.TimeoutExpired:
        return_code = None
    if return_code not in {None, 0}:
        try:
            detail = launcher_log.read_text(encoding="utf-8", errors="replace")[-600:].strip()
        except OSError:
            detail = ""
        return False, detail or f"任务启动失败（exit {return_code}）"
    with children_lock:
        children.add(proc)

    def reap() -> None:
        proc.wait()
        with children_lock:
            children.discard(proc)

    threading.Thread(target=reap, daemon=True).start()
    return True, "已启动；运行状态、日志和锁会在面板重启后继续保留"


def automation_payload() -> dict:
    payload = _read_json_file(AUTOMATION_PATH)
    entries = outbox_entries()

    def attention_code(item: dict) -> str | None:
        code = item.get("lastError")
        if code in HUMAN_REQUIRED_DELIVERY_ERRORS:
            return str(code)
        online = (item.get("receipts") or {}).get("online") or {}
        if (
            online.get("ok") is False
            and online.get("expected")
            and online.get("actual")
            and online.get("expected") != online.get("actual")
        ):
            return "ONLINE_HASH_MISMATCH"
        return None

    attention = [
        {
            "runId": item.get("runId"),
            "status": item.get("status"),
            "errorCode": attention_code(item),
        }
        for item in entries
        if attention_code(item)
    ]
    attention_ids = {str(item.get("runId")) for item in attention}
    return {
        "enabled": payload.get("enabled", True),
        "lastAction": payload.get("lastAction"),
        "lastActionAt": payload.get("lastActionAt"),
        "nextCheckAt": payload.get("nextCheckAt"),
        "attention": attention,
        "pending": sum(1 for item in entries if item.get("status") == "pending"),
        "advancing": sum(
            1 for item in entries
            if item.get("status") in {
                "pushed", "ingested-to-main", "indexes-rebuilt", "deployed",
            }
            and str(item.get("runId")) not in attention_ids
        ),
    }


def set_automation(enabled: bool, **fields: object) -> dict:
    payload = _read_json_file(AUTOMATION_PATH)
    payload.update({"schemaVersion": 1, "enabled": bool(enabled), **fields})
    atomic_json(AUTOMATION_PATH, payload)
    return automation_payload()


def automation_monitor() -> None:
    """Advance outbox delivery/receipts only; never starts a source capture."""
    backoff = [30, 120, 300]
    index = 0
    while not monitor_stop.wait(backoff[index]):
        config = automation_payload()
        if not config.get("enabled") or durable_state().get("running"):
            continue
        entries = outbox_entries()
        pending = [
            item for item in entries
            if item.get("status") == "pending"
            and item.get("lastError") not in HUMAN_REQUIRED_DELIVERY_ERRORS
        ]
        advancing = [
            item for item in entries
            if item.get("status") in {
                "pushed", "ingested-to-main", "indexes-rebuilt", "deployed",
            }
            and item.get("lastError") not in HUMAN_REQUIRED_DELIVERY_ERRORS
            and not (
                ((item.get("receipts") or {}).get("online") or {}).get("ok") is False
                and ((item.get("receipts") or {}).get("online") or {}).get("expected")
                and ((item.get("receipts") or {}).get("online") or {}).get("actual")
                and (
                    ((item.get("receipts") or {}).get("online") or {}).get("expected")
                    != ((item.get("receipts") or {}).get("online") or {}).get("actual")
                )
            )
        ]
        command = "deliver" if pending else "receipts" if advancing else ""
        if not command:
            index = 0
            continue
        ok, _message = start_job(command, [])
        if ok:
            delay = backoff[min(index + 1, len(backoff) - 1)]
            set_automation(
                True,
                lastAction=command,
                lastActionAt=dt.datetime.now(dt.timezone.utc).isoformat(),
                nextCheckAt=(
                    dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=delay)
                ).isoformat(),
            )
            index = min(index + 1, len(backoff) - 1)


def stop_job() -> tuple[bool, str]:
    state = durable_state()
    pid = int(state.get("pid") or 0)
    if not state.get("running") or not process_alive(pid):
        return False, "当前没有运行中的任务"
    try:
        os.killpg(pid, signal.SIGINT)
    except ProcessLookupError:
        return False, "任务刚刚结束"
    return True, "已发送中止信号；未通过校验的暂存数据不会进入发布包"


def load_captures() -> dict:
    try:
        payload = json.loads(CAPTURE_STATE_PATH.read_text(encoding="utf-8"))
        captures = payload.get("events") if isinstance(payload, dict) else {}
        return captures if isinstance(captures, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def normalize_capture(entry: dict) -> dict:
    entry = entry if isinstance(entry, dict) else {}
    if entry and "status" not in entry:
        entry = {**entry, "status": "complete"}
    return entry


def queue_payload() -> dict:
    try:
        payload = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"totals": {}, "targets": [], "missing": True}
    captures = load_captures()
    targets = []
    upcoming: list[str] = []
    summary = {
        "publicComplete": 0, "privateComplete": 0, "partial": 0,
        "pending": 0, "quarantined": 0, "needsParser": 0, "retryWait": 0,
        "schedulable": 0,
    }
    for item in payload.get("targets") or []:
        tid = re.sub(r"\D", "", str(item.get("tournamentID") or ""))
        if not tid:
            continue
        capture = normalize_capture(captures.get(tid) or {})
        next_action = str(item.get("nextAction") or "")
        capture_status = capture.get("status") or ""
        # Exactly the scheduler's projection: a target is schedulable iff the
        # queue wants it and should_skip_target() would not skip it.
        skip_reason = should_skip_target(capture, REFRESH_DAYS) if capture else ""
        schedulable = next_action in {"capture-event", "refresh-snapshot"} and not skip_reason
        if schedulable:
            summary["schedulable"] += 1
            if len(upcoming) < 10:
                upcoming.append(tid)
        if next_action == "monitor":
            summary["publicComplete"] += 1
            status, label = "public-complete", "历史公开层已完整（本地口径）"
        elif capture_status == "complete":
            summary["privateComplete"] += 1
            status, label = "privately-captured", STATUS_LABELS["complete"]
        elif capture_status == "partial":
            summary["partial"] += 1
            status, label = "partial", STATUS_LABELS["partial"]
        elif capture_status == "quarantined":
            summary["quarantined"] += 1
            status, label = "quarantined", STATUS_LABELS["quarantined"]
        elif capture_status == "unsupported":
            summary["needsParser"] += 1
            status, label = "needs-parser", STATUS_LABELS["unsupported"]
        elif capture_status in {"retry-wait", "failed"}:
            summary["retryWait"] += 1
            status, label = "retry-wait", STATUS_LABELS["retry-wait"]
        else:
            summary["pending"] += 1
            status, label = "pending-private-capture", "待私有抓取"
        targets.append({
            "tournamentID": tid,
            "eventName": str(item.get("eventName") or f"tnr{tid}")[:180],
            "category": str(item.get("category") or "国内赛事")[:80],
            "priorityScore": item.get("priorityScore"),
            "nextAction": next_action,
            "status": status,
            "statusLabel": label,
            "schedulable": schedulable,
            "skipReason": skip_reason or None,
            "errorCode": capture.get("errorCode"),
            "failedPage": capture.get("failedPage"),
            "nextRetryAt": capture.get("nextRetryAt"),
            "lastCapturedAt": capture.get("capturedAt"),
            "updatedAt": capture.get("updatedAt"),
            "captureStats": {
                "players": capture.get("players"),
                "rounds": capture.get("rounds"),
                "standings": capture.get("standings"),
                "pagesFetched": capture.get("pagesFetched"),
                "pagesExpected": capture.get("pagesExpected"),
            } if capture else None,
        })
    return {
        "generatedAt": payload.get("generatedAt"),
        "totals": payload.get("totals") or {},
        "summary": summary,
        "upcoming": upcoming,
        "parserVersion": PARSER_VERSION,
        "targets": targets,
    }


def recent_payload() -> dict:
    """Latest capture attempts straight from capture-state — including pasted
    TNRs that are not in the static queue, so no successful capture can hide."""
    try:
        queue_ids = {
            re.sub(r"\D", "", str(item.get("tournamentID") or ""))
            for item in (json.loads(QUEUE_PATH.read_text(encoding="utf-8")).get("targets") or [])
        }
    except (OSError, json.JSONDecodeError):
        queue_ids = set()
    entries = []
    for tid, raw in load_captures().items():
        capture = normalize_capture(raw)
        if not capture:
            continue
        status = capture.get("status") or "complete"
        entries.append({
            "tournamentID": tid,
            "status": status,
            "statusLabel": STATUS_LABELS.get(status, status),
            "errorCode": capture.get("errorCode"),
            "failedPage": capture.get("failedPage"),
            "players": capture.get("players"),
            "rounds": capture.get("rounds"),
            "standings": capture.get("standings"),
            "capturedAt": capture.get("capturedAt"),
            "updatedAt": capture.get("updatedAt") or capture.get("capturedAt"),
            "nextRetryAt": capture.get("nextRetryAt"),
            "inQueue": tid in queue_ids,
        })
    entries.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
    return {"entries": entries[:50]}


def latest_command_run(command: str) -> dict:
    runs = STATE_ROOT / "runs"
    try:
        entries = sorted((path for path in runs.iterdir() if path.is_dir()), reverse=True)
    except OSError:
        return {}
    for path in entries:
        payload = _read_json_file(path / "run.json")
        if payload.get("command") == command:
            return payload
    return {}


def latest_outbox_result() -> tuple[dict, dict]:
    try:
        entries = sorted(
            outbox_entries(),
            key=lambda item: str(item.get("createdAt") or ""),
            reverse=True,
        )
    except Exception:  # noqa: BLE001
        return {}, {}
    for delivery in entries:
        entry = pathlib.Path(str(delivery.get("path") or ""))
        manifest = _read_json_file(entry / "manifest.json")
        result = _read_json_file(entry / "result.json")
        if manifest.get("command") == "event-queue" and result:
            return {
                "runId": delivery.get("runId"),
                "runDir": "",
                "command": "event-queue",
                "result": "partial" if any(
                    key != "complete" and value
                    for key, value in (result.get("summary") or {}).items()
                ) else "ok",
                "errorCode": "PARTIAL_FAILURE" if any(
                    key != "complete" and value
                    for key, value in (result.get("summary") or {}).items()
                ) else None,
            }, result
    return {}, {}


def result_payload() -> dict:
    """Structured outcome of the latest event batch, retained across follow-ups."""
    current = durable_state()
    state = current if current.get("command") == "event-queue" else latest_command_run("event-queue")
    retained_result: dict = {}
    if not state:
        state, retained_result = latest_outbox_result()
    run_dir = state.get("runDir") or ""
    run_id = str(state.get("runId") or "")
    base = {
        "running": bool(
            current.get("running")
            and current.get("runId") == run_id
            and current.get("command") == "event-queue"
        ),
        "runId": run_id,
        "command": state.get("command"),
        "result": state.get("result"),
        "errorCode": state.get("errorCode"),
        "targets": {},
        "summary": {},
        "publication": publication_payload(pathlib.Path(run_dir) if run_dir else None, run_id),
    }
    payload = retained_result
    if run_dir:
        payload = _read_json_file(pathlib.Path(run_dir) / "result.json")
    if not payload and run_id:
        payload = _read_json_file(STATE_ROOT / "outbox" / run_id / "result.json")
    if not payload:
        return base
    targets = payload.get("targets") or {}
    changes = base["publication"].get("targetChanges") or {}
    base["targets"] = {
        str(tid): {
            **(target if isinstance(target, dict) else {}),
            "releaseFiles": int((changes.get(str(tid)) or {}).get("files") or 0),
            "releaseBytes": int((changes.get(str(tid)) or {}).get("bytes") or 0),
        }
        for tid, target in targets.items()
    }
    base["summary"] = payload.get("summary") or {}
    base["requested"] = payload.get("requested") or []
    base["statusLabels"] = STATUS_LABELS
    return base


def _read_json_file(path: pathlib.Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def publication_payload(run_dir: pathlib.Path | None, run_id: str) -> dict:
    """Join capture output, exact changed files and outbox delivery facts."""
    manifest = _read_json_file(run_dir / "release-manifest.json") if run_dir else {}
    outbox_dir = STATE_ROOT / "outbox" / run_id if run_id else None
    if not manifest and outbox_dir:
        manifest = _read_json_file(outbox_dir / "manifest.json")
    delivery = _read_json_file(outbox_dir / "delivery.json") if outbox_dir else {}
    files = manifest.get("files") if isinstance(manifest.get("files"), list) else []
    normalized_files = [item for item in files if isinstance(item, dict)]
    target_changes: dict[str, dict[str, int]] = {}
    upserts = deletes = changed_bytes = attributed = 0
    for item in normalized_files:
        operation = str(item.get("operation") or "")
        if operation == "upsert":
            upserts += 1
        elif operation == "delete":
            deletes += 1
        size = max(0, int(item.get("bytes") or 0))
        changed_bytes += size
        matched = TNR_RELEASE_PATH.search(str(item.get("path") or ""))
        if matched:
            attributed += 1
            current = target_changes.setdefault(matched.group(1), {"files": 0, "bytes": 0})
            current["files"] += 1
            current["bytes"] += size
    status = str(delivery.get("status") or ("local-only" if manifest else "no-release"))
    return {
        "hasManifest": bool(manifest),
        "changedFiles": len(normalized_files),
        "upserts": upserts,
        "deletes": deletes,
        "changedBytes": changed_bytes,
        "targetChanges": target_changes,
        "unattributedFiles": max(0, len(normalized_files) - attributed),
        "status": status,
        "delivered": status in DELIVERED_STATUSES,
        "onlineVerified": status == "online-verified",
        "route": delivery.get("route"),
        "remoteSHA": delivery.get("remoteSHA"),
        "lastError": delivery.get("lastError"),
        "receipts": delivery.get("receipts") or {},
    }


def progress_payload() -> dict:
    state = durable_state()
    run_dir = state.get("runDir") or ""
    result = {"running": bool(state.get("running")), "targets": {}}
    if not run_dir:
        return result
    try:
        payload = json.loads((pathlib.Path(run_dir) / "progress.json").read_text(encoding="utf-8"))
        targets = payload.get("targets") if isinstance(payload, dict) else {}
        result["targets"] = targets if isinstance(targets, dict) else {}
        result["updatedAt"] = payload.get("updatedAt")
    except (OSError, json.JSONDecodeError):
        pass
    return result


def preview_payload(tnr: str) -> dict:
    """Read-only summary of a private capture. Local preview only — this data
    is never published and the endpoint only listens on 127.0.0.1."""
    if not TNR_TOKEN.fullmatch(tnr):
        return {"ok": False, "message": "无效 tnr"}
    capture = normalize_capture(load_captures().get(tnr) or {})
    if not capture:
        return {"ok": False, "message": "该赛事尚无私有抓取记录"}
    root = capture.get("runPrivateRoot")
    extracted = pathlib.Path(root) / "extracted" / "chess-results-event-details" / f"tnr{tnr}.json" if root else None
    if not extracted or not extracted.is_file():
        published = EVENT_DETAIL_ROOT / f"tnr{tnr}.json"
        extracted = published if published.is_file() else None
    if not extracted:
        return {"ok": False, "message": "找不到本地解析结果文件", "capture": capture}
    try:
        payload = json.loads(extracted.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "message": f"解析结果读取失败：{exc}"}
    standings = payload.get("standings") or []
    return {
        "ok": True,
        "notice": "本地清洗结果预览；变化部分将随发布管线上传",
        "tournamentID": tnr,
        "title": payload.get("sourceName"),
        "format": payload.get("format"),
        "captureStatus": payload.get("captureStatus"),
        "errorCode": payload.get("captureErrorCode"),
        "failedPage": payload.get("failedPage"),
        "roundCount": payload.get("roundCount"),
        "capturedRounds": len(payload.get("rounds") or []),
        "players": len(payload.get("players") or []),
        "standingsRows": len(standings),
        "topStandings": [
            {
                "rank": row.get("rank"), "name": row.get("name"),
                "score": row.get("score"), "federation": row.get("federation"),
            }
            for row in standings[:10]
        ],
        "evidence": payload.get("evidence"),
        "privateRoot": root,
    }


def events_payload() -> dict:
    """Join local capture, catalogue, completeness and delivery state by TNR."""
    try:
        raw_value = json.loads(TOURNAMENTS_PATH.read_text(encoding="utf-8"))
        tournaments = raw_value if isinstance(raw_value, list) else []
    except (OSError, json.JSONDecodeError):
        tournaments = []
    tournament_map = {
        str(item.get("tournamentID") or ""): item
        for item in tournaments
        if isinstance(item, dict) and item.get("tournamentID")
    }
    queue = _read_json_file(QUEUE_PATH)
    queue_map = {
        str(item.get("tournamentID") or ""): item
        for item in (queue.get("targets") or [])
        if isinstance(item, dict) and item.get("tournamentID")
    }
    completeness = _read_json_file(COMPLETENESS_PATH)
    completeness_map = {
        str(item.get("tournamentID") or ""): item
        for item in (completeness.get("events") or [])
        if isinstance(item, dict) and item.get("tournamentID")
    }
    publication: dict[str, dict] = {}
    try:
        deliveries = sorted(
            outbox_entries(),
            key=lambda item: str(item.get("createdAt") or ""),
        )
    except Exception:  # noqa: BLE001
        deliveries = []
    for delivery in deliveries:
        manifest = _read_json_file(pathlib.Path(str(delivery.get("path") or "")) / "manifest.json")
        for item in manifest.get("files") or []:
            matched = TNR_RELEASE_PATH.search(str(item.get("path") or ""))
            if matched:
                publication[matched.group(1)] = {
                    "status": delivery.get("status"),
                    "runId": delivery.get("runId"),
                    "lastError": delivery.get("lastError"),
                }

    entries = []
    for tid, raw in load_captures().items():
        capture = normalize_capture(raw)
        if not capture:
            continue
        tournament = tournament_map.get(tid) or {}
        queued = queue_map.get(tid) or {}
        complete = completeness_map.get(tid) or {}
        detail = _read_json_file(EVENT_DETAIL_ROOT / f"tnr{tid}.json")
        status = str(capture.get("status") or "complete")
        entries.append({
            "tournamentID": tid,
            "name": queued.get("eventName") or tournament.get("name") or detail.get("sourceName") or f"tnr{tid}",
            "date": tournament.get("date"),
            "year": str(tournament.get("date") or "")[:4] or None,
            "category": queued.get("series") or queued.get("policy"),
            "status": status,
            "statusLabel": STATUS_LABELS.get(status, status),
            "errorCode": capture.get("errorCode"),
            "players": capture.get("players") or len(detail.get("players") or []),
            "rounds": capture.get("rounds") or len(detail.get("rounds") or []),
            "standings": capture.get("standings") or len(detail.get("standings") or []),
            "capturedAt": capture.get("updatedAt") or capture.get("capturedAt"),
            "resultsStatus": complete.get("resultsStatus"),
            "pgnAvailability": complete.get("pgnAvailability"),
            "archiveStatus": complete.get("archiveStatus"),
            "publication": publication.get(tid) or {"status": "not-packaged"},
        })
    entries.sort(
        key=lambda item: (str(item.get("date") or ""), str(item.get("capturedAt") or "")),
        reverse=True,
    )
    return {"entries": entries, "count": len(entries)}


def manifest_age_days(path: pathlib.Path) -> int | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("generatedAt", "")
        stamp = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return (dt.datetime.now(dt.timezone.utc) - stamp.astimezone(dt.timezone.utc)).days
    except Exception:  # noqa: BLE001
        return None


def publish_payload() -> dict:
    try:
        entries = outbox_entries()
    except Exception:  # noqa: BLE001
        entries = []
    fide_age = manifest_age_days(REGISTRY_MANIFEST)
    bulk_age = manifest_age_days(BULK_MANIFEST)
    return {
        "siteURL": SITE_URL,
        "fideAgeDays": fide_age,
        "fideDue": fide_age is None or fide_age >= 25,
        "lichessAgeDays": bulk_age,
        "lichessDue": bulk_age is None or bulk_age >= 25,
        "entries": [
            {
                "runId": item.get("runId"), "status": item.get("status"),
                "commit": (item.get("commit") or "")[:12],
                "remoteSHA": (item.get("remoteSHA") or "")[:12],
                "route": item.get("route"), "attempts": item.get("attempts"),
                "lastError": item.get("lastError"), "updatedAt": item.get("updatedAt"),
                "receipts": {
                    key: {"url": value.get("url"), "conclusion": value.get("conclusion"), "ok": value.get("ok")}
                    for key, value in (item.get("receipts") or {}).items()
                    if isinstance(value, dict)
                },
            }
            for item in entries
        ],
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "ChinaChessPanel/2.2"

    def log_message(self, *_args) -> None:
        pass

    def send_body(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, payload: dict, code: int = 200) -> None:
        self.send_body(code, json.dumps(payload, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in {"/", "/index.html"}:
            page = (
                PAGE.replace("{{CSRF}}", CSRF_TOKEN)
                .replace("{{REPO}}", str(REPO_ROOT))
                .replace("{{SITE}}", SITE_URL)
            )
            self.send_body(200, page.encode(), "text/html; charset=utf-8")
        elif parsed.path == "/api/ping":
            self.send_json({"panel": PING_TOKEN})
        elif parsed.path == "/api/state":
            self.send_json(durable_state())
        elif parsed.path == "/api/queue":
            self.send_json(queue_payload())
        elif parsed.path == "/api/recent":
            self.send_json(recent_payload())
        elif parsed.path == "/api/events":
            self.send_json(events_payload())
        elif parsed.path == "/api/result":
            self.send_json(result_payload())
        elif parsed.path == "/api/progress":
            self.send_json(progress_payload())
        elif parsed.path == "/api/outbox":
            self.send_json(publish_payload())
        elif parsed.path == "/api/automation":
            self.send_json(automation_payload())
        elif parsed.path == "/api/preview":
            query = urllib.parse.parse_qs(parsed.query)
            self.send_json(preview_payload((query.get("tnr") or [""])[0]))
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        if self.headers.get("X-Panel-Token") != CSRF_TOKEN:
            self.send_json({"ok": False, "message": "invalid panel token"}, 403)
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > 16384:
            self.send_json({"ok": False, "message": "request too large"}, 413)
            return
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self.send_json({"ok": False, "message": "invalid JSON"}, 400)
            return
        if self.path == "/api/run":
            ok, message = start_job(str(body.get("cmd") or ""), [str(x) for x in body.get("extra") or []])
            self.send_json({"ok": ok, "message": message}, 200 if ok else 409)
        elif self.path == "/api/capture":
            tnrs = [str(x) for x in body.get("tnrs") or []]
            if not tnrs:
                self.send_json({"ok": False, "message": "请先粘贴至少一个 TNR 或链接"}, 400)
                return
            if len(tnrs) > 10 or any(not TNR_TOKEN.fullmatch(t) for t in tnrs):
                self.send_json({"ok": False, "message": "一次最多 10 场，且 TNR 必须是 4-9 位数字"}, 400)
                return
            ok, message = start_job("event-queue", tnrs)
            self.send_json({"ok": ok, "message": message, "count": len(tnrs)}, 200 if ok else 409)
        elif self.path == "/api/stop":
            ok, message = stop_job()
            self.send_json({"ok": ok, "message": message}, 200 if ok else 409)
        elif self.path == "/api/automation":
            self.send_json(set_automation(bool(body.get("enabled"))))
        elif self.path == "/api/shutdown":
            self.send_json({"ok": True, "message": "面板已退出；运行中的采集任务不受影响"})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        else:
            self.send_json({"error": "not found"}, 404)


PAGE = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>维护者本地数据控制台</title><style>
:root{--bg:#f5f3ee;--card:#fff;--ink:#20232a;--muted:#68707b;--line:#ddd8ce;--blue:#175bd3;--ok:#177a3d;--bad:#b3261e;--warn:#a25700}
@media(prefers-color-scheme:dark){:root{--bg:#15171c;--card:#20232a;--ink:#eee;--muted:#a1a6b0;--line:#373b44;--blue:#7aa5f8;--ok:#62d18d;--bad:#ee918b;--warn:#e3a85f}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,"PingFang SC",sans-serif}.wrap{max-width:1120px;margin:auto;padding:28px 20px 70px}h1{margin:0;font-size:1.7rem}h1 small{display:block;color:var(--muted);font-size:.84rem;font-weight:400;margin-top:5px}.notice{margin:18px 0;padding:14px 16px;border:1px solid var(--line);border-radius:12px;background:var(--card)}.notice b{color:var(--ok)}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:13px}.card{background:var(--card);border:1px solid var(--line);border-radius:13px;padding:15px}.head{display:flex;justify-content:space-between;gap:8px}.badge{color:var(--blue);font-size:.76rem}.desc,.meta{color:var(--muted);font-size:.84rem}.meta{margin-top:8px}.actions{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap;align-items:center}button{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:8px;padding:7px 12px;cursor:pointer}button.primary{background:var(--blue);border-color:var(--blue);color:white}button.danger{color:var(--bad)}button:disabled{opacity:.45;cursor:not-allowed}.status{display:flex;gap:10px;align-items:flex-start;margin:22px 0;padding:14px;background:var(--card);border:1px solid var(--line);border-radius:12px}.dot{width:11px;height:11px;border-radius:50%;background:var(--muted);margin-top:6px}.dot.running{background:var(--blue);animation:pulse 1.2s infinite}.dot.ok{background:var(--ok)}.dot.bad{background:var(--bad)}.dot.warn{background:var(--warn)}@keyframes pulse{50%{opacity:.35}}#statusMeta{color:var(--muted);font-size:.83rem}#log{background:#0d1117;color:#d7e0ea;border-radius:11px;padding:14px;height:320px;overflow:auto;white-space:pre-wrap;font:12px/1.5 ui-monospace,SFMono-Regular,monospace}table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line)}th,td{padding:8px;border-bottom:1px solid var(--line);text-align:left;font-size:.83rem}h2{font-size:1.15rem;margin-top:34px;border-bottom:2px solid var(--line);padding-bottom:6px}h3{font-size:.98rem;margin:20px 0 8px}footer{display:flex;justify-content:space-between;color:var(--muted);font-size:.8rem;margin-top:20px}a{color:var(--blue);cursor:pointer;text-decoration:none}
textarea{width:100%;min-height:74px;border:1px solid var(--line);border-radius:9px;background:var(--bg);color:var(--ink);padding:9px;font:13px/1.5 ui-monospace,SFMono-Regular,monospace;resize:vertical}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}.chip{border:1px solid var(--line);border-radius:999px;padding:2px 10px;font-size:.78rem;background:var(--bg)}.chip.bad{color:var(--bad);border-color:var(--bad)}.chip.ok{color:var(--ok);border-color:var(--ok)}.chip.warn{color:var(--warn);border-color:var(--warn)}
.summary{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}.pill{border:1px solid var(--line);border-radius:999px;padding:3px 11px;font-size:.79rem;background:var(--card);cursor:pointer}.pill b{font-weight:600}.pill.active{border-color:var(--blue);color:var(--blue)}
#progressList{margin-top:10px;display:grid;gap:6px}.prog{border:1px solid var(--line);border-radius:9px;padding:8px 10px;font-size:.82rem;background:var(--card)}.prog .bar{height:5px;border-radius:3px;background:var(--line);margin-top:6px;overflow:hidden}.prog .bar i{display:block;height:100%;background:var(--blue)}
#previewBox{display:none;margin-top:14px;border:1px solid var(--line);border-radius:12px;background:var(--card);padding:14px}
#batchResult{display:none;margin-top:14px;border:1px solid var(--line);border-radius:12px;background:var(--card);padding:14px}
.small{font-size:.78rem;color:var(--muted)}
input[type=search],select{border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink);padding:6px 10px;font-size:.85rem;min-width:160px}
.pager{display:flex;gap:8px;align-items:center;margin-top:8px;color:var(--muted);font-size:.82rem}
.resultRow{display:flex;gap:10px;align-items:center;border:1px solid var(--line);border-radius:9px;padding:8px 10px;margin-top:6px;font-size:.85rem;flex-wrap:wrap}.resultGroup{margin-top:14px;font-size:.86rem}.resultGroup>b{display:block;margin-bottom:5px}.publishLine{margin-top:10px;padding:9px 11px;border-radius:9px;background:var(--bg);font-size:.84rem}
</style></head><body><div class="wrap">
<h1>维护者本地数据控制台<small>社区只提交线索、勘误和人工知识；所有网络采集均由维护者本机执行</small></h1>
<div class="notice"><b>合规边界已启用：</b>来源原始页面只保存在本机；通过完整性门禁的清洗结构化数据才进入 manifest → outbox → 云端摄入 → 重建 → 部署 → 线上验证。采集、数据变化和发布状态分别展示，只有 online-verified 才算上线。</div>
<div class="status"><span id="dot" class="dot"></span><div style="flex:1"><b id="statusText">读取状态…</b><div id="statusMeta"></div><div id="progressList"></div></div><button id="stop" class="danger" onclick="stopJob()">中止任务</button></div>
<div class="notice"><label><input type="checkbox" id="autoAdvance" onchange="toggleAutomation()"> <b>自动推进发布</b></label><span id="automationMeta" class="meta">只处理 outbox 投递与云端回执；不会自动抓取任何来源。</span></div>

<h2>① 赛事采集与发布（来源访问仅限本机）</h2>
<div class="card">
<textarea id="tnrInput" placeholder="粘贴 Chess-Results 链接或 TNR，一行一个，例如：&#10;1110333&#10;tnr1213323&#10;https://chess-results.com/tnr1156008.aspx?lan=1"></textarea>
<div class="chips" id="tnrChips"></div>
<div class="actions"><button class="primary" id="captureBtn" onclick="startCapture()">开始采集</button><span id="captureMsg" class="small"></span></div>
<div class="actions"><button onclick="runCmd('recover-events',[],false)">接管中断产物并发布</button><span class="small">只接管通过路径/JSON/PGN 格式校验的机器产物；不会回抓，也不会自动丢弃文件。</span></div>
</div>
<div id="batchResult"></div>
<div id="previewBox"></div>

<h3>目标队列</h3>
<div class="summary" id="queueSummary"></div>
<div class="actions">
<button onclick="queueTop(1)">采集下一个</button><button onclick="queueTop(3)">采集下 3 个</button><button onclick="queueTop(10)">采集下 10 个</button>
<span class="small" id="upcomingHint"></span>
</div>
<div class="actions"><span class="small">筛选：</span><span id="queueTabs"></span><input type="search" id="queueSearch" placeholder="搜索赛事名 / TNR…" oninput="qPage=0;renderQueue()"></div>
<table><thead><tr><th>赛事</th><th>tnr</th><th>优先级</th><th>状态</th><th>动作</th></tr></thead><tbody id="queue"></tbody></table>
<div class="pager"><button onclick="qPage=Math.max(0,qPage-1);renderQueue()">上一页</button><span id="pageInfo"></span><button onclick="qPage++;renderQueue()">下一页</button></div>

<h3>已抓赛事</h3>
<div class="actions">
<input type="search" id="eventSearch" placeholder="搜索赛事名 / TNR…" oninput="ePage=0;renderEvents()">
<select id="eventSort" onchange="ePage=0;renderEvents()"><option value="date">赛事日期倒序</option><option value="captured">抓取时间倒序</option></select>
<select id="eventPublication" onchange="ePage=0;renderEvents()"><option value="all">全部发布状态</option><option value="online-verified">线上已验证</option><option value="pending">待投递</option><option value="attention">需处理</option></select>
</div>
<table><thead><tr><th>日期 / 赛事</th><th>状态</th><th>完整度</th><th>发布</th><th>抓取时间</th><th>动作</th></tr></thead><tbody id="recent"></tbody></table>
<div class="pager"><button onclick="ePage=Math.max(0,ePage-1);renderEvents()">上一页</button><span id="eventPageInfo"></span><button onclick="ePage++;renderEvents()">下一页</button></div>

<h2>② 公开发布中心（FIDE / Lichess → local-data → 线上）</h2>
<div class="summary" id="publishInfo"></div>
<div class="grid">
 <div class="card"><div class="head"><b>FIDE 注册表</b><span class="badge">可发布</span></div><div class="desc">临时下载、ZIP/语义/人数/分片/勘误校验，通过后才原子晋升。姓名和等级分唯一权威。</div><div class="actions"><button class="primary" onclick="runCmd('registry',[],false)">开始</button><span class="small" id="fideDue"></span></div></div>
 <div class="card"><div class="head"><b>Lichess Broadcast</b><span class="badge">CC BY-SA 4.0</span></div><div class="desc">在暂存区验证分片并重建数据包，manifest 保留许可证和署名链接。</div><div class="actions"><button class="primary" onclick="runCmd('bulk',[],false)">开始</button><button onclick="runCmd('bulk-full',[],true)">全量刷新</button><span class="small" id="lichessDue"></span></div></div>
 <div class="card"><div class="head"><b>投递发布包</b><span class="badge">不抓取</span></div><div class="desc">把 outbox 中待发布的 release 包投递到 local-data；自动轮换直连/代理/API 路线。</div><div class="actions"><button class="primary" onclick="runCmd('deliver',[],false)">投递</button></div></div>
 <div class="card"><div class="head"><b>同步云端回执</b><span class="badge">只读</span></div><div class="desc">查询 GitHub ingest/rebuild/deploy 结论并校验线上文件哈希；pushed 不等于已发布。</div><div class="actions"><button class="primary" onclick="runCmd('receipts',[],false)">同步回执</button></div></div>
</div>
<h3>发布投递状态（outbox）</h3>
<div class="small">状态链：pending → pushed → ingested-to-main → indexes-rebuilt → deployed → <b>online-verified</b>（只有线上文件哈希验证通过才算已发布）。</div>
<table><thead><tr><th>run-id</th><th>状态</th><th>commit</th><th>路线</th><th>回执</th><th>最近错误</th></tr></thead><tbody id="outbox"></tbody></table>

<h2>③ 一键例行维护与诊断</h2>
<div class="grid">
 <div class="card"><div class="head"><b>健康检查</b><span class="badge">只读</span></div><div class="desc">磁盘、FIDE last-good、发布路径、.git 锁、三个来源直连和 GitHub 投递路线。</div><div class="actions"><button class="primary" onclick="runCmd('health',[],false)">检查</button></div></div>
 <div class="card"><div class="head"><b>安全常规刷新</b><span class="badge">独立阶段</span></div><div class="desc">FIDE 满 25 天才更新；另采集队列前 3 个赛事到私有区。投递失败留在 outbox，不阻塞采集。</div><div class="actions"><button class="primary" onclick="runCmd('all',[],false)">开始</button></div></div>
 <div class="card"><div class="head"><b>姓名候选</b><span class="badge">仅私有</span></div><div class="desc">生成待人工审查的姓名候选；不会自动写 manual/community 或覆盖 registry。</div><div class="actions"><button class="primary" onclick="runCmd('candidates',[],false)">开始</button></div></div>
 <div class="card"><div class="head"><b>本地离线诊断</b><span class="badge">不交付</span></div><div class="desc">本地重建派生索引用于诊断；不会自动暂存、提交或推送。</div><div class="actions"><button class="primary" onclick="runCmd('reindex',[],false)">离线运行</button></div></div>
</div>

<h2>本次运行日志</h2><div id="log">尚无运行记录。</div>
<footer><span>仓库：{{REPO}}</span><a onclick="shutdown()">退出面板</a></footer></div>
<script>
const TOKEN="{{CSRF}}", SITE="{{SITE}}";
const $=s=>document.querySelector(s);
const queue=$('#queue'), stop=$('#stop'), dot=$('#dot');
const statusText=$('#statusText'), statusMeta=$('#statusMeta');
const log=$('#log'), chips=$('#tnrChips');
const progressList=$('#progressList'), previewBox=$('#previewBox'), batchResult=$('#batchResult');
const queueSummary=$('#queueSummary'), outboxBody=$('#outbox'), recentBody=$('#recent');
function esc(v){return String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
function post(path,body){return fetch(path,{method:"POST",headers:{"Content-Type":"application/json","X-Panel-Token":TOKEN},body:JSON.stringify(body)}).then(r=>r.json())}
async function runCmd(cmd,extra,full){if(full&&!confirm("全量刷新会消耗大量流量，确认继续？"))return;const r=await post('/api/run',{cmd,extra});if(!r.ok)alert(r.message);setTimeout(poll,400)}
async function stopJob(){if(!confirm("中止当前任务？已抓页面与检查点会保留，续跑只补缺页。"))return;const r=await post('/api/stop',{});alert(r.message)}
async function loadAutomation(){const a=await(await fetch('/api/automation')).json();$('#autoAdvance').checked=!!a.enabled;$('#automationMeta').textContent=`只处理 outbox/回执，不访问数据源 · 待投递 ${a.pending||0} · 推进中 ${a.advancing||0}${(a.attention||[]).length?' · 需要你处理 '+a.attention.length:''}${a.lastAction?' · 最近 '+a.lastAction:''}`}
async function toggleAutomation(){const a=await post('/api/automation',{enabled:$('#autoAdvance').checked});$('#automationMeta').textContent=a.enabled?'自动推进已开启；不会自动抓取。':'自动推进已暂停。'}
function queueTop(n){runCmd('event-queue',['--from-queue',String(n)],false);watchRun('队列前 '+n+' 个')}

// ---- capture box ----
function parseTnrs(){
 const lines=$('#tnrInput').value.split(/[\n,;\s]+/).map(s=>s.trim()).filter(Boolean);
 const seen=new Set(), good=[], bad=[];
 for(const line of lines){
  if(/^https?:\/\//i.test(line)&&!/chess-results\.com/i.test(line)){bad.push(line);continue}
  const m=line.match(/(?:tnr)?(\d{4,9})/i);
  if(m){if(!seen.has(m[1])){seen.add(m[1]);good.push(m[1])}}else bad.push(line);
 }
 return {good,bad};
}
function refreshChips(){
 const {good,bad}=parseTnrs();
 chips.innerHTML=good.map(t=>`<span class=chip>tnr${esc(t)}</span>`).join('')+bad.map(t=>`<span class="chip bad" title="无法识别">${esc(t.slice(0,40))}</span>`).join('');
 $('#captureBtn').textContent=good.length?`开始采集（${good.length} 场）`:'开始采集';
 return {good,bad};
}
$('#tnrInput').addEventListener('input',refreshChips);
let watchingRun=false, watchLabel='';
function watchRun(label){watchingRun=true;watchLabel=label;batchResult.style.display='none'}
async function startCapture(){
 const {good,bad}=refreshChips();
 if(!good.length){alert('请先粘贴至少一个有效的 TNR 或 chess-results.com 链接');return}
 if(bad.length&&!confirm(`${bad.length} 行无法识别，将被忽略。继续采集 ${good.length} 场？`))return;
 if(good.length>10){alert('一次最多 10 场；请分批粘贴以保护访问预算。');return}
 const r=await post('/api/capture',{tnrs:good});
 if(!r.ok){alert(r.message);return}
 watchRun('指定赛事 '+good.length+' 场');
 setTimeout(async()=>{const s=await(await fetch('/api/state')).json();$('#captureMsg').textContent=`已启动 run ${s.runId||''} · 共 ${good.length} 场`},600);
 setTimeout(poll,400);
}
async function renderBatchResult(){
 const r=await(await fetch('/api/result')).json();
 if(r.command!=='event-queue'||!Object.keys(r.targets||{}).length){batchResult.style.display='none';return r}
 const labels=r.statusLabels||{};
 const order={'complete':0,'partial':1,'unsupported':2,'quarantined':3,'retry-wait':4,'failed':5};
 const rows=Object.entries(r.targets).sort((a,b)=>(order[a[1].status]??9)-(order[b[1].status]??9));
 const requested=(r.requested||[]).length||rows.length, complete=Number(r.summary?.complete||0);
 const attention=requested-complete, pub=r.publication||{};
 const deliveryLabels={'no-release':'没有数据变化','local-only':'已生成发布包，尚未进入 outbox','pending':'发布包待投递','pushed':'已投递到 local-data','ingested-to-main':'已合并到 main','indexes-rebuilt':'索引已重建','deployed':'已部署，待线上校验','online-verified':'线上已验证','abandoned':'发布包已放弃'};
 const pubClass=pub.onlineVerified?'ok':pub.delivered?'':pub.hasManifest?'warn':'';
 const pubText=deliveryLabels[pub.status]||pub.status||'未生成发布包';
 const summary=`目标 <b>${requested}</b> · 完整成功 <b>${complete}</b> · 需处理 <b>${Math.max(0,attention)}</b> · 更新数据文件 <b>${pub.changedFiles||0}</b>${pub.changedFiles?`（${formatBytes(pub.changedBytes||0)}）`:''}`;
 const renderRows=(items)=>items.map(([tid,t])=>{
  const cls=t.status==='complete'?'ok':(t.status==='partial'||t.status==='unsupported'||t.status==='quarantined')?'warn':'bad';
  const stats=t.players!=null?` · ${esc(t.players)} 人 / ${esc(t.rounds??'-')} 轮 / ${esc(t.standings??'-')} 行排名`:'';
  const err=t.errorCode?` · <span class="chip ${cls}">${esc(t.errorCode)}${t.failedPage?' @ '+esc(t.failedPage):''}</span>`:'';
  const change=t.releaseFiles?`<span class="chip ok">更新 ${esc(t.releaseFiles)} 文件 · ${formatBytes(t.releaseBytes||0)}</span>`:'<span class=small>无直接赛事文件变化</span>';
  const btns=(t.status==='complete'||t.status==='partial')?`<button onclick="showPreview('${tid}')">本地预览</button>`:'';
  return `<div class=resultRow><span class="chip ${cls}">${esc((r.statusLabels||{})[t.status]||t.status)}</span><b>tnr${esc(tid)}</b><span>${esc(t.title||'')}${stats}${err}</span>${change}${btns}<button onclick="runCmd('event-queue',['${tid}'],false)">重新抓取</button></div>`
 }).join('');
 const successRows=rows.filter(([,t])=>t.status==='complete');
 const attentionRows=rows.filter(([,t])=>t.status!=='complete');
 batchResult.style.display='block';
 batchResult.innerHTML=`<div class=head><b>本批结果${r.running?'（进行中）':''}</b><span class=badge>run ${esc(r.runId||'')}</span></div>
 <div class=meta>${summary}</div>
 <div class="publishLine"><span class="chip ${pubClass}">${esc(pubText)}</span> · 数据文件 ${esc(pub.changedFiles||0)}（新增/更新 ${esc(pub.upserts||0)}，删除 ${esc(pub.deletes||0)}）${pub.route?' · 路线 '+esc(pub.route):''}${pub.remoteSHA?' · remote '+esc(String(pub.remoteSHA).slice(0,12)):''}${pub.lastError?' · '+esc(pub.lastError):''}</div>
 ${successRows.length?`<div class=resultGroup><b>完整成功（${successRows.length}）</b>${renderRows(successRows)}</div>`:''}
 ${attentionRows.length?`<div class=resultGroup><b style="color:var(--warn)">部分完成 / 失败（${attentionRows.length}）</b>${renderRows(attentionRows)}</div>`:''}`;
 return r;
}

function formatBytes(value){
 const n=Number(value||0);if(n<1024)return n+' B';if(n<1024*1024)return (n/1024).toFixed(1)+' KB';return (n/1024/1024).toFixed(2)+' MB'
}

function batchStatusText(r){
 const total=(r.requested||[]).length||Object.keys(r.targets||{}).length;
 const complete=Number(r.summary?.complete||0), attention=Math.max(0,total-complete);
 const label=r.running?'进行中':(r.errorCode==='PARTIAL_FAILURE'||attention>0?'部分完成':r.result==='ok'?'完成':'失败');
 return `event-queue · ${label} · ${complete}/${total} 完整${attention?' · '+attention+' 需处理':''}`;
}

function batchPublicationText(r){
 const p=r.publication||{}, labels={'no-release':'无数据变化','local-only':'发布包仅在本地','pending':'发布包待投递','pushed':'已投递 local-data','ingested-to-main':'已合并 main','indexes-rebuilt':'索引已重建','deployed':'已部署待校验','online-verified':'线上已验证','abandoned':'发布包已放弃'};
 return `更新 ${p.changedFiles||0} 个数据文件${p.changedFiles?' / '+formatBytes(p.changedBytes||0):''} · ${labels[p.status]||p.status||'未生成发布包'}${p.remoteSHA?' · '+String(p.remoteSHA).slice(0,12):''}`;
}

// ---- queue ----
let qData=null,qTab='schedulable',qPage=0;const PAGE_SIZE=50;
const TABS=[['schedulable','可立即抓取'],['pending','待抓'],['partial','部分'],['retry-wait','等待重试'],['privately-captured','已完成'],['quarantined','已隔离'],['needs-parser','需解析器'],['all','全部']];
function renderTabs(){$('#queueTabs').innerHTML=TABS.map(([k,v])=>`<span class="pill${qTab===k?' active':''}" onclick="qTab='${k}';qPage=0;renderQueue()">${v}</span>`).join(' ')}
function renderQueue(){
 if(!qData)return;
 renderTabs();
 const s=qData.summary||{};
 queueSummary.innerHTML=[
  ['可立即抓取',s.schedulable],['待抓',s.pending],['部分',s.partial],['等待重试',s.retryWait],
  ['新私有完成',s.privateComplete],['已隔离',s.quarantined],['需解析器',s.needsParser],
 ].map(([k,v])=>`<span class=pill>${k} <b>${v??0}</b></span>`).join('')
 +`<a class=pill href="${SITE}/events.html" target="_blank">已发布在线（打开赛事目录）↗</a>`;
 $('#upcomingHint').textContent=(qData.upcoming||[]).length?('下一批将抓取: '+qData.upcoming.slice(0,3).map(t=>'tnr'+t).join(', ')+(qData.upcoming.length>3?' …':'')):'队列中没有可立即抓取的目标';
 const term=($('#queueSearch').value||'').trim().toLowerCase();
 let rows=(qData.targets||[]).filter(t=>{
  if(qTab==='schedulable')return t.schedulable;
  if(qTab==='all')return t.status!=='public-complete';
  if(qTab==='pending')return t.status==='pending-private-capture';
  return t.status===qTab;
 });
 if(term)rows=rows.filter(t=>t.tournamentID.includes(term)||(t.eventName||'').toLowerCase().includes(term));
 if(qTab==='privately-captured')rows=rows.slice().sort((a,b)=>String(b.lastCapturedAt||'').localeCompare(String(a.lastCapturedAt||'')));
 const pages=Math.max(1,Math.ceil(rows.length/PAGE_SIZE));
 qPage=Math.min(qPage,pages-1);
 $('#pageInfo').textContent=`第 ${qPage+1}/${pages} 页 · 共 ${rows.length} 项`;
 queue.innerHTML=rows.slice(qPage*PAGE_SIZE,(qPage+1)*PAGE_SIZE).map(t=>{
  const stColor=t.status==='quarantined'||t.status==='needs-parser'||t.status==='partial'?'var(--warn)':t.status==='retry-wait'?'var(--muted)':t.status==='privately-captured'?'var(--ok)':'inherit';
  const st=`<span style="color:${stColor}">${esc(t.statusLabel)}</span>${t.errorCode?`<br><span class=meta>${esc(t.errorCode)}${t.failedPage?' · '+esc(t.failedPage):''}${t.nextRetryAt?' · 隔离/重试至 '+esc(String(t.nextRetryAt).slice(0,16)):''}</span>`:t.lastCapturedAt?`<br><span class=meta>${esc(String(t.lastCapturedAt).slice(0,16))} · ${esc(t.captureStats?.players??'-')} 人 / ${esc(t.captureStats?.rounds??'-')} 轮</span>`:''}`;
  const act=t.status==='partial'?`<button onclick="runCmd('event-queue',['${t.tournamentID}'],false)">续跑补缺页</button>`
    :t.status==='privately-captured'?`<button onclick="showPreview('${t.tournamentID}')">本地预览</button> <button onclick="runCmd('event-queue',['${t.tournamentID}'],false)">重新抓取</button>`
    :`<button onclick="runCmd('event-queue',['${t.tournamentID}'],false)">采集</button>`;
  return `<tr><td>${esc(t.eventName)}</td><td>${esc(t.tournamentID)}</td><td>${esc(t.priorityScore)}</td><td>${st}</td><td>${act}</td></tr>`
 }).join('')||'<tr><td colspan=5>该筛选下没有目标</td></tr>';
}
async function loadQueue(){qData=await(await fetch('/api/queue')).json();renderQueue();loadEvents();loadOutbox();loadAutomation()}

// ---- captured events ----
let eData={entries:[]},ePage=0;const EVENT_PAGE_SIZE=50;
async function loadEvents(){eData=await(await fetch('/api/events')).json();renderEvents()}
function renderEvents(){
 const term=($('#eventSearch').value||'').trim().toLowerCase();
 const publication=$('#eventPublication').value, sort=$('#eventSort').value;
 let rows=(eData.entries||[]).filter(e=>!term||e.tournamentID.includes(term)||(e.name||'').toLowerCase().includes(term));
 if(publication==='attention')rows=rows.filter(e=>e.status!=='complete'||!['online-verified','not-packaged'].includes(e.publication?.status));
 else if(publication!=='all')rows=rows.filter(e=>e.publication?.status===publication);
 rows.sort((a,b)=>String(sort==='captured'?b.capturedAt:b.date||'').localeCompare(String(sort==='captured'?a.capturedAt:a.date||'')));
 const pages=Math.max(1,Math.ceil(rows.length/EVENT_PAGE_SIZE));ePage=Math.min(ePage,pages-1);
 $('#eventPageInfo').textContent=`第 ${ePage+1}/${pages} 页 · 共 ${rows.length} 项`;
 recentBody.innerHTML=rows.slice(ePage*EVENT_PAGE_SIZE,(ePage+1)*EVENT_PAGE_SIZE).map(e=>{
  const cls=e.status==='complete'?'ok':(e.status==='partial'||e.status==='unsupported'||e.status==='quarantined')?'warn':'bad';
  const pub=e.publication||{},pubCls=pub.status==='online-verified'?'ok':pub.status==='pending'?'warn':'';
  const pubLabels={'not-packaged':'尚无发布包','pending':'发布包待投递','pushed':'已投递','ingested-to-main':'已合并 main','indexes-rebuilt':'索引已重建','deployed':'已部署待校验','online-verified':'线上已验证'};
  const completeness=[e.resultsStatus,e.pgnAvailability,e.archiveStatus].filter(Boolean).map(esc).join('<br>')||'-';
  const btns=(e.status==='complete'||e.status==='partial')?`<button onclick="showPreview('${e.tournamentID}')">预览</button> `:'';
  return `<tr><td>${esc(e.date||'日期未知')}<br><b>${esc(e.name)}</b><br><span class=meta>tnr${esc(e.tournamentID)} · ${esc(e.players??'-')} 人 / ${esc(e.rounds??'-')} 轮 / ${esc(e.standings??'-')} 行排名</span></td><td><span class="chip ${cls}">${esc(e.statusLabel)}</span>${e.errorCode?`<br><span class=meta>${esc(e.errorCode)}</span>`:''}</td><td>${completeness}</td><td><span class="chip ${pubCls}">${esc(pubLabels[pub.status]||pub.status||'-')}</span></td><td>${esc(String(e.capturedAt||'').slice(0,16))}</td><td>${btns}<button onclick="runCmd('event-queue',['${e.tournamentID}'],false)">重新抓取</button>${pub.status==='pending'?` <button onclick="runCmd('deliver',[],false)">投递</button>`:''}</td></tr>`
 }).join('')||'<tr><td colspan=6>尚无抓取记录</td></tr>';
}

// ---- publish center ----
async function loadOutbox(){
 const o=await(await fetch('/api/outbox')).json();
 $('#fideDue').textContent=o.fideAgeDays==null?'（本地无 registry manifest）':o.fideDue?`已 ${o.fideAgeDays} 天未更新，到期`:`${o.fideAgeDays} 天前已更新`;
 $('#lichessDue').textContent=o.lichessAgeDays==null?'':o.lichessDue?`已 ${o.lichessAgeDays} 天未更新，到期`:`${o.lichessAgeDays} 天前已更新`;
 $('#publishInfo').innerHTML=`<span class=pill>FIDE ${o.fideDue?'<b style="color:var(--warn)">到期</b>':'未到期'}</span><span class=pill>Lichess ${o.lichessDue?'<b style="color:var(--warn)">到期</b>':'未到期'}</span><a class=pill href="${SITE}/" target="_blank">打开线上站点 ↗</a>`;
 outboxBody.innerHTML=(o.entries||[]).map(e=>{
  const cls=e.status==='online-verified'?'ok':e.status==='pending'?'warn':'';
  const rc=Object.entries(e.receipts||{}).map(([k,v])=>v.url?`<a href="${esc(v.url)}" target="_blank">${k}${v.conclusion?':'+esc(v.conclusion):''}</a>`:(k==='online'?`online:${v.ok?'✔':'✘'}`:'')).filter(Boolean).join(' · ');
  return `<tr><td>${esc(e.runId)}</td><td><span class="chip ${cls}">${esc(e.status)}</span></td><td>${esc(e.commit)}</td><td>${esc(e.route||'-')}</td><td>${rc||'-'}</td><td>${esc(e.lastError||'-')}</td></tr>`
 }).join('')||'<tr><td colspan=6>没有待投递或最近投递的发布包</td></tr>';
}

// ---- preview / progress / poll ----
async function showPreview(tnr){
 const p=await(await fetch('/api/preview?tnr='+encodeURIComponent(tnr))).json();
 if(!p.ok){alert(p.message||'暂无预览');return}
 previewBox.style.display='block';
 previewBox.innerHTML=`<div class=head><b>${esc(p.title||('tnr'+tnr))}</b><span class=badge>${esc(p.notice)}</span></div>
 <div class=meta>格式 ${esc(p.format||'-')} · 状态 ${esc(p.captureStatus||'-')}${p.errorCode?' · '+esc(p.errorCode)+'（'+esc(p.failedPage||'-')+'）':''} · ${esc(p.players)} 人 · ${esc(p.capturedRounds)}/${esc(p.roundCount||'?')} 轮 · ${esc(p.standingsRows)} 行排名</div>
 <table style="margin-top:8px"><thead><tr><th>#</th><th>棋手</th><th>积分</th><th>联邦</th></tr></thead><tbody>${(p.topStandings||[]).map(r=>`<tr><td>${esc(r.rank)}</td><td>${esc(r.name)}</td><td>${esc(r.score)}</td><td>${esc(r.federation)}</td></tr>`).join('')}</tbody></table>
 <div class=small style="margin-top:8px">运行目录：${esc(p.privateRoot||'-')}</div>
 <div class=actions><button onclick="previewBox.style.display='none'">关闭预览</button></div>`;
 previewBox.scrollIntoView({behavior:'smooth'});
}
async function loadProgress(){
 try{
  const p=await(await fetch('/api/progress')).json();
  const targets=Object.values(p.targets||{});
  if(!p.running||!targets.length){progressList.innerHTML='';return}
  progressList.innerHTML=targets.map(t=>{
   const done=(t.pagesFetched||0)+(t.pagesCached||0), total=t.pagesExpected||0;
   const pct=total?Math.min(100,Math.round(100*done/total)):0;
   return `<div class=prog><b>${esc(t.title||('tnr'+t.tournamentID))}</b> · ${esc(t.stage)}${t.currentPage?' · '+esc(t.currentPage):''} · ${done}/${total||'?'} 页${t.pagesCached?`（缓存 ${t.pagesCached}）`:''}${t.errorCode?' · <span style="color:var(--bad)">'+esc(t.errorCode)+'</span>':''}<div class=bar><i style="width:${pct}%"></i></div></div>`
  }).join('');
 }catch(e){progressList.innerHTML=''}
}
const REMEDY={DIRTY_RELEASE_PATH:"先处理相应机器发布路径的未提交修改；工具不会代你覆盖。",FIDE_DOWNLOAD_OR_VALIDATION_FAILED:"检查 last-good 与 FIDE 直连；坏下载不会替换有效缓存。",SOURCE_CIRCUIT_OPEN:"来源已熔断，等待提示时间后重试。",VISIT_BUDGET_EXHAUSTED:"旧运行记录的兼容状态；当前采集不设本机日访问额度，可直接续抓。",PARSER_LAYOUT_CHANGED:"来源页面结构变化；私有 raw 证据已保留，更新解析器后离线重放即可。",COMPLIANCE_POLICY_BLOCKED:"此操作违反数据边界（原始 HTML 不入库 / 人工数据只进 manual、community）。",GIT_PUSH_FAILED:"发布包已留在 outbox；恢复网络或代理后点“投递发布包”。",GIT_AUTH_FAILED:"GitHub 认证失败；请重新登录 gh 或更新凭据后再投递。",GIT_INDEX_LOCK_STALE:"存在无活跃 git 进程的 .git/index.lock；确认后删除该文件。",PARTIAL_FAILURE:"混合批次：逐场结果见上方“本批结果”卡片；成功赛事已保留。",VALIDATION_REGRESSION:"数据量或身份断言异常，检查本次日志和 staging，禁止发布。",EVENT_EMPTY:"来源记录不存在（Record not found），已保存证据并隔离 7 天；请核对该 TNR 在人工登记表中的链接是否正确。",PAIRINGS_NOT_PUBLISHED:"该赛事未公开逐轮对阵；名单与最终排名已保留为 standings-only。",TEAM_FORMAT_UNSUPPORTED:"团队赛轮次页暂不支持逐台解析；名单与排名已保留，等待解析器适配。",ROUND_COUNT_UNKNOWN:"无法确定轮数；已保留名单与排名，可人工补充队列轮数元数据后续跑。",PAIRING_REFS_OUTSIDE_ROSTER:"对阵中出现名单外棋手，疑似名单分页截断；请重新抓取该赛事。",RECEIPT_CHECK_FAILED:"云端回执查询失败；检查 gh 登录与 GitHub 路线后重试。"};
const WARN_CODES=new Set(["EVENT_EMPTY","PARTIAL_FAILURE","PAIRINGS_NOT_PUBLISHED","TEAM_FORMAT_UNSUPPORTED","ROUND_COUNT_UNKNOWN"]);
let wasRunning=false;
async function poll(){try{const s=await(await fetch('/api/state')).json();document.querySelectorAll('button').forEach(b=>{if(b.id!=='stop')b.disabled=!!s.running});stop.disabled=!s.running;dot.className='dot '+(s.running?'running':s.result==='ok'?'ok':s.result?(WARN_CODES.has(s.errorCode)||s.result==='partial'?'warn':'bad'):'');if(s.running){statusText.textContent=`${s.command} · ${s.stage||'running'}`;statusMeta.textContent=`run ${s.runId||''} · ${s.message||''}`;loadProgress();if(s.command==='event-queue')renderBatchResult()}else if(s.command){if(s.command==='event-queue'){const r=await renderBatchResult();statusText.textContent=batchStatusText(r);statusMeta.textContent=`run ${s.runId||''} · ${batchPublicationText(r)}${s.errorCode?' · '+s.errorCode:''}`}else{statusText.textContent=`${s.command} · ${s.result||'finished'}${s.errorCode?' · '+s.errorCode:''}`;statusMeta.textContent=(s.message||'')+(s.errorCode&&REMEDY[s.errorCode]?' 处理建议：'+REMEDY[s.errorCode]:'');renderBatchResult()}progressList.innerHTML='';if(wasRunning){loadQueue();if(s.command==='event-queue'){$('#captureMsg').textContent=''}}}else{statusText.textContent='空闲';statusMeta.textContent=''}wasRunning=!!s.running;const atBottom=log.scrollHeight-log.scrollTop-log.clientHeight<45;if(s.log){log.textContent=s.log;if(atBottom)log.scrollTop=log.scrollHeight}}catch(e){document.querySelectorAll('button').forEach(b=>b.disabled=true);stop.disabled=true;dot.className='dot bad';statusText.textContent='面板连接中断';statusMeta.textContent='本机面板服务不可达；这不代表任务仍在运行。请重新打开面板确认。';progressList.innerHTML='';wasRunning=false}}
async function shutdown(){await post('/api/shutdown',{});document.body.innerHTML='<p style="padding:40px">面板已退出。正在运行的维护者任务会继续，并可在重开面板后恢复查看。</p>'}
renderTabs();loadQueue();renderBatchResult();poll();setInterval(poll,1500);setInterval(loadQueue,10000);
</script></body></html>"""


def pick_port() -> int:
    for port in range(8763, 8780):
        try:
            probe = socket.create_connection(("127.0.0.1", port), timeout=0.3)
        except OSError:
            return port
        probe.close()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=1) as response:
                if PING_TOKEN in response.read().decode(errors="replace"):
                    webbrowser.open(f"http://127.0.0.1:{port}/")
                    raise SystemExit(0)
        except SystemExit:
            raise
        except Exception:
            continue
    raise SystemExit("8763-8779 端口均被占用")


def main() -> int:
    if not REFRESH.exists():
        raise SystemExit(f"找不到 {REFRESH}")
    port = pick_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    PORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    PORT_FILE.write_text(str(port), encoding="utf-8")
    url = f"http://127.0.0.1:{port}/"
    print(f"维护者本地数据控制台：{url}")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    threading.Thread(target=automation_monitor, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        monitor_stop.set()
        PORT_FILE.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
