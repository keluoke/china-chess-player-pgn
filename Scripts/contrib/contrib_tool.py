#!/usr/bin/env python3
"""社区贡献工具:本地一键抓取某棋手 / 某赛事的 Chess-Results 数据并自动提交。

设计要点(与仓库治理机制配套,见 docs/GOVERNANCE.md):

- 抓取跑在贡献者自己的住宅 IP 上,分摊维护者机器 2000 visits/day 的限制;
- 所有产出只写入本工具的暂存目录,打包为 ``data/incoming/<submission-id>/``
  载荷:解析结果 + 原始 HTML / 原始 PGN 证据(gzip),附 manifest 与 sha256;
- 提交走 GitHub 设备码授权(浏览器输入 8 位码即可,无需安装 gh),工具通过
  REST API 自动 fork、提交、开 PR;没有 GitHub 账号可退化为打 zip 包;
- CI(Scripts/validate_incoming.py)离线复核:重新解析 HTML/重切 PGN,与提交
  的解析结果逐字节比对,防伪造;维护者本地核验后由 promote_incoming.py 入库;
- 上线后昵称进入 data/community/contributors.csv 鸣谢名录。

双击仓库根目录的「贡献工具-双击启动」即可运行;或:

    python3 Scripts/contrib/contrib_tool.py [--port 8765] [--no-browser]
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import gzip
import hashlib
import html as html_mod
import json
import pathlib
import re
import secrets
import shutil
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / "Scripts"))

import crawl_player_events as cpe  # noqa: E402
import fetch_event_pgn as fep  # noqa: E402
from sync_static_pgn import count_pgn_games, download_chess_results_pgn  # noqa: E402

TOOL_NAME = "cr-contrib"
TOOL_VERSION = "1.0"
STATE_DIR = HERE / ".state"
PROFILE_JSON = STATE_DIR / "profile.json"
VISITS_JSON = STATE_DIR / "visits.json"
GITHUB_JSON = STATE_DIR / "github.json"
PAYLOADS_DIR = STATE_DIR / "payloads"

CONFIG = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
UPSTREAM = f"{CONFIG['upstream_owner']}/{CONFIG['upstream_repo']}"
API = "https://api.github.com"

_LOCK = threading.Lock()
JOB: dict = {"kind": None, "status": "idle", "log": [], "result": {}, "auth": {}}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    with _LOCK:
        JOB["log"].append(f"[{dt.datetime.now().strftime('%H:%M:%S')}] {msg}")


def read_json(path: pathlib.Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: pathlib.Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def bump_visits(n: int) -> int:
    today = dt.date.today().isoformat()
    rec = read_json(VISITS_JSON, {})
    if rec.get("date") != today:
        rec = {"date": today, "count": 0}
    rec["count"] += n
    write_json(VISITS_JSON, rec)
    return rec["count"]


def visits_today() -> int:
    rec = read_json(VISITS_JSON, {})
    return rec.get("count", 0) if rec.get("date") == dt.date.today().isoformat() else 0


def parse_targets(text: str) -> list[dict]:
    """'8603677 tnr1234567 https://chess-results.com/tnr999.aspx' -> targets."""
    targets: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for token in re.split(r"[\s,;，、]+", text.strip()):
        if not token:
            continue
        m = re.search(r"tnr(\d{3,9})", token, re.IGNORECASE)
        if m:
            key = ("event", m.group(1))
        elif re.fullmatch(r"\d{4,10}", token):
            key = ("player", token)
        else:
            raise ValueError(f"看不懂的目标:{token!r}(请输入 FIDE ID 或 tnr 赛事号/链接)")
        if key not in seen:
            seen.add(key)
            targets.append({"type": key[0], "id": key[1]})
    if not targets:
        raise ValueError("请至少输入一个 FIDE ID 或赛事号")
    if len(targets) > CONFIG["max_targets_per_run"]:
        raise ValueError(f"一次最多 {CONFIG['max_targets_per_run']} 个目标,请分批")
    return targets


# ---------------------------------------------------------------------------
# Grab job
# ---------------------------------------------------------------------------

def grab_job(targets: list[dict], fetch_pgn_for_players: bool) -> None:
    sub_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
    out = PAYLOADS_DIR / sub_id
    out.mkdir(parents=True, exist_ok=True)
    delay = float(CONFIG["request_delay_seconds"])
    files: list[dict] = []
    stats = {"players": 0, "participations": 0, "events": 0, "games": 0, "assignedGames": 0}
    visits = 0

    def save(rel: str, data: bytes) -> None:
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        files.append({"path": rel, "sha256": sha256_bytes(data), "bytes": len(data)})

    china_ids = fep.load_china_fide_ids()
    names = fep.load_name_index()
    event_queue: list[str] = [t["id"] for t in targets if t["type"] == "event"]

    for t in targets:
        if t["type"] != "player":
            continue
        fid = t["id"]
        log(f"抓取棋手 FIDE {fid} 的赛事列表…")
        sink: list[str] = []
        try:
            rows = cpe.search_player(fid, html_sink=sink)
        except Exception as exc:
            log(f"⚠️ FIDE {fid} 抓取失败:{exc}")
            continue
        visits += 2
        stats["players"] += 1
        stats["participations"] += len(rows)
        save(f"players/{fid}/rows.json", json.dumps(rows, ensure_ascii=False, indent=2).encode("utf-8"))
        if sink:
            save(f"players/{fid}/spielersuche.html.gz", gzip.compress(sink[0].encode("utf-8")))
        log(f"✓ FIDE {fid}:{len(rows)} 项赛事记录")
        if fetch_pgn_for_players:
            recent = [r["tnrid"] for r in rows if r.get("tnrid")][:5]
            event_queue.extend(x for x in recent if x not in event_queue)
        time.sleep(delay)

    for tid in event_queue:
        log(f"抓取赛事 tnr{tid} 的对局 PGN…")
        try:
            pgn = download_chess_results_pgn("", tid)
        except Exception as exc:
            log(f"⚠️ tnr{tid} 下载失败:{exc}")
            continue
        visits += 2
        games = count_pgn_games(pgn)
        if not games:
            log(f"tnr{tid} 无公开 PGN,跳过")
            time.sleep(delay)
            continue
        save(f"events/tnr{tid}/raw.pgn.gz", gzip.compress(pgn.encode("utf-8")))
        split_root = out / "events" / f"tnr{tid}" / "split"
        result = fep.process_event(tid, china_ids, names, split_root.parent, overwrite=True,
                                   dry_run=False, pgn_text=pgn)
        # process_event writes into <parent>/tnr<tid>/ - relocate into split/
        produced = sorted((split_root.parent / f"tnr{tid}").glob("*.pgn"))
        split_root.mkdir(parents=True, exist_ok=True)
        for p in produced:
            data = p.read_bytes()
            rel = f"events/tnr{tid}/split/{p.name}"
            (out / rel).write_bytes(data)
            files.append({"path": rel, "sha256": sha256_bytes(data), "bytes": len(data)})
        shutil.rmtree(split_root.parent / f"tnr{tid}", ignore_errors=True)
        stats["events"] += 1
        stats["games"] += games
        stats["assignedGames"] += result.get("assigned", 0)
        log(f"✓ tnr{tid}:{games} 盘,其中 {result.get('assigned', 0)} 盘归属 {result.get('players', 0)} 名中国棋手")
        time.sleep(delay)

    profile = read_json(PROFILE_JSON, {})
    manifest = {
        "schemaVersion": 1,
        "id": sub_id,
        "createdAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "contributor": {"nickname": profile.get("nickname", ""), "github": profile.get("github", "")},
        "targets": targets,
        "visits": visits,
        "stats": stats,
        "files": sorted(files, key=lambda f: f["path"]),
    }
    write_json(out / "manifest.json", manifest)
    total = bump_visits(visits)
    log(f"完成。本次约 {visits} 次访问,今日累计约 {total} 次"
        + (f"(已超过软上限 {CONFIG['daily_visit_soft_limit']},建议明天再抓)" if total > CONFIG["daily_visit_soft_limit"] else ""))
    if not stats["players"] and not stats["events"]:
        raise RuntimeError("没有抓到任何数据,不生成提交载荷")
    with _LOCK:
        JOB["result"] = {"submissionID": sub_id, "stats": stats}
    log(f"载荷已就绪:{sub_id}(下一步:自动提交,或打包 zip)")


# ---------------------------------------------------------------------------
# GitHub REST (stdlib only)
# ---------------------------------------------------------------------------

def gh_request(url: str, token: str | None = None, data: dict | None = None,
               method: str | None = None, accept: str = "application/vnd.github+json"):
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, method=method or ("POST" if body else "GET"))
    req.add_header("Accept", accept)
    req.add_header("User-Agent", f"{TOOL_NAME}/{TOOL_VERSION}")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def device_flow_token() -> str:
    saved = read_json(GITHUB_JSON, {})
    if saved.get("token"):
        try:
            user = gh_request(f"{API}/user", token=saved["token"])
            log(f"已用保存的授权登录 GitHub:@{user['login']}")
            return saved["token"]
        except Exception:
            log("保存的授权已失效,重新授权…")
    client_id = CONFIG.get("github_oauth_client_id", "")
    if not client_id:
        raise RuntimeError("仓库尚未配置 GitHub OAuth client_id,请先用「打包 zip」方式提交")
    init = gh_request("https://github.com/login/device/code", data={"client_id": client_id, "scope": "public_repo"},
                      accept="application/json")
    with _LOCK:
        JOB["auth"] = {"code": init["user_code"], "url": init["verification_uri"]}
    log(f"请在浏览器打开 {init['verification_uri']} 并输入代码:{init['user_code']}")
    try:
        webbrowser.open(init["verification_uri"])
    except Exception:
        pass
    interval = int(init.get("interval", 5))
    deadline = time.time() + int(init.get("expires_in", 900))
    while time.time() < deadline:
        time.sleep(interval)
        poll = gh_request("https://github.com/login/oauth/access_token",
                          data={"client_id": client_id, "device_code": init["device_code"],
                                "grant_type": "urn:ietf:params:oauth:grant-type:device_code"},
                          accept="application/json")
        if poll.get("access_token"):
            with _LOCK:
                JOB["auth"] = {}
            write_json(GITHUB_JSON, {"token": poll["access_token"]})
            return poll["access_token"]
        if poll.get("error") == "slow_down":
            interval += 5
        elif poll.get("error") not in (None, "authorization_pending"):
            raise RuntimeError(f"GitHub 授权失败:{poll.get('error_description') or poll.get('error')}")
    raise RuntimeError("GitHub 授权超时,请重试")


def submit_job(sub_id: str) -> None:
    payload_dir = PAYLOADS_DIR / sub_id
    manifest = read_json(payload_dir / "manifest.json", None)
    if not manifest:
        raise RuntimeError("找不到该载荷,请先抓取")
    token = device_flow_token()
    user = gh_request(f"{API}/user", token=token)
    login = user["login"]

    log("准备 fork 仓库(已有则复用)…")
    try:
        gh_request(f"{API}/repos/{UPSTREAM}/forks", token=token, data={})
    except urllib.error.HTTPError as exc:
        if exc.code not in (202, 403):
            raise
    fork = None
    for _ in range(30):
        try:
            fork = gh_request(f"{API}/repos/{login}/{CONFIG['upstream_repo']}", token=token)
            break
        except Exception:
            time.sleep(2)
    if not fork:
        raise RuntimeError("fork 未就绪,请稍后重试")
    fork_full = fork["full_name"]
    default = fork["default_branch"]

    head = gh_request(f"{API}/repos/{fork_full}/git/ref/heads/{default}", token=token)
    base_sha = head["object"]["sha"]
    base_commit = gh_request(f"{API}/repos/{fork_full}/git/commits/{base_sha}", token=token)

    log("上传数据文件…")
    tree_items = []
    all_files = [dict(f) for f in manifest["files"]] + [
        {"path": "manifest.json"}]
    for f in all_files:
        data = (payload_dir / f["path"]).read_bytes()
        blob = gh_request(f"{API}/repos/{fork_full}/git/blobs", token=token,
                          data={"content": base64.b64encode(data).decode("ascii"), "encoding": "base64"})
        tree_items.append({"path": f"data/incoming/{sub_id}/{f['path']}",
                           "mode": "100644", "type": "blob", "sha": blob["sha"]})
    tree = gh_request(f"{API}/repos/{fork_full}/git/trees", token=token,
                      data={"base_tree": base_commit["tree"]["sha"], "tree": tree_items})

    nickname = manifest["contributor"].get("nickname") or login
    stats = manifest["stats"]
    title = f"社区数据贡献:{stats['players']} 名棋手 / {stats['events']} 项赛事({sub_id})"
    body = "\n".join([
        f"由 {TOOL_NAME} v{TOOL_VERSION} 自动生成的社区抓取载荷。",
        "",
        f"- 贡献者:{nickname}" + (f"(@{login})" if login else ""),
        f"- 目标:{'、'.join(t['type'] + ':' + t['id'] for t in manifest['targets'])}",
        f"- 棋手记录 {stats['participations']} 条;赛事 PGN {stats['events']} 项 / {stats['games']} 盘"
        f"(归属中国棋手 {stats['assignedGames']} 盘)",
        f"- 载荷路径:`data/incoming/{sub_id}/`(原始 HTML/PGN 证据已随载荷提交)",
        "",
        "CI 将离线复核证据一致性;维护者本地核验通过后由 `promote_incoming.py` 入库,",
        "上线后贡献者进入鸣谢名录。数据按 CC BY 4.0 授权。",
    ])
    commit = gh_request(f"{API}/repos/{fork_full}/git/commits", token=token,
                        data={"message": title + "\n\n" + body, "tree": tree["sha"], "parents": [base_sha]})
    branch = f"contrib/{sub_id}"
    gh_request(f"{API}/repos/{fork_full}/git/refs", token=token,
               data={"ref": f"refs/heads/{branch}", "sha": commit["sha"]})
    log("创建 Pull Request…")
    pr = gh_request(f"{API}/repos/{UPSTREAM}/pulls", token=token,
                    data={"title": title, "head": f"{login}:{branch}", "base": CONFIG["base_branch"],
                          "body": body, "maintainer_can_modify": True})
    with _LOCK:
        JOB["result"] = {"prURL": pr["html_url"], "submissionID": sub_id}
    log(f"✅ 已提交:{pr['html_url']}")


def package_job(sub_id: str) -> None:
    payload_dir = PAYLOADS_DIR / sub_id
    if not (payload_dir / "manifest.json").exists():
        raise RuntimeError("找不到该载荷,请先抓取")
    zip_path = REPO_ROOT / f"contribution-{sub_id}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(payload_dir.rglob("*")):
            if p.is_file():
                zf.write(p, f"data/incoming/{sub_id}/{p.relative_to(payload_dir)}")
    with _LOCK:
        JOB["result"] = {"zipPath": str(zip_path), "submissionID": sub_id}
    log(f"✅ 已打包:{zip_path}")
    log(f"上传方式:在 GitHub 仓库 https://github.com/{UPSTREAM}/issues/new 开一个 Issue,"
        "把 zip 拖进正文即可;或发给任一维护者。")


def run_job(kind: str, fn, *args) -> bool:
    with _LOCK:
        if JOB["status"] == "running":
            return False
        JOB.update({"kind": kind, "status": "running", "log": [], "result": {}, "auth": {}})

    def wrapper():
        try:
            fn(*args)
            with _LOCK:
                JOB["status"] = "done"
        except Exception as exc:  # noqa: BLE001
            log(f"❌ {exc}")
            with _LOCK:
                JOB["status"] = "error"

    threading.Thread(target=wrapper, daemon=True).start()
    return True


# ---------------------------------------------------------------------------
# Web UI
# ---------------------------------------------------------------------------

PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>中国国际象棋数据库 · 社区贡献工具</title><style>
body{font:15px/1.6 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;max-width:760px;
margin:24px auto;padding:0 16px;color:#1c2733;background:#f4f6fa}
h1{font-size:20px} .card{background:#fff;border:1px solid #dbe3ee;border-radius:12px;
padding:16px 18px;margin:14px 0;box-shadow:0 1px 3px rgba(20,40,80,.06)}
label{display:block;margin:8px 0 4px;font-weight:600;font-size:13px;color:#4a5a6d}
input,textarea{width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid #c7d2e0;
border-radius:8px;font:inherit} textarea{height:64px}
button{margin-top:10px;padding:9px 18px;border:0;border-radius:8px;background:#0866c6;color:#fff;
font:inherit;font-weight:600;cursor:pointer} button:disabled{background:#9db4cc}
button.ghost{background:#eef3fa;color:#0866c6}
#log{background:#0d1a26;color:#cfe3f5;border-radius:8px;padding:10px 12px;font:12px/1.7 ui-monospace,monospace;
max-height:260px;overflow:auto;white-space:pre-wrap;margin-top:10px;display:none}
.auth{background:#fff7e0;border:1px solid #edd28a;border-radius:8px;padding:10px;margin-top:10px;display:none}
.auth code{font-size:22px;font-weight:800;letter-spacing:3px}
.ok{color:#0a7a3d;font-weight:700} small{color:#68798c}
.result a{font-weight:700}</style></head><body>
<h1>♟️ 社区贡献工具 <small>抓取 Chess-Results 数据 → 自动提交 → 审核入库 → 鸣谢上榜</small></h1>
<div class="card"><label>你的昵称(用于网站鸣谢名录,必填)</label>
<input id="nickname" placeholder="例如:小李棋爸">
<label>GitHub 用户名(可选)</label><input id="github" placeholder="例如:xiaoli">
<button class="ghost" onclick="saveProfile()">保存</button> <span id="profileMsg"></span></div>
<div class="card"><label>要抓取的目标(棋手 FIDE ID 或赛事 tnr 号/链接,空格分隔,可混填)</label>
<textarea id="targets" placeholder="例如:8603677 tnr1111363 https://chess-results.com/tnr892911.aspx"></textarea>
<label style="font-weight:400"><input type="checkbox" id="withPgn" style="width:auto"> 同时抓取棋手最近 5 项赛事的对局 PGN(更耗访问额度)</label>
<button id="grabBtn" onclick="grab()">① 开始抓取</button>
<span id="visits"></span></div>
<div class="card"><b>② 提交给数据库</b><br><small>推荐自动提交(首次需在浏览器里给 GitHub 授权,输入 8 位码即可);
没有 GitHub 账号就打包 zip,再在 Issue 里上传。</small><br>
<button id="submitBtn" onclick="act('submit')" disabled>自动提交(开 PR)</button>
<button id="zipBtn" class="ghost" onclick="act('package')" disabled>打包 zip</button>
<div class="auth" id="auth">GitHub 授权:请在 <a id="authUrl" target="_blank"></a> 输入代码 <code id="authCode"></code></div>
<div class="result" id="result"></div></div>
<div id="log"></div>
<script>
let subID=null;
async function api(p,b){const r=await fetch(p,b?{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b)}:{});return r.json()}
async function saveProfile(){const r=await api("/api/profile",{nickname:nickname.value.trim(),github:github.value.trim()});
profileMsg.textContent=r.ok?"已保存 ✓":r.error;profileMsg.className=r.ok?"ok":""}
async function grab(){if(!nickname.value.trim()){alert("请先填写昵称并保存");return}
const r=await api("/api/grab",{targets:targets.value,withPgn:withPgn.checked});
if(r.error){alert(r.error);return} poll()}
async function act(kind){const r=await api("/api/"+kind,{submissionID:subID});if(r.error){alert(r.error);return} poll()}
let timer=null;
async function poll(){clearTimeout(timer);const s=await api("/api/state");
document.getElementById("log").style.display="block";
document.getElementById("log").textContent=s.job.log.join("\\n");
const el=document.getElementById("log");el.scrollTop=el.scrollHeight;
nickname.value=nickname.value||s.profile.nickname||"";github.value=github.value||s.profile.github||"";
visits.textContent=" 今日已用约 "+s.visitsToday+" 次访问";
if(s.job.auth&&s.job.auth.code){auth.style.display="block";authCode.textContent=s.job.auth.code;
authUrl.textContent=s.job.auth.url;authUrl.href=s.job.auth.url}else{auth.style.display="none"}
if(s.job.result&&s.job.result.submissionID){subID=s.job.result.submissionID}
submitBtn.disabled=zipBtn.disabled=!(subID&&s.job.status!=="running");
grabBtn.disabled=(s.job.status==="running");
if(s.job.result&&s.job.result.prURL){result.innerHTML="🎉 已提交:<a target=_blank href='"+s.job.result.prURL+"'>"+s.job.result.prURL+"</a><br>审核通过并上线后,你的昵称会出现在网站鸣谢名录里。"}
if(s.job.result&&s.job.result.zipPath){result.innerHTML="📦 zip 已生成:<code>"+s.job.result.zipPath+"</code>"}
if(s.job.status==="running")timer=setTimeout(poll,1000)}
window.onload=poll;
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def log_message(self, *args):  # quiet
        pass

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/state":
            with _LOCK:
                job = {k: JOB[k] for k in ("kind", "status", "log", "result", "auth")}
            self._json({"job": job, "profile": read_json(PROFILE_JSON, {}), "visitsToday": visits_today()})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        try:
            body = self._body()
            if self.path == "/api/profile":
                nickname = (body.get("nickname") or "").strip()
                if not (1 <= len(nickname) <= 20) or re.search(r"https?://", nickname):
                    return self._json({"ok": False, "error": "昵称需 1-20 字且不含链接"})
                github = (body.get("github") or "").strip().lstrip("@")
                if github and not re.fullmatch(r"[A-Za-z0-9-]{1,39}", github):
                    return self._json({"ok": False, "error": "GitHub 用户名格式不对"})
                write_json(PROFILE_JSON, {"nickname": nickname, "github": github})
                return self._json({"ok": True})
            if self.path == "/api/grab":
                targets = parse_targets(body.get("targets") or "")
                if not run_job("grab", grab_job, targets, bool(body.get("withPgn"))):
                    return self._json({"error": "已有任务在运行"})
                return self._json({"ok": True})
            if self.path == "/api/submit":
                if not run_job("submit", submit_job, body.get("submissionID")):
                    return self._json({"error": "已有任务在运行"})
                return self._json({"ok": True})
            if self.path == "/api/package":
                if not run_job("package", package_job, body.get("submissionID")):
                    return self._json({"error": "已有任务在运行"})
                return self._json({"ok": True})
            return self._json({"error": "not found"}, 404)
        except Exception as exc:  # noqa: BLE001
            return self._json({"error": str(exc)})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"社区贡献工具已启动:{url}(Ctrl+C 退出)")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
