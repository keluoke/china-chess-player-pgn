#!/usr/bin/env python3
"""Fetch one or more Chess-Results events by tnr ID.

This is the user-facing entry point for pasted tnr links. It stores final
standings and every round's pairings, then optionally chains into the existing
player-name and tournament-PGN importers.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import html
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

from apply_aliases_to_registry import sanitize_person_name
from source_http import SourceHTTPError, fetch_bytes
from source_policy import (
    chess_results_release_policy,
    local_state_root,
    require_chess_results_publication,
)


ROOT = pathlib.Path(__file__).resolve().parents[1]
PUBLIC_OUTPUT = ROOT / "data" / "generated" / "chess-results-event-details"
EVENT_QUEUE = ROOT / "docs" / "data" / "audit" / "domestic-event-queue.json"
CAPTURE_STATE = local_state_root() / "chess-results" / "capture-state.json"
USER_AGENT = "ChinaChessPlayerPGN/EventDetailSync"


def clean(value: Any) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split()).strip()


def normalized_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", clean(value).casefold())


@dataclass
class Cell:
    text: str
    links: list[str] = field(default_factory=list)


class TableParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.tables: list[list[list[Cell]]] = []
        self.h2s: list[str] = []
        self._table: list[list[Cell]] | None = None
        self._row: list[Cell] | None = None
        self._cell_text: list[str] | None = None
        self._cell_links: list[str] = []
        self._h2: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell_text = []
            self._cell_links = []
        elif tag == "a" and self._cell_text is not None and values.get("href"):
            self._cell_links.append(urllib.parse.urljoin(self.base_url, html.unescape(values["href"])))
        elif tag == "h2":
            self._h2 = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell_text is not None and self._row is not None:
            self._row.append(Cell(clean(" ".join(self._cell_text)), list(self._cell_links)))
            self._cell_text = None
            self._cell_links = []
        elif tag == "tr" and self._row is not None and self._table is not None:
            if any(cell.text for cell in self._row):
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table)
            self._table = None
        elif tag == "h2" and self._h2 is not None:
            value = clean(" ".join(self._h2))
            if value:
                self.h2s.append(value)
            self._h2 = None

    def handle_data(self, data: str) -> None:
        if self._cell_text is not None:
            self._cell_text.append(data)
        if self._h2 is not None:
            self._h2.append(data)


def fetch_page(tournament_id: str, art: int, round_no: int | None, timeout: float, retries: int) -> tuple[str, TableParser, str]:
    params = {"lan": "1", "art": str(art)}
    if round_no is not None:
        params["rd"] = str(round_no)
    url = f"https://chess-results.com/tnr{tournament_id}.aspx?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml", "Accept-Encoding": "identity"},
    )

    def validate(body: bytes, _headers: Any) -> None:
        sample = body[:20000].lower()
        if b"<html" not in sample and b"<table" not in sample:
            raise SourceHTTPError("PARSER_LAYOUT_CHANGED", f"tnr{tournament_id} 返回内容不像 HTML 表格。")

    raw, final_url, headers = fetch_bytes(
        request,
        timeout=timeout,
        retries=retries,
        expected_types=("text/html", "application/xhtml+xml"),
        validator=validate,
    )
    body = raw.decode(headers.get_content_charset() or "utf-8", errors="replace")
    parser = TableParser(final_url)
    parser.feed(body)
    if not parser.tables:
        raise SourceHTTPError("PARSER_LAYOUT_CHANGED", f"tnr{tournament_id} 未解析到任何表格。")
    return body, parser, final_url


def find_table(parser: TableParser, required: set[str]) -> list[list[Cell]]:
    for table in parser.tables:
        if not table:
            continue
        headers = {normalized_header(cell.text) for cell in table[0]}
        if required.issubset(headers):
            return table
    return []


def cell_map(table: list[list[Cell]], row: list[Cell]) -> dict[str, Cell]:
    headers = [normalized_header(cell.text) for cell in table[0]]
    result: dict[str, Cell] = {}
    for index, header in enumerate(headers):
        if header and index < len(row):
            result.setdefault(header, row[index])
    return result


def player_number(cell: Cell | None) -> str:
    if cell is None:
        return ""
    for link in cell.links:
        values = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)
        if values.get("snr"):
            return values["snr"][0]
    return ""


def parse_players(parser: TableParser) -> dict[str, dict[str, str]]:
    table = find_table(parser, {"name", "fideid"})
    result: dict[str, dict[str, str]] = {}
    for row in table[1:]:
        values = cell_map(table, row)
        name_cell = values.get("name")
        number = clean((values.get("no") or values.get("sno") or Cell("")).text) or player_number(name_cell)
        if not number or name_cell is None:
            continue
        result[number] = {
            "playerNo": number,
            "name": name_cell.text,
            "chineseName": sanitize_person_name((values.get("typ") or values.get("gr") or Cell("")).text),
            "fideID": re.sub(r"\D", "", clean((values.get("fideid") or Cell("")).text)),
            "federation": clean((values.get("fed") or Cell("")).text),
            "rating": clean((values.get("rtg") or Cell("")).text),
            "club": clean((values.get("clubcity") or values.get("club") or Cell("")).text),
        }
    return result


def parse_standings(parser: TableParser, players: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    table = find_table(parser, {"rk", "name", "pts"})
    result: list[dict[str, Any]] = []
    for row in table[1:]:
        values = cell_map(table, row)
        number = clean((values.get("sno") or values.get("no") or Cell("")).text)
        name = clean((values.get("name") or Cell("")).text)
        if not number or not name:
            continue
        known = players.get(number, {})
        tie_breaks = [cell.text for key, cell in values.items() if key.startswith("tb") and cell.text]
        result.append({
            "rank": clean((values.get("rk") or Cell("")).text),
            "playerNo": number,
            "name": name,
            "chineseName": sanitize_person_name((values.get("gr") or Cell("")).text) or known.get("chineseName", ""),
            "fideID": known.get("fideID", ""),
            "federation": clean((values.get("fed") or Cell("")).text) or known.get("federation", ""),
            "rating": clean((values.get("rtg") or Cell("")).text) or known.get("rating", ""),
            "club": clean((values.get("clubcity") or Cell("")).text) or known.get("club", ""),
            "score": clean((values.get("pts") or Cell("")).text),
            "tieBreaks": tie_breaks,
        })
    return result


def pairing_side(number: str, name: str, chinese_name: str, players: dict[str, dict[str, str]]) -> dict[str, str]:
    known = players.get(number, {})
    return {
        key: value for key, value in {
            "playerNo": number,
            "name": name,
            "chineseName": sanitize_person_name(chinese_name) or known.get("chineseName", ""),
            "fideID": known.get("fideID", ""),
        }.items() if value
    }


def parse_pairings(parser: TableParser, players: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    table = find_table(parser, {"bo", "white", "black", "result"})
    result: list[dict[str, Any]] = []
    for row in table[1:]:
        values = cell_map(table, row)
        white_no = clean(row[1].text)
        # Duplicate No./Pts./Gr headers are lost by a dict. Use the stable
        # Chess-Results pairing layout for the second side.
        if len(row) < 12:
            continue
        black_no = clean(row[11].text)
        pgn_cell = row[12] if len(row) > 12 else Cell("")
        pgn_url = pgn_cell.links[0] if pgn_cell.links else ""
        result.append({
            "board": clean(row[0].text),
            "white": pairing_side(white_no, clean(row[3].text), clean(row[4].text), players),
            "black": pairing_side(black_no, clean(row[9].text), clean(row[10].text), players),
            "result": clean(row[6].text),
            "hasPGN": bool(pgn_cell.text or pgn_url),
            "pgnURL": pgn_url,
        })
    return result


def rounds_from(parser: TableParser, standings: list[dict[str, Any]]) -> int:
    for heading in parser.h2s:
        match = re.search(r"(?:after|nach)\s+(\d+)\s+round", heading, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return max(0, len(standings[0].get("opponents", [])) if standings else 0)


def scrape_event(tournament_id: str, timeout: float, retries: int, delay: float, max_rounds: int) -> dict[str, Any]:
    fetched_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    source_bodies: dict[str, tuple[str, str]] = {}
    players_body, players_page, players_url = fetch_page(tournament_id, 0, None, timeout, retries)
    source_bodies["starting-rank"] = (players_url, players_body)
    players = parse_players(players_page)
    if not (2 <= len(players) <= 5000):
        raise SourceHTTPError("PARSER_LAYOUT_CHANGED", f"tnr{tournament_id} 起始名单人数异常：{len(players)}")
    time.sleep(delay)
    standings_body, standings_page, standings_url = fetch_page(tournament_id, 1, None, timeout, retries)
    source_bodies["standings"] = (standings_url, standings_body)
    standings = parse_standings(standings_page, players)
    if not standings or len(standings) < max(1, int(len(players) * 0.5)):
        raise SourceHTTPError(
            "VALIDATION_REGRESSION",
            f"tnr{tournament_id} 排名行异常：players={len(players)} standings={len(standings)}",
        )
    rounds = rounds_from(standings_page, standings) or max_rounds
    if max_rounds:
        rounds = min(rounds, max_rounds)
    if not (1 <= rounds <= 30):
        raise SourceHTTPError("PARSER_LAYOUT_CHANGED", f"tnr{tournament_id} 轮数异常：{rounds}")
    title = standings_page.h2s[0] if standings_page.h2s else players_page.h2s[0] if players_page.h2s else f"tnr{tournament_id}"
    round_rows: list[dict[str, Any]] = []
    for round_no in range(1, rounds + 1):
        time.sleep(delay)
        round_body, round_page, round_url = fetch_page(tournament_id, 2, round_no, timeout, retries)
        source_bodies[f"round-{round_no}"] = (round_url, round_body)
        pairings = parse_pairings(round_page, players)
        if not pairings:
            raise SourceHTTPError("PARSER_LAYOUT_CHANGED", f"tnr{tournament_id} 第 {round_no} 轮未解析到对阵。")
        round_rows.append({"round": round_no, "sourceURL": round_url, "pairings": pairings})
    snapshots = [snapshot_entry(tournament_id, kind, url, body) for kind, (url, body) in source_bodies.items()]
    return {
        "schemaVersion": 1,
        "fetchedAt": fetched_at,
        "source": "Chess-Results",
        "tournamentID": tournament_id,
        "sourceName": title,
        "sourceRefs": [{"source": "Chess-Results", "tournamentID": tournament_id, "url": standings_url}],
        "coverageScope": "domestic-full",
        "roundCount": rounds,
        "players": list(players.values()),
        "standings": standings,
        "rounds": round_rows,
        "evidence": {"startingRankURL": players_url, "standingsURL": standings_url},
        "sourceSnapshots": snapshots,
        "_sourceBodies": {kind: body for kind, (_url, body) in source_bodies.items()},
    }


def snapshot_entry(tournament_id: str, kind: str, url: str, body: str) -> dict[str, Any]:
    content = body.encode("utf-8")
    return {
        "kind": kind,
        "url": url,
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
        "localPrivatePath": f"raw/chess-results/tnr{tournament_id}/{kind}.html.gz",
    }


def write_snapshot_bundle(snapshot_output: pathlib.Path, tournament_id: str, payload: dict[str, Any]) -> None:
    bodies = payload.pop("_sourceBodies", {})
    if not bodies:
        return
    root = snapshot_output / f"tnr{tournament_id}"
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    for kind, body in bodies.items():
        target = root / f"{kind}.html.gz"
        tmp = root / f".{kind}.{os.getpid()}.tmp"
        with tmp.open("wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=9, mtime=0) as handle:
                handle.write(str(body).encode("utf-8"))
        os.replace(tmp, target)
        target.chmod(0o600)


def write_private_json(path: pathlib.Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    path.chmod(0o600)


def load_capture_state() -> dict[str, Any]:
    try:
        payload = json.loads(CAPTURE_STATE.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {"schemaVersion": 1, "events": {}}
    except (OSError, json.JSONDecodeError):
        return {"schemaVersion": 1, "events": {}}


def write_capture_state(payload: dict[str, Any]) -> None:
    CAPTURE_STATE.parent.mkdir(parents=True, exist_ok=True)
    CAPTURE_STATE.parent.chmod(0o700)
    write_private_json(CAPTURE_STATE, payload)


def queue_targets(limit: int, refresh_days: int) -> list[str]:
    if not EVENT_QUEUE.exists():
        raise SystemExit("赛事线索队列不存在；请先在社区/人工数据变更后离线重建队列。")
    payload = json.loads(EVENT_QUEUE.read_text(encoding="utf-8"))
    capture_state = load_capture_state().get("events") or {}
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=max(0, refresh_days))
    targets = []
    for item in payload.get("targets", []):
        if item.get("nextAction") not in {"capture-event", "refresh-snapshot"}:
            continue
        tournament = clean(item.get("tournamentID"))
        seen = (capture_state.get(tournament) or {}).get("capturedAt")
        if seen and refresh_days > 0:
            try:
                stamp = dt.datetime.fromisoformat(str(seen).replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=dt.timezone.utc)
                if stamp.astimezone(dt.timezone.utc) >= cutoff:
                    continue
            except ValueError:
                pass
        targets.append(tournament)
    return [value for value in targets if value][:limit]


def tournament_id(value: str) -> str:
    match = re.search(r"(?:tnr)?(\d{5,9})", value, flags=re.IGNORECASE)
    return match.group(1) if match else ""


def run_command(args: list[str]) -> None:
    subprocess.run(args, cwd=ROOT, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("targets", nargs="*", help="tnr ID or Chess-Results URL")
    parser.add_argument("--tournament-id", action="append", default=[])
    parser.add_argument("--from-queue", type=int, default=0, metavar="N", help="ingest the top N registered/demand-ranked event targets")
    parser.add_argument("--refresh-days", type=int, default=30, help="skip queue targets privately captured within N days")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--max-rounds", type=int, default=0)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--no-players", action="store_true")
    parser.add_argument("--no-pgn", action="store_true")
    parser.add_argument("--no-rebuild", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--private-root",
        type=pathlib.Path,
        help="repo 外的私有运行目录；默认写入本机应用数据目录",
    )
    parser.add_argument(
        "--authorized-publication",
        action="store_true",
        help="仅在取得书面授权并设置环境确认后，将结构化赛事数据写入公开生成层",
    )
    args = parser.parse_args()
    if args.from_queue > 10:
        raise SystemExit("单次赛事队列最多 10 个目标；请拆分运行以保护访问预算。")
    if args.authorized_publication:
        require_chess_results_publication()
    private_root = (
        args.private_root
        or (local_state_root() / "standalone" / f"{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}")
    ).resolve()
    try:
        private_root.relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise SystemExit("COMPLIANCE_POLICY_BLOCKED: Chess-Results 私有运行目录必须位于 Git 仓库之外")
    if not args.dry_run:
        private_root.mkdir(parents=True, exist_ok=True)
        private_root.chmod(0o700)
    snapshot_output = private_root / "raw" / "chess-results"
    output_root = PUBLIC_OUTPUT if args.authorized_publication else private_root / "extracted" / "chess-results-event-details"
    queued_ids = queue_targets(args.from_queue, args.refresh_days) if args.from_queue > 0 else []
    ids = [tournament_id(value) for value in [*args.targets, *args.tournament_id, *queued_ids]]
    ids = list(dict.fromkeys(value for value in ids if value))
    if not ids:
        if args.from_queue > 0:
            print(json.dumps({
                "events": [],
                "skippedFresh": True,
                "refreshDays": args.refresh_days,
                "releasePolicy": "authorized" if args.authorized_publication else chess_results_release_policy(),
                "publicMutation": False,
            }, ensure_ascii=False, indent=2))
            return 0
        raise SystemExit("请提供至少一个 tnr ID 或 Chess-Results URL")

    stats = []
    capture_state = load_capture_state()
    captured_events = capture_state.setdefault("events", {})
    for tid in ids:
        output = output_root / f"tnr{tid}.json"
        if output.exists() and not args.overwrite and tid not in queued_ids:
            payload = json.loads(output.read_text(encoding="utf-8"))
        else:
            payload = scrape_event(tid, args.timeout, args.retries, args.delay, args.max_rounds)
            if not args.dry_run:
                output.parent.mkdir(parents=True, exist_ok=True)
                write_snapshot_bundle(snapshot_output, tid, payload)
                payload["releasePolicy"] = "authorized" if args.authorized_publication else chess_results_release_policy()
                if args.authorized_publication:
                    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                else:
                    write_private_json(output, payload)
        stats.append({"tournamentID": tid, "players": len(payload.get("players", [])), "rounds": len(payload.get("rounds", [])), "standings": len(payload.get("standings", []))})
        if not args.dry_run:
            captured_events[tid] = {
                "capturedAt": payload.get("fetchedAt") or dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
                "players": len(payload.get("players", [])),
                "rounds": len(payload.get("rounds", [])),
                "standings": len(payload.get("standings", [])),
                "releasePolicy": "authorized" if args.authorized_publication else chess_results_release_policy(),
                "runPrivateRoot": str(private_root),
            }
            # Checkpoint each successful event.  If a later target fails, the
            # completed private capture is still remembered and will not be
            # re-requested on the next queue run.
            capture_state["updatedAt"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
            write_capture_state(capture_state)

    # Link-only collection never mutates manual/community/public player data or
    # fetches public PGN.  Downstream publication is reachable only through the
    # explicit, environment-confirmed authorized mode.
    if args.authorized_publication and not args.dry_run and not args.no_players:
        command = [
            sys.executable,
            "Scripts/sync_chess_results_starting_rank_aliases.py",
            "--only-explicit",
        ]
        for tid in ids:
            command.extend(["--tournament-id", tid])
        run_command(command)
        run_command([sys.executable, "Scripts/sync_domestic_players.py"])
    if args.authorized_publication and not args.dry_run and not args.no_pgn:
        command = [sys.executable, "Scripts/fetch_event_pgn.py", "--workers", "1"]
        if args.overwrite:
            command.append("--overwrite")
        for tid in ids:
            command.extend(["--tournament-id", tid])
        run_command(command)
    if args.authorized_publication and not args.dry_run and not args.no_rebuild:
        run_command([sys.executable, "Scripts/build_static_player_pgn.py"])
        run_command([sys.executable, "Scripts/build_event_details.py"])
        run_command([sys.executable, "Scripts/build_event_catalog.py"])
        run_command([sys.executable, "Scripts/build_dashboard.py"])

    print(json.dumps({
        "events": stats,
        "dryRun": args.dry_run,
        "releasePolicy": "authorized" if args.authorized_publication else chess_results_release_policy(),
        "privateRoot": str(private_root),
        "publicMutation": bool(args.authorized_publication and not args.dry_run),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
