#!/usr/bin/env python3
"""Fetch one or more Chess-Results events by tnr ID.

This is the user-facing entry point for pasted tnr links. It stores final
standings and every round's pairings, then optionally chains into the existing
player-name and tournament-PGN importers.
"""

from __future__ import annotations

import argparse
import certifi
import html
import json
import pathlib
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "generated" / "chess-results-event-details"
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
    context = ssl.create_default_context(cafile=certifi.where())
    for attempt in range(retries + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                body = response.read().decode(response.headers.get_content_charset() or "utf-8", errors="replace")
            parser = TableParser(url)
            parser.feed(body)
            return body, parser, url
        except (urllib.error.URLError, TimeoutError) as error:
            if attempt >= retries:
                raise RuntimeError(f"tnr{tournament_id} art={art} rd={round_no}: {error}") from error
            time.sleep(attempt + 1)
    raise AssertionError("unreachable")


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
            "chineseName": clean((values.get("typ") or values.get("gr") or Cell("")).text),
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
            "chineseName": clean((values.get("gr") or Cell("")).text) or known.get("chineseName", ""),
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
            "chineseName": chinese_name or known.get("chineseName", ""),
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
    _body, players_page, players_url = fetch_page(tournament_id, 0, None, timeout, retries)
    players = parse_players(players_page)
    time.sleep(delay)
    _body, standings_page, standings_url = fetch_page(tournament_id, 1, None, timeout, retries)
    standings = parse_standings(standings_page, players)
    rounds = rounds_from(standings_page, standings) or max_rounds
    if max_rounds:
        rounds = min(rounds, max_rounds)
    title = standings_page.h2s[0] if standings_page.h2s else players_page.h2s[0] if players_page.h2s else f"tnr{tournament_id}"
    round_rows: list[dict[str, Any]] = []
    for round_no in range(1, rounds + 1):
        time.sleep(delay)
        _body, round_page, round_url = fetch_page(tournament_id, 2, round_no, timeout, retries)
        round_rows.append({"round": round_no, "sourceURL": round_url, "pairings": parse_pairings(round_page, players)})
    return {
        "schemaVersion": 1,
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
    }


def tournament_id(value: str) -> str:
    match = re.search(r"(?:tnr)?(\d{5,9})", value, flags=re.IGNORECASE)
    return match.group(1) if match else ""


def run_command(args: list[str]) -> None:
    subprocess.run(args, cwd=ROOT, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("targets", nargs="*", help="tnr ID or Chess-Results URL")
    parser.add_argument("--tournament-id", action="append", default=[])
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--max-rounds", type=int, default=0)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--no-players", action="store_true")
    parser.add_argument("--no-pgn", action="store_true")
    parser.add_argument("--no-rebuild", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    ids = [tournament_id(value) for value in [*args.targets, *args.tournament_id]]
    ids = list(dict.fromkeys(value for value in ids if value))
    if not ids:
        raise SystemExit("请提供至少一个 tnr ID 或 Chess-Results URL")

    stats = []
    for tid in ids:
        output = OUTPUT / f"tnr{tid}.json"
        if output.exists() and not args.overwrite:
            payload = json.loads(output.read_text(encoding="utf-8"))
        else:
            payload = scrape_event(tid, args.timeout, args.retries, args.delay, args.max_rounds)
            if not args.dry_run:
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        stats.append({"tournamentID": tid, "players": len(payload.get("players", [])), "rounds": len(payload.get("rounds", [])), "standings": len(payload.get("standings", []))})

    if not args.dry_run and not args.no_players:
        command = [sys.executable, "Scripts/sync_chess_results_starting_rank_aliases.py"]
        for tid in ids:
            command.extend(["--tournament-id", tid])
        run_command(command)
        run_command([sys.executable, "Scripts/sync_domestic_players.py"])
    if not args.dry_run and not args.no_pgn:
        command = [sys.executable, "Scripts/fetch_event_pgn.py", "--workers", "1"]
        if args.overwrite:
            command.append("--overwrite")
        for tid in ids:
            command.extend(["--tournament-id", tid])
        run_command(command)
    if not args.dry_run and not args.no_rebuild:
        run_command([sys.executable, "Scripts/build_static_player_pgn.py"])
        run_command([sys.executable, "Scripts/build_event_details.py"])
        run_command([sys.executable, "Scripts/build_event_catalog.py"])
        run_command([sys.executable, "Scripts/build_dashboard.py"])

    print(json.dumps({"events": stats, "dryRun": args.dry_run}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
