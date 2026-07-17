#!/usr/bin/env python3
"""Serve the local data centre and a loopback-only targeted-capture console.

The console deliberately does not fetch data itself.  Its only write action is
to start the existing policy-enforced target runner, which delegates source
requests to ``refresh.sh event-queue``.  It is intended for a maintainer's
local machine and never binds to a network interface.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[2]
CENTER = ROOT / "local-data-center"
COLLECTION = CENTER / "collection"
RUNNER = ROOT / "Scripts" / "local" / "targeted_series_capture.py"
STATE_PATH = COLLECTION / "run-state.json"
PLAN_PATH = COLLECTION / "target-plan.json"
TASK_OVERRIDES_PATH = COLLECTION / "task-overrides.json"
LOG_PATH = COLLECTION / "capture.log"
PANEL_STATE_PATH = COLLECTION / "panel-state.json"
CAPTURE_STATE_PATH = (
    Path.home() / "Library" / "Application Support" / "ChinaChessPlayerPGN" / "chess-results" / "capture-state.json"
)
EVENT_PGN_ARCHIVE = ROOT / "data" / "generated" / "chess-results-event-pgn"
EVENT_DETAILS = ROOT / "data" / "generated" / "chess-results-event-details"
LOCK_PATH = ROOT / ".git" / "index.lock"
TNR_PATTERN = re.compile(r"(?:tnr)?(\d{4,9})", re.IGNORECASE)


def read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def normalize_tnr(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "://" in raw:
        parsed = urlparse(raw)
        host = (parsed.hostname or "").lower()
        if host != "chess-results.com" and not host.endswith(".chess-results.com"):
            return ""
    match = TNR_PATTERN.search(raw)
    return match.group(1) if match else ""


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def pid_alive(value: Any) -> bool:
    try:
        pid = int(value)
        if pid <= 0:
            return False
        os.kill(pid, 0)
    except (OSError, TypeError, ValueError):
        return False
    return True


def active_retry_after(state: dict[str, Any]) -> str | None:
    """Find an active transient source backoff for the checkpoint batch.

    Quarantine review timestamps are deliberately not a runner pause: the
    collector has already isolated that target and may continue with the rest
    of the saved batch.
    """
    now = dt.datetime.now(dt.timezone.utc)
    events = read_json(CAPTURE_STATE_PATH, {}).get("events", {})
    values: list[str] = []
    for tournament_id in state.get("currentTargets") or []:
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
            values.append(value)
    return max(values) if values else None


def tail(path: Path, limit: int = 18000) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - limit))
            text = handle.read().decode("utf-8", errors="replace")
        # The dashboard is a data-centre frontend, not the maintainer's raw
        # evidence workspace.  Keep operational outcomes but never expose
        # private run paths or an upstream source name through log output.
        text = re.sub(r"/(?:Users|Volumes|private|var)/[^\s\"']+", "[本机私有路径]", text)
        text = re.sub(r"chess-results", "来源", text, flags=re.IGNORECASE)
        return text if size <= limit else "…（仅显示日志末尾）\n" + text
    except OSError:
        return "尚无抓取日志。"


class Panel:
    def __init__(self) -> None:
        self.token = secrets.token_urlsafe(32)
        self.child: subprocess.Popen[str] | None = None
        self.lock = threading.Lock()

    def runner_active(self, state: dict[str, Any]) -> bool:
        return bool((self.child and self.child.poll() is None) or pid_alive(state.get("pid")))

    def capture_summary(self) -> dict[str, Any]:
        events = read_json(CAPTURE_STATE_PATH, {}).get("events", {})
        counts: dict[str, int] = {}
        attention: list[dict[str, Any]] = []
        for tnr, item in events.items():
            status = str(item.get("status") or "unknown")
            counts[status] = counts.get(status, 0) + 1
            if status != "complete":
                attention.append({
                    "tnr": str(tnr), "status": status,
                    "errorCode": item.get("errorCode"),
                    "nextRetryAt": item.get("nextRetryAt"),
                    "attempts": item.get("attempts"),
                    "updatedAt": item.get("updatedAt"),
                })
        attention.sort(key=lambda item: (str(item.get("nextRetryAt") or "9999"), item["tnr"]))
        return {"counts": counts, "attention": attention[:120]}

    def batch_progress(self, state: dict[str, Any], active: bool) -> dict[str, Any]:
        """Summarize the current batch without exposing private run paths.

        A batch cannot advance until the event records and their complete PGN
        archives have both finished.  The event collector checkpoint reaches
        ``complete`` first, so distinguish that downstream phase rather than
        making a completed 10-event batch look frozen at the same index.
        """
        targets = [str(item) for item in state.get("currentTargets") or []]
        events = read_json(CAPTURE_STATE_PATH, {}).get("events", {})
        statuses = [str(events.get(tnr, {}).get("status") or "pending") for tnr in targets]
        complete = sum(status == "complete" for status in statuses)
        total = len(targets)
        pgn_complete = sum(
            (EVENT_PGN_ARCHIVE / f"tnr{tnr}.pgn").is_file()
            and (EVENT_PGN_ARCHIVE / f"tnr{tnr}.pgn").stat().st_size > 0
            for tnr in targets
        )
        pgn_source_gaps = sum(self.source_explicitly_omits_pgn(tnr) for tnr in targets)
        pgn_resolved = pgn_complete + pgn_source_gaps
        if total and complete == total and pgn_resolved < total and active:
            phase = "pgn-and-finalize"
            message = "赛事资料已齐，正在逐场补抓完整 PGN 棋谱并进行本地整理。"
        elif total and complete == total and pgn_resolved < total:
            phase = "pgn-pending"
            message = "赛事资料已齐，但完整 PGN 棋谱尚未补全；本批不会提前跳过。"
        elif total and complete == total and pgn_source_gaps:
            phase = "source-pgn-gaps"
            message = f"赛事资料已齐；其中 {pgn_source_gaps} 场的配对页明确未公开棋谱链接，已记录为来源缺口。"
        elif total and complete == total:
            phase = "ready-to-checkpoint"
            message = "赛事资料已齐，正在写入批次完成检查点。"
        elif active:
            phase = "event-capture"
            message = "正在采集赛事资料（名单、排名、逐轮对阵与结果）。"
        else:
            phase = "pending"
            message = "等待本批开始。"
        return {
            "phase": phase, "message": message, "complete": complete,
            "total": total, "pgnComplete": pgn_complete, "pgnSourceGaps": pgn_source_gaps,
        }

    @staticmethod
    def source_explicitly_omits_pgn(tournament_id: str) -> bool:
        payload = read_json(EVENT_DETAILS / f"tnr{tournament_id}.json", {})
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

    def payload(self) -> dict[str, Any]:
        state = read_json(STATE_PATH, {})
        plan = read_json(PLAN_PATH, {})
        active = self.runner_active(state)
        retry_after = active_retry_after(state)
        stored_status = state.get("status") or "pending"
        status = "running" if active else (
            "interrupted" if stored_status == "running"
            else ("stopped" if stored_status == "waiting" and not retry_after else stored_status)
        )
        return {
            "status": status,
            "runnerActive": active,
            "state": state,
            "plan": {"totals": plan.get("totals", {}), "bySeries": plan.get("bySeries", {}), "generatedAt": plan.get("generatedAt")},
            "capture": self.capture_summary(),
            "batchProgress": self.batch_progress(state, active),
            "retryAfter": retry_after,
            "blockers": (["检测到 Git 索引锁：确认没有 Git 进程后再清理该锁。"] if LOCK_PATH.exists() else []),
            "logTail": tail(LOG_PATH),
        }

    def task_payload(self) -> dict[str, Any]:
        plan = read_json(PLAN_PATH, {})
        override = read_json(TASK_OVERRIDES_PATH, {})
        excluded = override.get("excluded") if isinstance(override, dict) else {}
        excluded = excluded if isinstance(excluded, dict) else {}
        capture_events = read_json(CAPTURE_STATE_PATH, {}).get("events", {})
        tasks: list[dict[str, Any]] = []
        for item in plan.get("targets", []):
            tnr = str(item.get("tournamentID") or "")
            capture = capture_events.get(tnr, {}) if isinstance(capture_events, dict) else {}
            status = str(capture.get("status") or ("structured-record" if item.get("existingRecord") else "pending"))
            tasks.append({
                "tnr": tnr, "title": item.get("displayName") or f"tnr{tnr}",
                "series": item.get("seriesLabel") or item.get("series") or "", "date": item.get("date") or "",
                "url": item.get("sourceURL") or f"https://chess-results.com/tnr{tnr}.aspx?lan=1",
                "status": status, "errorCode": capture.get("errorCode"),
                "nextRetryAt": capture.get("nextRetryAt"), "manual": item.get("series") == "manual-review",
            })
        removed = [
            {"tnr": str(tnr), "title": info.get("title") or f"tnr{tnr}", "removedAt": info.get("removedAt"), "reason": info.get("reason") or ""}
            for tnr, info in excluded.items() if isinstance(info, dict)
        ]
        tasks.sort(key=lambda item: (item["series"], item["date"], item["tnr"]))
        removed.sort(key=lambda item: str(item.get("removedAt") or ""), reverse=True)
        return {"tasks": tasks, "removed": removed, "generatedAt": plan.get("generatedAt")}

    def reset_checkpoint_for_review(self) -> None:
        state = read_json(STATE_PATH, {})
        state.update({
            "status": "pending", "nextBatchIndex": 0, "completedBatches": 0,
            "currentBatch": None, "currentTargets": [], "pid": None,
            "nextRetryAt": None,
            "lastOutcome": {"result": "review-updated", "message": "人工审核任务清单已变更；下次续抓将按新清单重新排程，已保存页面会复用。"},
            "updatedAt": now_iso(),
        })
        write_json(STATE_PATH, state)

    def change_tasks(self, action: str, values: list[Any]) -> tuple[bool, str]:
        with self.lock:
            if self.runner_active(read_json(STATE_PATH, {})):
                return False, "抓取进行中，不能修改任务清单。"
            normalized = list(dict.fromkeys(value for value in (normalize_tnr(item) for item in values) if value))
            if not normalized:
                return False, "没有识别到有效 TNR（仅接受 4–9 位数字或本站赛事链接）。"
            override = read_json(TASK_OVERRIDES_PATH, {})
            if not isinstance(override, dict):
                override = {}
            override.setdefault("schemaVersion", 1)
            override.setdefault("excluded", {})
            override.setdefault("additions", {})
            plan = read_json(PLAN_PATH, {})
            known = {str(item.get("tournamentID")): item for item in plan.get("targets", [])}
            if action == "add":
                for tnr in normalized:
                    override["excluded"].pop(tnr, None)
                    override["additions"].setdefault(tnr, {"addedAt": now_iso()})
                message = f"已补充 {len(normalized)} 个待抓 TNR。"
            elif action == "exclude":
                for tnr in normalized:
                    item = known.get(tnr, {})
                    override["excluded"][tnr] = {
                        "removedAt": now_iso(), "title": item.get("displayName") or f"tnr{tnr}",
                        "reason": "人工审核移除",
                    }
                message = f"已从待抓清单移除 {len(normalized)} 个 TNR。"
            elif action == "restore":
                for tnr in normalized:
                    override["excluded"].pop(tnr, None)
                message = f"已恢复 {len(normalized)} 个 TNR。"
            else:
                return False, "未知任务操作。"
            write_json(TASK_OVERRIDES_PATH, override)
            result = subprocess.run([sys.executable, str(RUNNER), "--plan"], cwd=ROOT, capture_output=True, text=True, check=False)
            if result.returncode:
                return False, (result.stderr or result.stdout or "重建本机任务计划失败。").strip()
            self.reset_checkpoint_for_review()
            return True, message

    def start_resume(self) -> tuple[bool, str]:
        with self.lock:
            state = read_json(STATE_PATH, {})
            if self.runner_active(state):
                return False, "抓取器已经在运行。"
            if LOCK_PATH.exists():
                return False, "检测到 Git 索引锁，已阻止启动以保护本地数据。"
            retry_after = active_retry_after(state)
            if retry_after:
                return False, f"来源仍在退避期，{retry_after} 前不能续抓；此限制会阻止产生空失败运行。"
            COLLECTION.mkdir(parents=True, exist_ok=True)
            launcher_log = COLLECTION / "panel-launcher.log"
            handle = launcher_log.open("a", encoding="utf-8")
            plan = read_json(PLAN_PATH, {})
            targets_by_id = {
                str(item.get("tournamentID")): item for item in plan.get("targets", [])
            }
            saved_targets = [str(item) for item in state.get("currentTargets") or []]
            inferred_manual_scope = bool(saved_targets) and all(
                targets_by_id.get(item, {}).get("series") == "manual-review" for item in saved_targets
            )
            command = [sys.executable, str(RUNNER), "--run"]
            if state.get("taskScope") == "manual-only" or inferred_manual_scope:
                command.append("--only-manual")
            else:
                command.append("--refresh-existing")
            self.child = subprocess.Popen(
                command,
                cwd=ROOT,
                stdout=handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                text=True,
            )
            return True, "已启动：将从已保存的失败批次继续；页面会自动刷新。"

    def regenerate_plan(self) -> tuple[bool, str]:
        with self.lock:
            state = read_json(STATE_PATH, {})
            if self.runner_active(state):
                return False, "抓取进行中，不能重建计划。"
            result = subprocess.run(
                [sys.executable, str(RUNNER), "--plan"], cwd=ROOT,
                capture_output=True, text=True, check=False,
            )
            if result.returncode:
                return False, (result.stderr or result.stdout or "重建计划失败。").strip()
            return True, "已离线重建目标计划和缺 TNR 清单；未访问任何来源。"


class Handler(SimpleHTTPRequestHandler):
    panel: Panel

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(CENTER), **kwargs)

    def log_message(self, _format: str, *_args: Any) -> None:
        # Browser polling should not flood the terminal.
        return

    def send_json(self, payload: dict[str, Any], status: int = HTTPStatus.OK) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/targeted-capture/state":
            self.send_json(self.panel.payload())
            return
        if path == "/api/targeted-capture/tasks":
            self.send_json(self.panel.task_payload())
            return
        if path in {"/capture.html", "/capture", "/tasks.html", "/tasks"}:
            filename = "tasks.html" if path in {"/tasks.html", "/tasks"} else "capture.html"
            page = (CENTER / filename).read_text(encoding="utf-8")
            page = page.replace("__TARGET_CAPTURE_TOKEN__", self.panel.token)
            raw = page.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        if self.headers.get("X-Target-Capture-Token") != self.panel.token:
            self.send_json({"ok": False, "message": "本机控制令牌无效，请刷新页面。"}, HTTPStatus.FORBIDDEN)
            return
        path = urlparse(self.path).path
        try:
            length = min(int(self.headers.get("Content-Length") or 0), 100_000)
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except (ValueError, UnicodeDecodeError):
            self.send_json({"ok": False, "message": "请求内容不是有效 JSON。"}, HTTPStatus.BAD_REQUEST)
            return
        if path == "/api/targeted-capture/resume":
            ok, message = self.panel.start_resume()
        elif path == "/api/targeted-capture/replan":
            ok, message = self.panel.regenerate_plan()
        elif path in {"/api/targeted-capture/tasks/add", "/api/targeted-capture/tasks/exclude", "/api/targeted-capture/tasks/restore"}:
            action = path.rsplit("/", 1)[-1]
            values = body.get("values") if isinstance(body, dict) else []
            values = values if isinstance(values, list) else []
            ok, message = self.panel.change_tasks(action, values)
        else:
            self.send_json({"ok": False, "message": "未知操作。"}, HTTPStatus.NOT_FOUND)
            return
        self.send_json({"ok": ok, "message": message, "payload": self.panel.payload(), "tasks": self.panel.task_payload()}, HTTPStatus.OK if ok else HTTPStatus.CONFLICT)


def choose_port(port: int) -> int:
    for candidate in range(port, port + 16):
        try:
            probe = ThreadingHTTPServer(("127.0.0.1", candidate), Handler)
        except OSError:
            continue
        probe.server_close()
        return candidate
    raise SystemExit(f"端口 {port}-{port + 15} 均不可用。")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    port = choose_port(args.port)
    Handler.panel = Panel()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/capture.html"
    COLLECTION.mkdir(parents=True, exist_ok=True)
    PANEL_STATE_PATH.write_text(json.dumps({"url": url, "pid": os.getpid()}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"本机目标赛事控制台：{url}", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
