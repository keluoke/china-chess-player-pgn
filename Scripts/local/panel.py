#!/usr/bin/env python3
"""Maintainer-local control panel for the policy-enforced refresh entrypoint."""

from __future__ import annotations

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
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent))

from run_manager import current_payload, process_alive  # noqa: E402
from source_policy import local_state_root  # noqa: E402

REFRESH = SCRIPT_DIR / "refresh.sh"
QUEUE_PATH = REPO_ROOT / "docs" / "data" / "audit" / "domestic-event-queue.json"
STATE_ROOT = local_state_root()
CAPTURE_STATE_PATH = STATE_ROOT / "chess-results" / "capture-state.json"
PORT_FILE = STATE_ROOT / "panel.port"
PING_TOKEN = "china-chess-local-panel-v2"
CSRF_TOKEN = secrets.token_urlsafe(24)

ALLOWED_COMMANDS = {
    "health", "all", "registry", "event-queue", "candidates",
    "bulk", "bulk-full", "push", "reindex",
}
EXTRA_TOKEN = re.compile(r"^[A-Za-z0-9_.:/=-]{1,200}$")
children: set[subprocess.Popen[bytes]] = set()
children_lock = threading.Lock()


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
    proc = subprocess.Popen(
        argv,
        cwd=REPO_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=env,
    )
    with children_lock:
        children.add(proc)

    def reap() -> None:
        proc.wait()
        with children_lock:
            children.discard(proc)

    threading.Thread(target=reap, daemon=True).start()
    return True, "已启动；运行状态、日志和锁会在面板重启后继续保留"


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


