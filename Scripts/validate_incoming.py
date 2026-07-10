#!/usr/bin/env python3
"""离线甄别 data/incoming/ 社区抓取载荷(CI 与维护者本地共用)。

CI 无法回抓 chess-results(GitHub IP 被封),因此甄别 = 证据一致性复核:

1. manifest 模式、submission-id 格式、sha256 与字节数逐文件比对,不允许有
   manifest 之外的文件混入;
2. 棋手载荷:用仓库自己的解析器(crawl_player_events._extract_rows)重新解析
   随载荷提交的原始 SpielerSuche HTML 快照,与提交的 rows.json 逐行比对——
   伪造解析结果而不同时伪造出可通过同一解析器的 HTML 是非常困难的;
3. 赛事载荷:用 fetch_event_pgn 的切分逻辑对 raw.pgn.gz 重切,与提交的
   split/*.pgn 逐字节比对;split 中的 FIDE ID 必须在注册表中;
4. 中文名过 sanitize_person_name;昵称 1-20 字不含链接;
5. 体积上限:单载荷 25 MB(gzip 后)。

真实性(数据确实来自 chess-results)由维护者在住宅 IP 环境用
``promote_incoming.py --verify`` 抽查回验,与本脚本互补。

Exit 非零 = 拒绝。用法:python3 Scripts/validate_incoming.py [submission-id ...]
"""

from __future__ import annotations

import gzip
import hashlib
import json
import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "Scripts"))

import crawl_player_events as cpe  # noqa: E402
import fetch_event_pgn as fep  # noqa: E402
from sync_static_pgn import count_pgn_games  # noqa: E402

INCOMING = REPO_ROOT / "data" / "incoming"
MAX_PAYLOAD_BYTES = 25 * 1024 * 1024
SUB_ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")
ERRORS: list[str] = []


def err(sub: str, msg: str) -> None:
    ERRORS.append(f"{sub}: {msg}")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compare_rows(submitted: list[dict], reparsed: list[dict], fide_id: str) -> str | None:
    """rows.json must equal a fresh parse of the archived HTML."""
    keep = [r for r in reparsed if not r["fide_id_seen"] or r["fide_id_seen"] == fide_id]
    for r in keep:
        r["fide_id"] = fide_id
    if len(submitted) != len(keep):
        return f"rows.json 有 {len(submitted)} 行,但 HTML 快照重解析得到 {len(keep)} 行"
    for i, (a, b) in enumerate(zip(submitted, keep)):
        for key in ("tnrid", "tournament", "end_date", "rank", "rounds", "participants",
                    "player_name", "club", "federation", "player_snr"):
            if str(a.get(key, "")) != str(b.get(key, "")):
                return f"第 {i + 1} 行字段 {key} 不一致:{a.get(key)!r} != {b.get(key)!r}"
    return None


