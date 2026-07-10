#!/usr/bin/env python3
"""把已合并进 main 的 data/incoming/ 社区载荷核验后并入正式数据(维护者本地)。

本机永不 pull,因此载荷通过 GitHub raw HTTP 拉取(不动本地 git 历史):

1. 列出 main 上 data/incoming/ 的载荷(GitHub contents API,公开仓库免认证);
2. 跳过 data/generated/contrib-processed.json 已记录的;
3. 下载载荷到临时目录,先跑 validate_incoming 同款离线甄别;
4. ``--verify`` 时再抽查回抓 chess-results 比对(住宅 IP 环境);
5. 入库:
   - 棋手 rows.json → 走 crawl_player_events 相同的 absorb/write_outputs 逻辑,
     并入 player-events.csv、名字证据与赛事目录;
   - 赛事 split/*.pgn → docs/data/pgn/chess-results/tnr<id>/;
   - 贡献者 → data/community/contributors.csv(鸣谢名录,昵称+可选 GitHub 名);
   - submission-id → data/generated/contrib-processed.json;
6. 之后正常走 refresh.sh reindex(重建派生索引)与 local-data 推送。

用法:
    python3 Scripts/promote_incoming.py            # 拉取并入库全部未处理载荷
    python3 Scripts/promote_incoming.py --verify   # 入库前抽查回抓比对
    python3 Scripts/promote_incoming.py --dry-run
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import json
import pathlib
import sys
import tempfile
import urllib.parse
import urllib.request

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "Scripts"))

import crawl_player_events as cpe  # noqa: E402
import validate_incoming as vi  # noqa: E402

UPSTREAM = "keluoke/china-chess-player-pgn"
RAW = f"https://raw.githubusercontent.com/{UPSTREAM}/main"
API = f"https://api.github.com/repos/{UPSTREAM}/contents"
PROCESSED_JSON = REPO_ROOT / "data" / "generated" / "contrib-processed.json"
CONTRIBUTORS_CSV = REPO_ROOT / "data" / "community" / "contributors.csv"
PGN_ROOT = REPO_ROOT / "docs" / "data" / "pgn" / "chess-results"

CONTRIB_COLUMNS = ["nickname", "github", "first_contribution", "last_contribution",
                   "submissions", "players", "events", "games", "notes"]


def http_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "promote-incoming/1.0",
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "promote-incoming/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def list_remote_dir(path: str) -> list[dict]:
    try:
        return http_json(f"{API}/{urllib.parse.quote(path, safe='/')}?ref=main")
    except Exception:
        return []


def download_payload(sub: str, dest: pathlib.Path) -> None:
    stack = [f"data/incoming/{sub}"]
    while stack:
        current = stack.pop()
        for entry in list_remote_dir(current):
            if entry["type"] == "dir":
                stack.append(entry["path"])
            elif entry["type"] == "file":
                rel = pathlib.PurePosixPath(entry["path"]).relative_to(f"data/incoming/{sub}")
                target = dest / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(http_bytes(entry["download_url"]))


def verify_against_source(payload: pathlib.Path) -> list[str]:
    """住宅 IP 抽查:每个载荷回抓 1 名棋手,与提交行数/首行比对。"""
    problems: list[str] = []
    for rows_path in sorted(payload.glob("players/*/rows.json"))[:1]:
        fide_id = rows_path.parent.name
        submitted = json.loads(rows_path.read_text(encoding="utf-8"))
        try:
            live = cpe.search_player(fide_id)
        except Exception as exc:
            problems.append(f"回抓 FIDE {fide_id} 失败:{exc}")
            continue
        if len(live) < len(submitted):
            problems.append(f"FIDE {fide_id}: 线上 {len(live)} 行 < 提交 {len(submitted)} 行,存疑")
        sub_ids = {r["tnrid"] for r in submitted}
        live_ids = {r["tnrid"] for r in live}
        missing = sub_ids - live_ids
        if missing:
            problems.append(f"FIDE {fide_id}: 提交包含线上不存在的赛事 {sorted(missing)[:5]}")
    return problems


def absorb_player_rows(payload: pathlib.Path, events: dict, name_map: dict,
                       known_cn: dict, crawled_at: str, stats: dict) -> None:
    for rows_path in sorted(payload.glob("players/*/rows.json")):
        fide_id = rows_path.parent.name
        rows = json.loads(rows_path.read_text(encoding="utf-8"))
        stats["players"] += 1
        for r in rows:
            key = (fide_id, r["tnrid"])
            events[key] = {
                "fide_id": fide_id,
                "tnrid": r["tnrid"],
                "tournament_name": r["tournament"],
                "end_date": r["end_date"],
                "rank": r["rank"],
                "rounds": r["rounds"],
                "participants": r["participants"],
                "player_name": r["player_name"],
                "club": r["club"],
                "federation": r["federation"],
                "player_snr": r["player_snr"],
                "source_url": r["tournament_url"],
                "crawled_at": crawled_at,
            }
            stats["participations"] += 1
            club_clean = cpe.sanitize_person_name(r.get("club", ""))
            cjk = [v for v in (cpe.sanitize_person_name(n) for n in r.get("cjk_names", []) or [])
                   if v and v != club_clean]
            if cjk and fide_id not in name_map:
                name_map[fide_id] = {
                    "fide_id": fide_id,
                    "chinese_name": known_cn.get(fide_id) or cjk[0],
                    "pinyin_name": "",
                    "latin_name": r["player_name"] if not cpe.CJK_RE.search(r["player_name"]) else "",
                    "name_variants": "|".join(dict.fromkeys(cjk)),
                    "evidence_tnrid": r["tnrid"],
                    "source": "community-contribution",
                    "source_url": r.get("player_url") or r.get("tournament_url") or "",
                    "notes": f"community payload, SpielerSuche FIDE {fide_id} tnr{r['tnrid']}",
                }


def absorb_event_pgn(payload: pathlib.Path, dry_run: bool, stats: dict) -> None:
    for split_dir in sorted(payload.glob("events/tnr*/split")):
        tid = split_dir.parent.name.removeprefix("tnr")
        out_dir = PGN_ROOT / f"tnr{tid}"
        stats["events"] += 1
        for p in sorted(split_dir.glob("*.pgn")):
            stats["games"] += p.read_text(encoding="utf-8", errors="replace").count("[Event ")
            if not dry_run:
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / p.name).write_bytes(p.read_bytes())
        raw = split_dir.parent / "raw.pgn.gz"
        if raw.exists() and not dry_run:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "games.pgn").write_bytes(gzip.decompress(raw.read_bytes()))


def credit_contributor(manifest: dict, stats: dict, dry_run: bool) -> None:
    nickname = manifest.get("contributor", {}).get("nickname") or "匿名棋友"
    github = manifest.get("contributor", {}).get("github") or ""
    today = dt.date.today().isoformat()
    rows: dict[str, dict] = {}
    if CONTRIBUTORS_CSV.exists():
        with CONTRIBUTORS_CSV.open("r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                if row.get("nickname"):
                    rows[row["nickname"]] = row
    rec = rows.get(nickname) or {
        "nickname": nickname, "github": github, "first_contribution": today,
        "last_contribution": today, "submissions": "0", "players": "0",
        "events": "0", "games": "0", "notes": "",
    }
    rec["github"] = rec.get("github") or github
    rec["last_contribution"] = today
    rec["submissions"] = str(int(rec.get("submissions") or 0) + 1)
    rec["players"] = str(int(rec.get("players") or 0) + stats["players"])
    rec["events"] = str(int(rec.get("events") or 0) + stats["events"])
    rec["games"] = str(int(rec.get("games") or 0) + stats["games"])
    rows[nickname] = rec
    if dry_run:
        return
    CONTRIBUTORS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with CONTRIBUTORS_CSV.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CONTRIB_COLUMNS)
        writer.writeheader()
        for rec in sorted(rows.values(), key=lambda r: r["first_contribution"]):
            writer.writerow({k: rec.get(k, "") for k in CONTRIB_COLUMNS})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="入库前回抓 chess-results 抽查")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--submission", action="append", default=[], help="只处理指定 submission-id")
    args = parser.parse_args()

    processed = set(json.loads(PROCESSED_JSON.read_text(encoding="utf-8"))
                    if PROCESSED_JSON.exists() else [])
    remote = [e["name"] for e in list_remote_dir("data/incoming") if e["type"] == "dir"]
    todo = [s for s in remote if s not in processed and (not args.submission or s in args.submission)]
    if not todo:
        print(json.dumps({"promoted": 0, "message": "no pending payloads"}, ensure_ascii=False))
        return 0

    events = cpe._read_events_csv()
    name_map = cpe._read_name_map()
    known_cn = cpe.registry_chinese_names()
    crawled_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    promoted = []

    for sub in todo:
        print(f"== {sub} ==", file=sys.stderr)
        with tempfile.TemporaryDirectory() as tmp:
            payload = pathlib.Path(tmp) / sub
            payload.mkdir()
            download_payload(sub, payload)
            vi.ERRORS.clear()
            vi.validate_payload(payload)
            if vi.ERRORS:
                for line in vi.ERRORS:
                    print(f"REJECT {line}", file=sys.stderr)
                continue
            if args.verify:
                problems = verify_against_source(payload)
                if problems:
                    for line in problems:
                        print(f"SUSPECT {sub}: {line}", file=sys.stderr)
                    continue
            manifest = json.loads((payload / "manifest.json").read_text(encoding="utf-8"))
            stats = {"players": 0, "participations": 0, "events": 0, "games": 0}
            absorb_player_rows(payload, events, name_map, known_cn, crawled_at, stats)
            absorb_event_pgn(payload, args.dry_run, stats)
            credit_contributor(manifest, stats, args.dry_run)
            promoted.append({"id": sub, **stats})

    if promoted and not args.dry_run:
        cpe.write_outputs(events, name_map, dry_run=False)
        processed.update(p["id"] for p in promoted)
        PROCESSED_JSON.parent.mkdir(parents=True, exist_ok=True)
        PROCESSED_JSON.write_text(json.dumps(sorted(processed), ensure_ascii=False, indent=2) + "\n",
                                  encoding="utf-8")
    print(json.dumps({"promoted": len(promoted), "details": promoted}, ensure_ascii=False, indent=2))
    if promoted and not args.dry_run:
        print("下一步:bash Scripts/local/refresh.sh reindex(重建派生索引并推送)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