def queue_payload() -> dict:
    try:
        payload = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"totals": {}, "targets": [], "missing": True}
    try:
        capture_payload = json.loads(CAPTURE_STATE_PATH.read_text(encoding="utf-8"))
        captures = capture_payload.get("events") if isinstance(capture_payload, dict) else {}
        captures = captures if isinstance(captures, dict) else {}
    except (OSError, json.JSONDecodeError):
        captures = {}
    targets = []
    for item in payload.get("targets") or []:
        tournament_id = re.sub(r"\D", "", str(item.get("tournamentID") or ""))
        if not tournament_id:
            continue
        capture = captures.get(tournament_id)
        capture = capture if isinstance(capture, dict) else {}
        targets.append({
            "tournamentID": tournament_id,
            "eventName": str(item.get("eventName") or f"tnr{tournament_id}")[:180],
            "category": str(item.get("category") or "国内赛事")[:80],
            "priorityScore": item.get("priorityScore"),
            "status": "privately-captured" if capture else "pending-private-capture",
            "lastCapturedAt": capture.get("capturedAt"),
            "captureStats": {
                "players": capture.get("players"),
                "rounds": capture.get("rounds"),
                "standings": capture.get("standings"),
            } if capture else None,
        })
    return {
        "generatedAt": payload.get("generatedAt"),
        "totals": payload.get("totals") or {},
        "targets": targets,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "ChinaChessPanel/2.0"

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
        if self.path in {"/", "/index.html"}:
            page = PAGE.replace("{{CSRF}}", CSRF_TOKEN).replace("{{REPO}}", str(REPO_ROOT))
            self.send_body(200, page.encode(), "text/html; charset=utf-8")
        elif self.path == "/api/ping":
            self.send_json({"panel": PING_TOKEN})
        elif self.path == "/api/state":
            self.send_json(durable_state())
        elif self.path == "/api/queue":
            self.send_json(queue_payload())
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
        elif self.path == "/api/stop":
            ok, message = stop_job()
            self.send_json({"ok": ok, "message": message}, 200 if ok else 409)
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
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,"PingFang SC",sans-serif}.wrap{max-width:1120px;margin:auto;padding:28px 20px 70px}h1{margin:0;font-size:1.7rem}h1 small{display:block;color:var(--muted);font-size:.84rem;font-weight:400;margin-top:5px}.notice{margin:18px 0;padding:14px 16px;border:1px solid var(--line);border-radius:12px;background:var(--card)}.notice b{color:var(--ok)}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:13px}.card{background:var(--card);border:1px solid var(--line);border-radius:13px;padding:15px}.head{display:flex;justify-content:space-between;gap:8px}.badge{color:var(--blue);font-size:.76rem}.desc,.meta{color:var(--muted);font-size:.84rem}.meta{margin-top:8px}.actions{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}button{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:8px;padding:7px 12px;cursor:pointer}button.primary{background:var(--blue);border-color:var(--blue);color:white}button.danger{color:var(--bad)}button:disabled{opacity:.45;cursor:not-allowed}.status{display:flex;gap:10px;align-items:flex-start;margin:22px 0;padding:14px;background:var(--card);border:1px solid var(--line);border-radius:12px}.dot{width:11px;height:11px;border-radius:50%;background:var(--muted);margin-top:6px}.dot.running{background:var(--blue);animation:pulse 1.2s infinite}.dot.ok{background:var(--ok)}.dot.bad{background:var(--bad)}@keyframes pulse{50%{opacity:.35}}#statusMeta{color:var(--muted);font-size:.83rem}#log{background:#0d1117;color:#d7e0ea;border-radius:11px;padding:14px;height:390px;overflow:auto;white-space:pre-wrap;font:12px/1.5 ui-monospace,SFMono-Regular,monospace}table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line)}th,td{padding:8px;border-bottom:1px solid var(--line);text-align:left;font-size:.83rem}h2{font-size:1.15rem;margin-top:28px}footer{display:flex;justify-content:space-between;color:var(--muted);font-size:.8rem;margin-top:20px}a{color:var(--blue);cursor:pointer}
</style></head><body><div class="wrap">
<h1>维护者本地数据控制台<small>社区只提交线索、勘误和人工知识；所有网络采集均由维护者本机执行</small></h1>
<div class="notice"><b>合规边界已启用：</b>Chess-Results 默认 link-only，原始页面和解析结果只保存在仓库外；FIDE/Lichess 只有通过校验且列入 release manifest 的文件才能交付。</div>
<div class="status"><span id="dot" class="dot"></span><div style="flex:1"><b id="statusText">读取状态…</b><div id="statusMeta"></div></div><button id="stop" class="danger" onclick="stopJob()">中止任务</button></div>
<div class="grid" id="tasks"></div>
<h2>Chess-Results 私有目标队列</h2><div class="actions"><button onclick="queueTop(1)">采集前 1 个</button><button onclick="queueTop(3)">采集前 3 个</button><button onclick="queueTop(10)">采集前 10 个</button></div>
<table><thead><tr><th>赛事</th><th>tnr</th><th>优先级</th><th>私有抓取状态</th><th>动作</th></tr></thead><tbody id="queue"></tbody></table>
<h2>本次运行日志</h2><div id="log">尚无运行记录。</div>
<footer><span>仓库：{{REPO}}</span><a onclick="shutdown()">退出面板</a></footer></div>
<script>
const TOKEN="{{CSRF}}";
const tasks=document.querySelector('#tasks'), queue=document.querySelector('#queue');
const stop=document.querySelector('#stop'), dot=document.querySelector('#dot');
const statusText=document.querySelector('#statusText'), statusMeta=document.querySelector('#statusMeta');
const log=document.querySelector('#log');
const TASKS=[
 {cmd:"health",name:"健康检查",badge:"只读",desc:"检查磁盘、FIDE last-good、发布路径、TLS 和三个来源的直连状态。",meta:"建议每次正式抓取前运行"},
 {cmd:"all",name:"安全常规刷新",badge:"独立阶段",desc:"FIDE 满 25 天才更新；另采集队列前 3 个 Chess-Results 赛事到私有区。",meta:"一个来源失败不会抹掉另一个来源的成功结果"},
 {cmd:"registry",name:"FIDE 注册表",badge:"可发布",desc:"临时下载、ZIP/语义/人数/分片/勘误校验，通过后才原子晋升。",meta:"姓名和等级分唯一权威；建议每月"},
 {cmd:"event-queue",name:"Chess-Results 赛事",badge:"仅私有",desc:"抓取目标队列并保存私有原始证据和解析结果，不发布 HTML、排名或 PGN。",meta:"默认 3 个；全局限速、预算与熔断"},
 {cmd:"candidates",name:"姓名候选",badge:"仅私有",desc:"生成待人工审查的姓名候选；不会自动写 manual/community 或覆盖 registry。",meta:"人工确认后再走 name-corrections/人工别名机制"},
 {cmd:"bulk",full:"bulk-full",name:"Lichess Broadcast",badge:"CC BY-SA 4.0",desc:"在暂存区验证分片并重建数据包，manifest 保留许可证和署名链接。",meta:"建议每月；全量刷新流量很大"},
 {cmd:"push",name:"重投最近发布包",badge:"不抓取",desc:"只 force-push 已提交且带 manifest 的最近发布，不重新访问任何数据源。",meta:"用于 GitHub 网络恢复后重投"},
 {cmd:"reindex",name:"本地离线诊断",badge:"不交付",desc:"本地重建派生索引用于诊断；不会自动暂存、提交或推送。",meta:"通常由 Actions 完成"}
];
function esc(v){return String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
function post(path,body){return fetch(path,{method:"POST",headers:{"Content-Type":"application/json","X-Panel-Token":TOKEN},body:JSON.stringify(body)}).then(r=>r.json())}
function renderCards(){tasks.innerHTML=TASKS.map(t=>`<div class=card><div class=head><b>${esc(t.name)}</b><span class=badge>${esc(t.badge)}</span></div><div class=desc>${esc(t.desc)}</div><div class=meta>${esc(t.meta)}</div><div class=actions><button class=primary onclick="runCmd('${t.cmd}',[],false)">${t.cmd==='health'?'检查':t.cmd==='push'?'重投':t.cmd==='reindex'?'离线运行':'开始'}</button>${t.full?`<button onclick="runCmd('${t.full}',[],true)">全量刷新</button>`:''}</div></div>`).join('')}
async function runCmd(cmd,extra,full){if(full&&!confirm("全量刷新会消耗大量流量，确认继续？"))return;const r=await post('/api/run',{cmd,extra});if(!r.ok)alert(r.message);setTimeout(poll,400)}
async function stopJob(){if(!confirm("中止当前任务？未通过校验的暂存数据不会发布。"))return;const r=await post('/api/stop',{});alert(r.message)}
function queueTop(n){runCmd('event-queue',['--from-queue',String(n)],false)}
async function loadQueue(){const q=await(await fetch('/api/queue')).json();queue.innerHTML=(q.targets||[]).slice(0,30).map(t=>{const captured=t.lastCapturedAt?`已私有抓取<br><span class=meta>${esc(t.lastCapturedAt)} · ${esc(t.captureStats?.players??'-')} 人 / ${esc(t.captureStats?.rounds??'-')} 轮</span>`:'待私有抓取';return `<tr><td>${esc(t.eventName)}</td><td>${esc(t.tournamentID)}</td><td>${esc(t.priorityScore)}</td><td>${captured}</td><td><button onclick="runCmd('event-queue',['${t.tournamentID}'],false)">${t.lastCapturedAt?'重新抓取':'私有采集'}</button></td></tr>`}).join('')||'<tr><td colspan=5>队列为空</td></tr>'}
const REMEDY={DIRTY_RELEASE_PATH:"先处理相应机器发布路径的未提交修改；工具不会代你覆盖。",FIDE_DOWNLOAD_OR_VALIDATION_FAILED:"检查 last-good 与 FIDE 直连；坏下载不会替换有效缓存。",SOURCE_CIRCUIT_OPEN:"来源已熔断，等待提示时间后重试。",VISIT_BUDGET_EXHAUSTED:"今日访问预算用完，明日再运行。",PARSER_LAYOUT_CHANGED:"来源页面结构变化，保留本次私有证据并更新解析器。",COMPLIANCE_POLICY_BLOCKED:"此操作违反当前 link-only/人工数据边界。",GIT_PUSH_FAILED:"开启 GitHub 代理后点“重投最近发布包”。",VALIDATION_REGRESSION:"数据量或身份断言异常，检查本次日志和 staging，禁止发布。"};
async function poll(){try{const s=await(await fetch('/api/state')).json();document.querySelectorAll('button').forEach(b=>{if(b.id!=='stop')b.disabled=!!s.running});stop.disabled=!s.running;dot.className='dot '+(s.running?'running':s.result==='ok'?'ok':s.result?'bad':'');if(s.running){statusText.textContent=`${s.command} · ${s.stage||'running'}`;statusMeta.textContent=`run ${s.runId||''} · ${s.message||''} · 日志 ${s.logPath||''}`}else if(s.command){statusText.textContent=`${s.command} · ${s.result||'finished'}${s.errorCode?' · '+s.errorCode:''}`;statusMeta.textContent=(s.message||'')+(s.errorCode&&REMEDY[s.errorCode]?' 处理建议：'+REMEDY[s.errorCode]:'')}else{statusText.textContent='空闲';statusMeta.textContent=''}const atBottom=log.scrollHeight-log.scrollTop-log.clientHeight<45;if(s.log){log.textContent=s.log;if(atBottom)log.scrollTop=log.scrollHeight}}catch(e){statusText.textContent='面板连接中断'}}
async function shutdown(){await post('/api/shutdown',{});document.body.innerHTML='<p style="padding:40px">面板已退出。正在运行的维护者任务会继续，并可在重开面板后恢复查看。</p>'}
renderCards();loadQueue();poll();setInterval(poll,1500);
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
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        PORT_FILE.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