def validate_payload(sub_dir: pathlib.Path) -> None:
    sub = sub_dir.name
    if not SUB_ID_RE.fullmatch(sub):
        err(sub, "submission-id 格式不合规(YYYYMMDD-HHMMSS-hex6)")
        return
    manifest_path = sub_dir / "manifest.json"
    if not manifest_path.exists():
        err(sub, "缺少 manifest.json")
        return
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        err(sub, f"manifest.json 不是合法 JSON:{exc}")
        return

    nickname = str(manifest.get("contributor", {}).get("nickname") or "")
    if not (1 <= len(nickname) <= 20) or re.search(r"https?://", nickname):
        err(sub, f"贡献者昵称不合规:{nickname!r}")
    github = str(manifest.get("contributor", {}).get("github") or "")
    if github and not re.fullmatch(r"[A-Za-z0-9-]{1,39}", github):
        err(sub, f"GitHub 用户名不合规:{github!r}")

    listed = {f["path"]: f for f in manifest.get("files", [])}
    on_disk = {str(p.relative_to(sub_dir)).replace("\\", "/")
               for p in sub_dir.rglob("*") if p.is_file()} - {"manifest.json"}
    for extra in sorted(on_disk - set(listed)):
        err(sub, f"manifest 之外的文件:{extra}")
    for missing in sorted(set(listed) - on_disk):
        err(sub, f"manifest 声明但缺失:{missing}")

    total = 0
    for rel, meta in sorted(listed.items()):
        path = sub_dir / rel
        if not path.exists():
            continue
        if ".." in pathlib.PurePosixPath(rel).parts:
            err(sub, f"非法路径:{rel}")
            continue
        data = path.read_bytes()
        total += len(data)
        if sha256_bytes(data) != meta.get("sha256"):
            err(sub, f"sha256 不匹配:{rel}")
        if len(data) != meta.get("bytes"):
            err(sub, f"字节数不匹配:{rel}")
    if total > MAX_PAYLOAD_BYTES:
        err(sub, f"载荷过大:{total} 字节(上限 {MAX_PAYLOAD_BYTES})")

    # -- players: reparse archived HTML and diff -----------------------------
    for rows_path in sorted(sub_dir.glob("players/*/rows.json")):
        fide_id = rows_path.parent.name
        if not re.fullmatch(r"\d{4,10}", fide_id):
            err(sub, f"players/{fide_id}: FIDE ID 不合规")
            continue
        try:
            submitted = json.loads(rows_path.read_text(encoding="utf-8"))
        except Exception as exc:
            err(sub, f"players/{fide_id}/rows.json 解析失败:{exc}")
            continue
        html_path = rows_path.parent / "spielersuche.html.gz"
        if not html_path.exists():
            err(sub, f"players/{fide_id}: 缺少 HTML 证据快照")
            continue
        try:
            html_text = gzip.decompress(html_path.read_bytes()).decode("utf-8", "replace")
        except Exception as exc:
            err(sub, f"players/{fide_id}: HTML 快照无法解压:{exc}")
            continue
        reparsed = cpe._extract_rows(html_text, "https://s3.chess-results.com/")
        problem = compare_rows(submitted, reparsed, fide_id)
        if problem:
            err(sub, f"players/{fide_id}: 证据不一致 - {problem}")

    # -- events: re-split raw PGN and diff ----------------------------------
    china_ids = fep.load_china_fide_ids()
    names = fep.load_name_index()
    for raw_path in sorted(sub_dir.glob("events/tnr*/raw.pgn.gz")):
        tnr_dir = raw_path.parent
        tid = tnr_dir.name.removeprefix("tnr")
        if not re.fullmatch(r"\d{3,9}", tid):
            err(sub, f"{tnr_dir.name}: 赛事号不合规")
            continue
        try:
            pgn = gzip.decompress(raw_path.read_bytes()).decode("utf-8", "replace")
        except Exception as exc:
            err(sub, f"{tnr_dir.name}: raw.pgn.gz 无法解压:{exc}")
            continue
        if count_pgn_games(pgn) == 0:
            err(sub, f"{tnr_dir.name}: raw.pgn 中没有可识别的对局")
            continue
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = pathlib.Path(tmp)
            fep.process_event(tid, china_ids, names, tmp_root, overwrite=True,
                              dry_run=False, pgn_text=pgn)
            expected = {p.name: p.read_bytes() for p in sorted((tmp_root / f"tnr{tid}").glob("*.pgn"))} \
                if (tmp_root / f"tnr{tid}").exists() else {}
        got = {p.name: p.read_bytes() for p in sorted((tnr_dir / "split").glob("*.pgn"))}
        if set(expected) != set(got):
            err(sub, f"{tnr_dir.name}: split 文件集合与 raw.pgn 重切结果不一致 "
                     f"(提交 {sorted(got)} vs 重切 {sorted(expected)})")
        else:
            for name, data in expected.items():
                if got[name] != data:
                    err(sub, f"{tnr_dir.name}/split/{name}: 内容与 raw.pgn 重切结果不一致")
        for name in got:
            m = re.fullmatch(r"fide-(\d{4,10})-\d{3,9}\.pgn", name)
            if not m:
                err(sub, f"{tnr_dir.name}/split/{name}: 文件名不合规")
            elif m.group(1) not in china_ids:
                err(sub, f"{tnr_dir.name}/split/{name}: FIDE {m.group(1)} 不在注册表中")


def main() -> int:
    if not INCOMING.exists():
        print(json.dumps({"payloads": 0, "errors": 0}))
        return 0
    wanted = set(sys.argv[1:])
    dirs = [d for d in sorted(INCOMING.iterdir())
            if d.is_dir() and (not wanted or d.name in wanted)]
    for sub_dir in dirs:
        validate_payload(sub_dir)
    for line in ERRORS:
        print(f"ERROR {line}", file=sys.stderr)
    print(json.dumps({"payloads": len(dirs), "errors": len(ERRORS)}))
    return 1 if ERRORS else 0


if __name__ == "__main__":
    raise SystemExit(main())
