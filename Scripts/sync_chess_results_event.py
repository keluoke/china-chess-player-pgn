#!/usr/bin/env python3
"""Fetch one or more Chess-Results events by tnr ID.

This is the user-facing entry point for pasted tnr links. Collection is a
persistent page-task pipeline: every successfully fetched page is written to
the private raw store *before* parsing, every parsed page is checkpointed,
and a failing target is isolated and recorded instead of aborting the batch.
Deterministic structure errors never trigger network retries; a parser update
replays the private raw pages offline (``--replay``) with zero source access.
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
from typing import Any, Callable

from apply_aliases_to_registry import sanitize_person_name
from fetch_event_pgn import EVENT_PGN_ARCHIVE, source_explicitly_omits_pgn
from event_targeting import DISCOVERY_POOL
from source_http import SourceHTTPError, fetch_bytes
from source_policy import (
    chess_results_release_policy,
    local_state_root,
    require_chess_results_publication,
)


ROOT = pathlib.Path(__file__).resolve().parents[1]
PUBLIC_OUTPUT = ROOT / "data" / "generated" / "chess-results-event-details"
EVENT_QUEUE = ROOT / "data" / "generated" / "audit" / "domestic-event-queue.json"
CAPTURE_STATE = local_state_root() / "chess-results" / "capture-state.json"
USER_AGENT = "ChinaChessPlayerPGN/EventDetailSync"

# v7: every event captures the independent ``turdet=YES`` tournament-details
# page from the canonical host before deciding whether a FIDE Event ID exists.
# Normal starting/standings pages can vary by Chess-Results serving node and
# are therefore never used as the Event ID authority.
# Bumping the version releases affected targets for one evidence-backed retry;
# cached starting-rank pages are replayed locally before any missing page is
# requested.
PARSER_VERSION = "chess-results-v7"
TOURNAMENT_DETAILS_ART = -1
QUARANTINE_DAYS = 7
STRUCTURE_QUARANTINE_THRESHOLD = 2

# Deterministic structure error codes: retrying the same page over the network
# is pointless; they need a parser update (offline replay) or human review.
STRUCTURAL_ERROR_CODES = {
    "EVENT_EMPTY",
    "PAIRINGS_NOT_PUBLISHED",
    "TEAM_FORMAT_UNSUPPORTED",
    "EVENT_FORMAT_UNSUPPORTED",
    "PARSER_LAYOUT_CHANGED",
    "ROUND_COUNT_UNKNOWN",
    "VALIDATION_REGRESSION",
    "PAIRING_REFS_MISSING",
    "PAIRING_REFS_OUTSIDE_ROSTER",
}


class EventCaptureError(RuntimeError):
    """A structured per-target failure that never aborts the whole batch."""

    def __init__(self, code: str, message: str, *, failed_page: str | None = None, structural: bool = True):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.failed_page = failed_page
        self.structural = structural


def clean(value: Any) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split()).strip()


def normalized_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", clean(value).casefold())


def normalized_pairing_name(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", clean(value).casefold())


NON_PLAYER_PAIRING_NAMES = {"bye", "notpaired"}


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
        self.links: list[str] = []
        self.text_parts: list[str] = []
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
        elif tag == "a" and values.get("href"):
            absolute = urllib.parse.urljoin(self.base_url, html.unescape(values["href"]))
            self.links.append(absolute)
            if self._cell_text is not None:
                self._cell_links.append(absolute)
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
        if clean(data):
            self.text_parts.append(data)
        if self._cell_text is not None:
            self._cell_text.append(data)
        if self._h2 is not None:
            self._h2.append(data)


def parse_html(body: str, base_url: str) -> TableParser:
    parser = TableParser(base_url)
    parser.feed(body)
    return parser


FIDE_EVENT_URL_RE = re.compile(
    r"^https://ratings\.fide\.com/tournament_information\.phtml\?event=(\d+)$",
    re.IGNORECASE,
)


def extract_fide_event_id(*parsers: TableParser) -> str:
    """Return the one unambiguous FIDE event ID linked by Chess-Results.

    The ID is a cross-source join key, not permission to assume that a PGN
    exists. Multiple conflicting IDs fail closed and leave the supplement
    disabled for that event.
    """
    event_ids = {
        match.group(1)
        for parser in parsers
        for link in parser.links
        if (match := FIDE_EVENT_URL_RE.fullmatch(link))
    }
    return next(iter(event_ids)) if len(event_ids) == 1 else ""


DATE_TOKEN = r"(?:20\d{2}[./-]\d{1,2}[./-]\d{1,2}|\d{1,2}[./-]\d{1,2}[./-]20\d{2})"


def normalize_event_date(value: str) -> str:
    numbers = [int(part) for part in re.split(r"[./-]", clean(value))]
    if len(numbers) != 3:
        return ""
    if numbers[0] >= 1900:
        year, month, day = numbers
    else:
        day, month, year = numbers
    try:
        return dt.date(year, month, day).isoformat()
    except ValueError:
        return ""


def extract_event_dates(*parsers: TableParser) -> tuple[str, str]:
    """Extract only explicitly labelled event dates, never upload timestamps."""
    text = clean(" ".join(part for parser in parsers for part in parser.text_parts))
    range_match = re.search(
        rf"(?:tournament\s+dates?|event\s+dates?|赛事日期|比赛日期)\s*[:：]?\s*"
        rf"({DATE_TOKEN})\s*(?:to|until|[-–—至])\s*({DATE_TOKEN})",
        text,
        re.IGNORECASE,
    )
    if range_match:
        return normalize_event_date(range_match.group(1)), normalize_event_date(range_match.group(2))

    def labelled(labels: str) -> str:
        match = re.search(rf"(?:{labels})\s*[:：]?\s*({DATE_TOKEN})", text, re.IGNORECASE)
        return normalize_event_date(match.group(1)) if match else ""

    date_begin = labelled(r"start\s+date|date\s+begin|begin|beginn|开始日期")
    date_end = labelled(r"end\s+date|date\s+end|ende|结束日期")
    single = labelled(r"tournament\s+date|event\s+date|赛事日期|比赛日期")
    return date_begin or single, date_end or single


def page_url(tournament_id: str, art: int, round_no: int | None) -> str:
    if art == TOURNAMENT_DETAILS_ART:
        params = {"lan": "1", "art": "0", "turdet": "YES"}
        return f"https://chess-results.com/tnr{tournament_id}.aspx?{urllib.parse.urlencode(params)}"
    # zeilen=99999 disables Swiss-Manager pagination: without it, player lists
    # and standings of large events silently truncate at ~150 rows while round
    # pages still show every board (the tnr1213322 lesson). The alias collector
    # already used it; the event collector must stay in sync.
    params = {"lan": "1", "art": str(art), "zeilen": "99999"}
    if round_no is not None:
        params["rd"] = str(round_no)
    return f"https://chess-results.com/tnr{tournament_id}.aspx?{urllib.parse.urlencode(params)}"


# --- private per-page raw store ---------------------------------------------


class PageStore:
    """Atomic per-page raw persistence with resume across runs.

    Every HTTP-successful body is saved *before* parsing so a parser failure
    always leaves offline-reproducible evidence. ``extra_roots`` point at the
    raw directories of previous runs; cached pages are copied forward so the
    current run directory stays self-contained and no successful page is ever
    re-requested.
    """

    def __init__(
        self,
        root: pathlib.Path | None,
        extra_roots: list[pathlib.Path],
        *,
        offline: bool = False,
        reuse_cache: bool = True,
    ):
        self.root = root
        self.extra_roots = [path for path in extra_roots if path and path.is_dir()] if reuse_cache else []
        self.offline = offline

    def _meta_path(self, root: pathlib.Path, tournament_id: str) -> pathlib.Path:
        return root / f"tnr{tournament_id}" / "pages.json"

    def _read_meta(self, root: pathlib.Path, tournament_id: str) -> dict[str, Any]:
        try:
            payload = json.loads(self._meta_path(root, tournament_id).read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def load(self, tournament_id: str, kind: str, expected_request_url: str | None = None) -> tuple[str, str] | None:
        roots = ([self.root] if self.root else []) + self.extra_roots
        for root in roots:
            target = root / f"tnr{tournament_id}" / f"{kind}.html.gz"
            if not target.is_file():
                continue
            meta = self._read_meta(root, tournament_id).get(kind) or {}
            if expected_request_url and str(meta.get("requestURL") or "") != expected_request_url:
                # The cached page was fetched with different request
                # parameters (e.g. before zeilen=99999 disabled pagination):
                # a truncated page must never satisfy a resume.
                continue
            try:
                body = gzip.decompress(target.read_bytes()).decode("utf-8", errors="replace")
            except OSError:
                continue
            url = str(meta.get("url") or page_url(tournament_id, 0, None))
            if self.root and root != self.root:
                self.save(tournament_id, kind, url, body, request_url=str(meta.get("requestURL") or ""))
            return body, url
        return None

    def save(self, tournament_id: str, kind: str, url: str, body: str, *, request_url: str = "") -> dict[str, Any]:
        content = body.encode("utf-8")
        entry = {
            "url": url,
            "requestURL": request_url or url,
            "sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content),
            "fetchedAt": now_iso(),
        }
        if self.root is None:
            return entry
        root = self.root / f"tnr{tournament_id}"
        root.mkdir(parents=True, exist_ok=True)
        root.chmod(0o700)
        target = root / f"{kind}.html.gz"
        tmp = root / f".{kind}.{os.getpid()}.tmp"
        with tmp.open("wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=9, mtime=0) as handle:
                handle.write(content)
        os.replace(tmp, target)
        target.chmod(0o600)
        meta = self._read_meta(self.root, tournament_id)
        meta[kind] = entry
        write_private_json(self._meta_path(self.root, tournament_id), meta)
        return entry


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


# --- page fetch --------------------------------------------------------------


def fetch_page_body(tournament_id: str, art: int, round_no: int | None, timeout: float, retries: int) -> tuple[str, str]:
    url = page_url(tournament_id, art, round_no)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml", "Accept-Encoding": "identity"},
    )

    def validate(body: bytes, _headers: Any) -> None:
        sample = body[:20000].lower()
        if b"<html" not in sample and b"<table" not in sample:
            raise SourceHTTPError("SOURCE_UNEXPECTED_CONTENT_TYPE", f"tnr{tournament_id} 返回内容不像 HTML 页面。")

    raw, final_url, headers = fetch_bytes(
        request,
        timeout=timeout,
        retries=retries,
        expected_types=("text/html", "application/xhtml+xml"),
        validator=validate,
    )
    return raw.decode(headers.get_content_charset() or "utf-8", errors="replace"), final_url


# --- table parsing -----------------------------------------------------------


def find_table(parser: TableParser, required: set[str]) -> list[list[Cell]]:
    for table in parser.tables:
        if not table:
            continue
        headers = {normalized_header(cell.text) for cell in table[0]}
        if required.issubset(headers):
            return table
    return []


def data_row_count(parser: TableParser) -> int:
    return sum(max(0, len(table) - 1) for table in parser.tables)


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


def find_player_table(parser: TableParser) -> list[list[Cell]]:
    # Domestic youth and master events often publish only ``No. / Name /
    # Club``.  The serial number is still a stable join key for standings and
    # pairings, so the absence of a FIDE ID/rating must not turn a perfectly
    # usable individual event into a false layout failure.
    for required in ({"name", "fideid"}, {"name", "rtg"}, {"name", "fed"}, {"no", "name"}, {"sno", "name"}):
        table = find_table(parser, required)
        if table:
            return table
    return []


def parse_players(parser: TableParser) -> dict[str, dict[str, str]]:
    table = find_player_table(parser)
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


def parse_team_standings(parser: TableParser) -> list[dict[str, Any]]:
    """Parse the team ranking table exposed at ``art=0``.

    Team events use the same endpoint as an individual starting list, but the
    table is a final team ranking (``Rk. / SNo / Team / …``).  The individual
    roster lives at art=15/16 and must not be used as the denominator for the
    team ranking validation.
    """
    table = find_table(parser, {"rk", "team"})
    result: list[dict[str, Any]] = []
    for row in table[1:]:
        values = cell_map(table, row)
        # Some team crosstables omit a serial-number column entirely.  Their
        # displayed rank is the only stable row key available from the source.
        number = clean((values.get("sno") or values.get("no") or values.get("rk") or Cell("")).text)
        name = clean((values.get("team") or Cell("")).text)
        if not number or not name:
            continue
        tie_breaks = [cell.text for key, cell in values.items() if key.startswith("tb") and cell.text]
        result.append({
            "rank": clean((values.get("rk") or Cell("")).text),
            # ``playerNo`` remains the schema's stable standing-row key.  In
            # team events it is the source's team serial number, not a player
            # FIDE number.
            "playerNo": number,
            "name": name,
            "chineseName": "",
            "fideID": "",
            "federation": clean((values.get("fed") or Cell("")).text),
            "rating": clean((values.get("rtg") or Cell("")).text),
            "club": name,
            # Team pages commonly expose match points as TB1 instead of Pts.
            "score": clean((values.get("pts") or values.get("tb1") or Cell("")).text),
            "tieBreaks": tie_breaks,
        })
    return result


def roster_number_for_name(name: str, players: dict[str, dict[str, str]]) -> str:
    """Return a roster number only when the normalized name is unambiguous."""
    wanted = normalized_pairing_name(name)
    if not wanted or wanted in NON_PLAYER_PAIRING_NAMES:
        return ""
    matches = {
        number
        for number, player in players.items()
        if wanted in {
            normalized_pairing_name(player.get("name", "")),
            normalized_pairing_name(player.get("chineseName", "")),
        }
    }
    return next(iter(matches)) if len(matches) == 1 else ""


def pairing_side(number: str, name: str, chinese_name: str, players: dict[str, dict[str, str]]) -> dict[str, str]:
    number = number or roster_number_for_name(name, players)
    known = players.get(number, {})
    return {
        key: value for key, value in {
            "playerNo": number,
            "name": name,
            "chineseName": sanitize_person_name(chinese_name) or known.get("chineseName", ""),
            "fideID": known.get("fideID", ""),
        }.items() if value
    }


def _pairing_indices(headers: list[str]) -> dict[str, int] | None:
    """Semantic column mapping for pairing tables.

    Chess-Results repeats No./Gr/Pts headers on both sides of the board; a
    plain header dict loses the second side. We map columns by position
    relative to the White/Black/Result anchors instead of hard-coding column
    numbers, so shifted layouts (missing title column, extra tie-break) still
    parse.
    """
    def index_of(name: str) -> int:
        return headers.index(name) if name in headers else -1

    board = index_of("bo")
    white = index_of("white")
    black = index_of("black")
    result = index_of("result")
    if min(board, white, black, result) < 0 or not (board < white < result < black):
        return None
    numbers = [i for i, h in enumerate(headers) if h in {"no", "snr"}]
    grs = [i for i, h in enumerate(headers) if h in {"gr", "typ"}]
    return {
        "board": board,
        "white": white,
        "black": black,
        "result": result,
        "whiteNo": max([i for i in numbers if i < white], default=-1),
        "blackNo": min([i for i in numbers if i > black], default=-1),
        "whiteGr": next((i for i in grs if white < i < result), -1),
        "blackGr": next((i for i in grs if i > black), -1),
    }


def _parse_pairings_semantic(table: list[list[Cell]], players: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    headers = [normalized_header(cell.text) for cell in table[0]]
    indices = _pairing_indices(headers)
    if indices is None:
        return []

    def cell(row: list[Cell], index: int) -> Cell:
        return row[index] if 0 <= index < len(row) else Cell("")

    result: list[dict[str, Any]] = []
    for row in table[1:]:
        white_cell = cell(row, indices["white"])
        black_cell = cell(row, indices["black"])
        white_no = clean(cell(row, indices["whiteNo"]).text) or player_number(white_cell)
        black_no = clean(cell(row, indices["blackNo"]).text) or player_number(black_cell)
        if not clean(white_cell.text):
            continue
        pgn_cell = row[-1] if len(row) - 1 > max(indices["blackNo"], indices["black"]) else Cell("")
        pgn_url = pgn_cell.links[0] if pgn_cell.links else ""
        result.append({
            "board": clean(cell(row, indices["board"]).text),
            "white": pairing_side(white_no, clean(white_cell.text), clean(cell(row, indices["whiteGr"]).text), players),
            "black": pairing_side(black_no, clean(black_cell.text), clean(cell(row, indices["blackGr"]).text), players),
            "result": clean(cell(row, indices["result"]).text),
            "hasPGN": bool(pgn_cell.text or pgn_url),
            "pgnURL": pgn_url,
        })
    return result


def _parse_pairings_fixed(table: list[list[Cell]], players: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    """Legacy fixed-column layout, kept only as a verified fallback."""
    result: list[dict[str, Any]] = []
    for row in table[1:]:
        if len(row) < 12:
            continue
        white_no = clean(row[1].text)
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
    boards = [item["board"] for item in result]
    digits = sum(1 for value in boards if value.isdigit())
    if not result or digits < max(1, len(result) // 2):
        return []
    return result


def parse_pairings(parser: TableParser, players: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    table = find_table(parser, {"bo", "white", "black", "result"})
    if not table:
        return []
    result = _parse_pairings_semantic(table, players)
    if result:
        return result
    return _parse_pairings_fixed(table, players)


def parse_team_pairings(
    parser: TableParser, team_numbers: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    """Parse a team-match pairing table without treating teams as players."""
    team_numbers = team_numbers or {}
    for table in parser.tables:
        if len(table) < 2:
            continue
        # Team pages can begin with a round caption, followed by the actual
        # header.  Recent pages also render a score as ``Res. : Res.`` rather
        # than a single Result cell.
        for header_index, header_row in enumerate(table[:-1]):
            headers = [normalized_header(cell.text) for cell in header_row]
            team_indices = [index for index, value in enumerate(headers) if value in {"team", "team1", "team2"}]
            if len(team_indices) < 2:
                continue
            result_index = headers.index("result") if "result" in headers else -1
            score_indices = [index for index, value in enumerate(headers) if value in {"result", "res"}]
            if result_index < 0 and len(score_indices) < 2:
                continue
            if result_index >= 0:
                left = max((index for index in team_indices if index < result_index), default=-1)
                right = min((index for index in team_indices if index > result_index), default=-1)
            else:
                # ``No. Team Team Res. : Res.`` names both teams before the
                # two component scores.
                left, right = team_indices[0], team_indices[1]
            if left < 0 or right < 0:
                continue
            board_index = next((index for index, value in enumerate(headers) if value in {"bo", "board", "no"}), -1)
            number_indices = [index for index, value in enumerate(headers) if value in {"no", "sno"}]
            pgn_index = next((index for index, value in enumerate(headers) if value == "pgn"), -1)

            def cell(row: list[Cell], index: int) -> Cell:
                return row[index] if 0 <= index < len(row) else Cell("")

            def side(row: list[Cell], team_index: int, before: bool) -> dict[str, str]:
                team_cell = cell(row, team_index)
                name = clean(team_cell.text)
                candidates = [index for index in number_indices if (index < team_index if before else index > team_index)]
                number_index = max(candidates) if before and candidates else min(candidates) if candidates else -1
                # A leading ``No.`` on team-match pages is normally the
                # match board, not a team serial.  Prefer the validated team
                # ranking mapping whenever it is available.
                number = team_numbers.get(name.casefold(), "") or clean(cell(row, number_index).text) or player_number(team_cell)
                value = {"name": name}
                if number:
                    value["playerNo"] = number
                return {key: val for key, val in value.items() if val}

            rows: list[dict[str, Any]] = []
            for row in table[header_index + 1:]:
                white, black = side(row, left, True), side(row, right, False)
                if not white.get("name") or not black.get("name"):
                    continue
                if result_index >= 0:
                    result = clean(cell(row, result_index).text)
                else:
                    scores = [clean(cell(row, index).text) for index in score_indices]
                    result = f"{scores[0]} - {scores[-1]}" if len(scores) >= 2 else ""
                pgn_cell = cell(row, pgn_index)
                pgn_url = pgn_cell.links[0] if pgn_cell.links else ""
                rows.append({
                    "board": clean(cell(row, board_index).text) if board_index >= 0 else str(len(rows) + 1),
                    "white": white,
                    "black": black,
                    "result": result,
                    "hasPGN": bool(pgn_url),
                    "pgnURL": pgn_url,
                })
            if rows:
                return rows
    return []


def embedded_round_tables(parser: TableParser) -> dict[int, list[list[Cell]]]:
    """Split legacy pages which put every round into a single HTML table.

    Older Chess-Results pages (notably some 2022 domestic events) ignore the
    ``rd`` query parameter and render repeated ``Round N on ...`` labels plus
    a header/data block inside one table.  ``find_table`` intentionally expects
    the first row to be a header, so it cannot consume this layout directly.
    """
    result: dict[int, list[list[Cell]]] = {}
    round_no: int | None = None
    header: list[Cell] | None = None
    rows: list[list[Cell]] = []
    marker = re.compile(r"\bround\s+(\d+)\b", re.IGNORECASE)
    for table in parser.tables:
        for row in table:
            label = clean(" ".join(cell.text for cell in row))
            matched = marker.search(label)
            if matched:
                if round_no is not None and header is not None and rows:
                    result.setdefault(round_no, [header, *rows])
                round_no, header, rows = int(matched.group(1)), None, []
                continue
            headers = {normalized_header(cell.text) for cell in row}
            if round_no is not None and {"bo", "white", "black", "result"}.issubset(headers):
                if header is not None and rows:
                    result.setdefault(round_no, [header, *rows])
                header, rows = row, []
                continue
            if round_no is not None and header is not None:
                rows.append(row)
        if round_no is not None and header is not None and rows:
            result.setdefault(round_no, [header, *rows])
        round_no, header, rows = None, None, []
    return result


def parse_pairings_for_round(
    parser: TableParser, players: dict[str, dict[str, str]], round_no: int
) -> list[dict[str, Any]]:
    """Parse the requested round, including legacy all-rounds-in-one pages."""
    table = embedded_round_tables(parser).get(round_no)
    if table:
        return _parse_pairings_semantic(table, players) or _parse_pairings_fixed(table, players)
    return parse_pairings(parser, players)


def looks_like_team_page(parser: TableParser) -> bool:
    for table in parser.tables:
        if not table:
            continue
        for row in table:
            headers = {normalized_header(cell.text) for cell in row}
            if ({"team", "result"}.issubset(headers) or {"team1", "team2"}.issubset(headers)
                    or ("team" in headers and "res" in headers)):
                return True
    return False


# --- round discovery ---------------------------------------------------------

ROUND_HEADING_PATTERNS = (
    re.compile(r"(?:after|nach)\s+(\d+)\s+round", re.IGNORECASE),
    re.compile(r"(?:rank|standing).*?after\s+round\s+(\d+)", re.IGNORECASE),
    re.compile(r"(?:round|runde|rd\.?)\s*(\d+)\s*(?:/|of|von)\s*(\d+)", re.IGNORECASE),
    re.compile(r"第\s*(\d+)\s*轮"),
)


def rounds_from_headings(parser: TableParser) -> int:
    for heading in parser.h2s:
        for pattern in ROUND_HEADING_PATTERNS:
            match = pattern.search(heading)
            if match:
                return int(match.groups()[-1])
    return 0


def rounds_from_links(parser: TableParser) -> int:
    best = 0
    for link in parser.links:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)
        if query.get("art", ["-"])[0] in {"2", "3"} and query.get("rd"):
            try:
                best = max(best, int(query["rd"][0]))
            except ValueError:
                continue
    return best


def discover_rounds(pages: list[TableParser], queue_rounds: int, max_rounds: int) -> tuple[int, dict[str, int]]:
    """Cross-validate round count from headings, page links and queue metadata."""
    candidates = {
        "heading": max((rounds_from_headings(page) for page in pages), default=0),
        "links": max((rounds_from_links(page) for page in pages), default=0),
        "queue": max(0, int(queue_rounds or 0)),
    }
    valid = [value for value in candidates.values() if 1 <= value <= 30]
    rounds = 0
    if valid:
        # Prefer agreement; otherwise trust the largest directly observed value
        # (heading/links) before human-entered queue metadata.
        for value in (candidates["heading"], candidates["links"], candidates["queue"]):
            if 1 <= value <= 30:
                rounds = value
                break
    if max_rounds:
        rounds = min(rounds, max_rounds) if rounds else max_rounds
    return rounds, candidates


def team_crosstable_rounds(parser: TableParser) -> int:
    """Infer a round-robin team schedule from a source crosstable.

    These pages omit both round links and an "after N rounds" heading.  The
    numbered opponent columns are nevertheless complete source evidence: an
    N-team round robin has N such columns and N-1 rounds.
    """
    table = find_table(parser, {"rk", "team"})
    if not table:
        return 0
    opponent_columns = [
        cell.text for cell in table[0]
        if clean(cell.text).isdigit() and 1 <= int(clean(cell.text)) <= 100
    ]
    rounds = len(opponent_columns) - 1
    return rounds if 1 <= rounds <= 30 else 0


# --- per-target collection ---------------------------------------------------


@dataclass
class CollectOptions:
    timeout: float = 30
    retries: int = 2
    delay: float = 1.0
    max_rounds: int = 0
    offline: bool = False


class EventCollector:
    """Persistent page-task collection for a single tournament."""

    def __init__(
        self,
        tournament_id: str,
        options: CollectOptions,
        store: PageStore,
        queue_rounds: int = 0,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.tid = tournament_id
        self.options = options
        self.store = store
        self.queue_rounds = queue_rounds
        self.progress = progress or (lambda _payload: None)
        self.pages_fetched = 0
        self.pages_parsed = 0
        self.pages_cached = 0
        self.pages_expected = 0
        self.snapshots: list[dict[str, Any]] = []
        self.title = f"tnr{tournament_id}"

    def report(self, stage: str, **extra: Any) -> None:
        self.progress({
            "tournamentID": self.tid,
            "stage": stage,
            "title": self.title,
            "pagesExpected": self.pages_expected,
            "pagesFetched": self.pages_fetched,
            "pagesParsed": self.pages_parsed,
            "pagesCached": self.pages_cached,
            "updatedAt": now_iso(),
            **extra,
        })

    def page(self, kind: str, art: int, round_no: int | None = None) -> tuple[TableParser, str, bool]:
        expected_url = page_url(self.tid, art, round_no)
        # Offline replay accepts whatever raw evidence exists; online resume
        # requires the cached page to match today's request parameters so
        # pre-zeilen truncated pages are refetched instead of reused.
        cached = self.store.load(self.tid, kind, None if self.options.offline else expected_url)
        if cached is not None:
            body, url = cached
            self.pages_cached += 1
        elif self.options.offline:
            raise EventCaptureError(
                "PAGE_CACHE_MISS",
                f"tnr{self.tid} 离线重放缺少页面 {kind}；请先在线补齐缺页。",
                failed_page=kind,
                structural=False,
            )
        else:
            if self.pages_fetched:
                time.sleep(self.options.delay)
            body, url = fetch_page_body(self.tid, art, round_no, self.options.timeout, self.options.retries)
            # Persist raw evidence *before* parsing: parser failures must be
            # reproducible offline and interrupted runs resume page-by-page.
            entry = self.store.save(self.tid, kind, url, body, request_url=expected_url)
            self.pages_fetched += 1
            self.snapshots.append({
                "kind": kind, "url": url,
                "sha256": entry["sha256"], "bytes": entry["bytes"],
                "localPrivatePath": f"raw/chess-results/tnr{self.tid}/{kind}.html.gz",
            })
        parser = parse_html(body, url)
        self.report("fetching", currentPage=kind)
        return parser, url, cached is not None

    def collect(self) -> dict[str, Any]:
        fetched_at = now_iso()
        self.pages_expected = 3
        self.report("probing")

        # 1. players / format probe: art=0 individual, art=15/16 team lists.
        event_format = "individual-swiss"
        players_page, players_url, _ = self.page("starting-rank", 0)
        team_rankings_page, team_rankings_url = players_page, players_url
        players = parse_players(players_page)
        if not players:
            for art in (15, 16):
                probe_page, probe_url, _ = self.page(f"player-list-art{art}", art)
                probe_players = parse_players(probe_page)
                if probe_players:
                    event_format = "team"
                    players, players_page, players_url = probe_players, probe_page, probe_url
                    break
            else:
                if data_row_count(players_page) < 2:
                    raise EventCaptureError(
                        "EVENT_EMPTY", f"tnr{self.tid} 起始名单为空或赛事未建立。", failed_page="starting-rank"
                    )
                raise EventCaptureError(
                    "PARSER_LAYOUT_CHANGED",
                    f"tnr{self.tid} 起始页有数据表但无法识别棋手名单。",
                    failed_page="starting-rank",
                )
        if not (2 <= len(players) <= 5000):
            raise EventCaptureError(
                "PARSER_LAYOUT_CHANGED", f"tnr{self.tid} 起始名单人数异常：{len(players)}", failed_page="starting-rank"
            )

        # 2. standings.
        standings_page, standings_url, _ = self.page("standings", 1)
        if event_format == "team":
            standings_page, standings_url = team_rankings_page, team_rankings_url
            standings = parse_team_standings(standings_page)
            team_numbers = {
                clean(item.get("name") or "").casefold(): str(item.get("playerNo") or "")
                for item in standings
            }
        else:
            standings = parse_standings(standings_page, players)
            team_numbers = {}
        if not standings:
            if data_row_count(standings_page) < 2:
                raise EventCaptureError(
                    "EVENT_EMPTY", f"tnr{self.tid} 排名页为空。", failed_page="standings"
                )
            raise EventCaptureError(
                "PARSER_LAYOUT_CHANGED", f"tnr{self.tid} 排名页无法解析。", failed_page="standings"
            )
        if event_format != "team" and len(standings) < max(1, int(len(players) * 0.5)):
            raise EventCaptureError(
                "VALIDATION_REGRESSION",
                f"tnr{self.tid} 排名行异常：players={len(players)} standings={len(standings)}",
                failed_page="standings",
            )
        # 3. Independent tournament details.  This canonical-host request is
        # mandatory even when another page happens to expose a FIDE link: the
        # normal pages vary by serving node, while ``turdet=YES`` is the stable
        # metadata contract.  PageStore persists it before parsing and a
        # network failure remains retryable rather than becoming a false
        # "no FIDE Event ID" verdict.
        tournament_details_page, _tournament_details_url, _ = self.page(
            "tournament-details", TOURNAMENT_DETAILS_ART
        )
        self.pages_parsed += 3
        self.title = (
            standings_page.h2s[0] if standings_page.h2s
            else players_page.h2s[0] if players_page.h2s
            else f"tnr{self.tid}"
        )
        date_begin, date_end = extract_event_dates(
            tournament_details_page, players_page, standings_page
        )
        fide_event_id = extract_fide_event_id(tournament_details_page)

        # 4. rounds: heading + page links + queue metadata cross-validation.
        rounds, round_candidates = discover_rounds(
            [standings_page, players_page], self.queue_rounds, self.options.max_rounds
        )
        if event_format == "team" and not rounds:
            crosstable_rounds = team_crosstable_rounds(standings_page)
            round_candidates["teamCrosstable"] = crosstable_rounds
            rounds = crosstable_rounds
        status = "complete"
        error_code = ""
        failed_page = ""
        round_rows: list[dict[str, Any]] = []
        if not rounds:
            status, error_code = "partial", "ROUND_COUNT_UNKNOWN"
        else:
            self.pages_expected = 3 + rounds
            self.report("rounds", rounds=rounds)
            all_rounds_page: TableParser | None = None
            all_rounds_url = ""
            for round_no in range(1, rounds + 1):
                kind = f"round-{round_no}"
                if all_rounds_page is None:
                    round_page, round_url, _ = self.page(kind, 2, round_no)
                    # Some legacy pages render every round in the first result
                    # table even when ``rd`` is supplied. Reuse that verified
                    # local page for the remaining rounds instead of issuing
                    # eight identical source requests (or failing offline
                    # replay because round-2..N cache files do not exist).
                    embedded = embedded_round_tables(round_page)
                    if all(number in embedded for number in range(1, rounds + 1)):
                        all_rounds_page, all_rounds_url = round_page, round_url
                        self.pages_expected = 4
                        self.report("rounds", rounds=rounds, legacyCombinedRounds=True)
                else:
                    round_page, round_url = all_rounds_page, all_rounds_url
                pairings = (
                    parse_team_pairings(round_page, team_numbers)
                    if event_format == "team"
                    else parse_pairings_for_round(round_page, players, round_no)
                )
                if not pairings:
                    failed_page = kind
                    if looks_like_team_page(round_page):
                        status, error_code = "partial", "TEAM_FORMAT_UNSUPPORTED"
                        event_format = "team"
                    elif data_row_count(round_page) < 2:
                        status, error_code = "partial", "PAIRINGS_NOT_PUBLISHED"
                    else:
                        status, error_code = "partial", "PARSER_LAYOUT_CHANGED"
                    break
                unknown_refs = sorted({
                    side.get("playerNo")
                    for pairing in pairings
                    for side in (pairing.get("white") or {}, pairing.get("black") or {})
                    if side.get("playerNo")
                } - set(players))
                if unknown_refs and event_format != "team":
                    # Every pairing reference must exist in the starting list;
                    # extra numbers mean the roster was truncated (pagination)
                    # or the pages belong to different groups — either way the
                    # capture cannot be trusted as a complete event.
                    raise EventCaptureError(
                        "PAIRING_REFS_OUTSIDE_ROSTER",
                        f"tnr{self.tid} 第 {round_no} 轮有 {len(unknown_refs)} 个名单外编号"
                        f"（如 {', '.join(unknown_refs[:8])}）；疑似名单分页截断，禁止作为完整赛事保存。",
                        failed_page=kind,
                    )
                missing_refs = sorted({
                    clean(side.get("name"))
                    for pairing in pairings
                    for side in (pairing.get("white") or {}, pairing.get("black") or {})
                    if (
                        not side.get("playerNo")
                        and normalized_pairing_name(side.get("name", "")) not in NON_PLAYER_PAIRING_NAMES
                    )
                })
                if missing_refs and event_format != "team":
                    raise EventCaptureError(
                        "PAIRING_REFS_MISSING",
                        f"tnr{self.tid} 第 {round_no} 轮有 {len(missing_refs)} 个对阵方无法唯一回查名单"
                        f"（如 {', '.join(missing_refs[:8])}）；禁止作为完整赛事保存。",
                        failed_page=kind,
                    )
                self.pages_parsed += 1
                round_rows.append({"round": round_no, "sourceURL": round_url, "pairings": pairings})
        if status == "partial" and not round_rows:
            event_format = event_format if event_format == "team" else "standings-only"

        payload = {
            "schemaVersion": 2,
            "parserVersion": PARSER_VERSION,
            "fetchedAt": fetched_at,
            "source": "Chess-Results",
            "tournamentID": self.tid,
            "sourceName": self.title,
            "dateBegin": date_begin or None,
            "dateEnd": date_end or None,
            "fideEventID": fide_event_id or None,
            "sourceRefs": [{"source": "Chess-Results", "tournamentID": self.tid, "url": standings_url}],
            "coverageScope": "domestic-full",
            "format": event_format,
            "captureStatus": status,
            "captureErrorCode": error_code or None,
            "failedPage": failed_page or None,
            "roundCount": rounds,
            "roundCandidates": round_candidates,
            "players": list(players.values()),
            "standings": standings,
            "rounds": round_rows,
            "evidence": {"startingRankURL": players_url, "standingsURL": standings_url},
            "sourceSnapshots": self.snapshots,
        }
        self.report("done", status=status, errorCode=error_code or None)
        return payload


# --- cloud merge --------------------------------------------------------------

# Keys that describe this capture run rather than the event itself; they never
# justify re-publishing an otherwise identical payload.
_MERGE_VOLATILE_KEYS = {"fetchedAt", "evidence", "sourceSnapshots", "releasePolicy"}
# Event-content collections: a fresh capture that came back empty (e.g. a
# standings-only partial) must never erase fuller data already published.
_MERGE_LIST_KEYS = ("players", "standings", "rounds")


def _merge_comparable(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in payload.items() if k not in _MERGE_VOLATILE_KEYS}


def merge_event_payload(existing: dict[str, Any] | None, fresh: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Merge a freshly cleaned capture with the already-published copy.

    The local worktree mirrors cloud main for machine paths (single-writer
    local-data branch, collector never pulls), so the existing file *is* the
    cloud state.  Completeness rules: locally cleaned data wins on conflict;
    non-empty collections already on the cloud are kept when the fresh capture
    lacks them.  Returns ``(payload, changed)`` — ``changed`` is False when the
    merged event content is identical to what is already published, so the
    caller can skip the write and keep it out of the release manifest.
    """
    if not isinstance(existing, dict) or not existing:
        return fresh, True
    merged = dict(existing)
    for key, value in fresh.items():
        if key in _MERGE_LIST_KEYS and not value and existing.get(key):
            continue  # keep the fuller published collection
        merged[key] = value
    changed = _merge_comparable(merged) != _merge_comparable(existing)
    return merged, changed


# --- private state -----------------------------------------------------------


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
        if not isinstance(payload, dict):
            return {"schemaVersion": 2, "events": {}}
    except (OSError, json.JSONDecodeError):
        return {"schemaVersion": 2, "events": {}}
    events = payload.get("events") or {}
    for entry in events.values():
        if isinstance(entry, dict) and "status" not in entry:
            # v1 entries were success-only checkpoints.
            entry["status"] = "complete"
    payload["schemaVersion"] = 2
    payload["events"] = events
    return payload


def write_capture_state(payload: dict[str, Any]) -> None:
    CAPTURE_STATE.parent.mkdir(parents=True, exist_ok=True)
    CAPTURE_STATE.parent.chmod(0o700)
    write_private_json(CAPTURE_STATE, payload)


def parse_timestamp(value: Any) -> dt.datetime | None:
    try:
        stamp = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=dt.timezone.utc)
    return stamp.astimezone(dt.timezone.utc)


def should_skip_target(entry: dict[str, Any], refresh_days: int) -> str:
    """Return a skip reason for queue scheduling, or '' to attempt the target."""
    if not entry:
        return ""
    status = entry.get("status") or "complete"
    now = dt.datetime.now(dt.timezone.utc)
    if status == "complete":
        stamp = parse_timestamp(entry.get("capturedAt"))
        if stamp and refresh_days > 0 and stamp >= now - dt.timedelta(days=refresh_days):
            return "recently-captured"
        return ""
    if status in {"quarantined", "retry-wait"}:
        stamp = parse_timestamp(entry.get("nextRetryAt"))
        if stamp and stamp > now and entry.get("parserVersion") == PARSER_VERSION:
            return status
        return ""
    if status == "unsupported" and entry.get("parserVersion") == PARSER_VERSION:
        return "needs-parser"
    return ""


def queue_targets(limit: int, refresh_days: int) -> list[str]:
    if not EVENT_QUEUE.exists():
        raise SystemExit("赛事线索队列不存在；请先在社区/人工数据变更后离线重建队列。")
    from event_targeting import merged_target_items

    capture_state = load_capture_state().get("events") or {}
    targets = []
    for item in merged_target_items(queue_path=EVENT_QUEUE, pool_path=DISCOVERY_POOL):
        if item.get("nextAction") not in {"capture-event", "refresh-snapshot"}:
            continue
        tournament = clean(item.get("tournamentID"))
        if not tournament:
            continue
        entry = capture_state.get(tournament) or {}
        if should_skip_target(entry, refresh_days):
            continue
        targets.append(tournament)
    return targets[:limit]


def queue_rounds_metadata() -> dict[str, int]:
    from event_targeting import merged_target_items

    result: dict[str, int] = {}
    for item in merged_target_items(queue_path=EVENT_QUEUE, pool_path=DISCOVERY_POOL):
        tournament = clean(item.get("tournamentID"))
        rounds = item.get("capturedRounds") or item.get("rounds") or 0
        if tournament and isinstance(rounds, int) and rounds > 0:
            result[tournament] = rounds
    return result


def record_target_result(
    events: dict[str, Any],
    tournament_id: str,
    *,
    status: str,
    error_code: str = "",
    failed_page: str = "",
    structural: bool = False,
    private_root: pathlib.Path | None = None,
    payload: dict[str, Any] | None = None,
    collector: EventCollector | None = None,
) -> dict[str, Any]:
    entry = events.get(tournament_id)
    entry = dict(entry) if isinstance(entry, dict) else {}
    now = dt.datetime.now(dt.timezone.utc)
    entry["attempts"] = int(entry.get("attempts") or 0) + 1
    entry["parserVersion"] = PARSER_VERSION
    entry["updatedAt"] = now_iso()
    if private_root is not None:
        entry["runPrivateRoot"] = str(private_root)
    if collector is not None:
        entry["pagesExpected"] = collector.pages_expected
        entry["pagesFetched"] = collector.pages_fetched + collector.pages_cached
        entry["pagesParsed"] = collector.pages_parsed
    if payload is not None:
        entry.update({
            "capturedAt": payload.get("fetchedAt") or now_iso(),
            "players": len(payload.get("players", [])),
            "rounds": len(payload.get("rounds", [])),
            "standings": len(payload.get("standings", [])),
            "format": payload.get("format"),
            "releasePolicy": payload.get("releasePolicy") or chess_results_release_policy(),
        })
    if status == "complete":
        entry.update(status="complete", errorCode=None, failedPage=None, nextRetryAt=None, structureFailures=0)
        events[tournament_id] = entry
        return entry
    entry["errorCode"] = error_code or None
    entry["failedPage"] = failed_page or None
    if structural:
        failures = int(entry.get("structureFailures") or 0) + 1
        entry["structureFailures"] = failures
        if error_code in {"EVENT_EMPTY"} or failures >= STRUCTURE_QUARANTINE_THRESHOLD:
            entry["status"] = "quarantined"
            entry["nextRetryAt"] = (now + dt.timedelta(days=QUARANTINE_DAYS)).replace(microsecond=0).isoformat()
        elif status == "partial":
            entry["status"] = "partial"
            entry["nextRetryAt"] = None
        else:
            entry["status"] = "unsupported"
            entry["nextRetryAt"] = None
    else:
        entry["status"] = "retry-wait" if status == "failed" else status
        backoff_minutes = min(24 * 60, 30 * (2 ** max(0, entry["attempts"] - 1)))
        entry["nextRetryAt"] = (now + dt.timedelta(minutes=backoff_minutes)).replace(microsecond=0).isoformat()
    events[tournament_id] = entry
    return entry


def tournament_id(value: str) -> str:
    value = clean(value)
    if "://" in value:
        host = (urllib.parse.urlparse(value).hostname or "").lower()
        if not (host == "chess-results.com" or host.endswith(".chess-results.com")):
            return ""
    # Chess-Results has legacy four-digit TNRs (for example World Youth 2003
    # and 2004).  They are valid source identifiers, not malformed modern IDs.
    match = re.search(r"(?:tnr)?(\d{4,9})", value, flags=re.IGNORECASE)
    return match.group(1) if match else ""


def run_command(args: list[str], *, allowed_returncodes: tuple[int, ...] = (0,)) -> int:
    """Run a local pipeline step, preserving documented partial outcomes."""
    completed = subprocess.run(args, cwd=ROOT, check=False)
    if completed.returncode not in allowed_returncodes:
        raise subprocess.CalledProcessError(completed.returncode, args)
    return completed.returncode


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
    parser.add_argument(
        "--force-source", action="store_true",
        help="force fresh event-page requests instead of reusing previous-run raw cache",
    )
    parser.add_argument("--no-players", action="store_true")
    parser.add_argument("--no-pgn", action="store_true")
    parser.add_argument("--no-rebuild", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--replay",
        action="store_true",
        help="offline re-parse from private raw pages only; zero source access",
    )
    parser.add_argument(
        "--private-root",
        type=pathlib.Path,
        help="repo 外的私有运行目录；默认写入本机应用数据目录",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="将清洗后的结构化赛事数据写入公开生成层，与已发布副本比对合并（完备性优先）",
    )
    parser.add_argument(
        "--authorized-publication",
        action="store_true",
        help="--publish 的旧别名",
    )
    args = parser.parse_args()
    if args.force_source and args.replay:
        raise SystemExit("--force-source 与 --replay 不能同时使用")
    publish = args.publish or args.authorized_publication
    if args.from_queue > 10:
        raise SystemExit("单次赛事队列最多 10 个目标；请拆分运行以保护访问预算。")
    if publish:
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
    output_root = PUBLIC_OUTPUT if publish else private_root / "extracted" / "chess-results-event-details"
    queued_ids = queue_targets(args.from_queue, args.refresh_days) if args.from_queue > 0 else []
    ids = [tournament_id(value) for value in [*args.targets, *args.tournament_id, *queued_ids]]
    ids = list(dict.fromkeys(value for value in ids if value))
    if not ids:
        if args.from_queue > 0:
            print(json.dumps({
                "events": [],
                "skippedFresh": True,
                "refreshDays": args.refresh_days,
                "releasePolicy": chess_results_release_policy(),
                "publicMutation": False,
            }, ensure_ascii=False, indent=2))
            return 0
        raise SystemExit("请提供至少一个 tnr ID 或 Chess-Results URL")

    capture_state = load_capture_state()
    captured_events = capture_state.setdefault("events", {})
    rounds_metadata = queue_rounds_metadata()
    options = CollectOptions(
        timeout=args.timeout, retries=args.retries, delay=args.delay,
        max_rounds=args.max_rounds, offline=args.replay,
    )
    progress_path = private_root / "progress.json"
    progress_state: dict[str, Any] = {"schemaVersion": 1, "targets": {}}

    def progress_writer(update: dict[str, Any]) -> None:
        progress_state["targets"][update.get("tournamentID", "?")] = update
        progress_state["updatedAt"] = now_iso()
        if not args.dry_run:
            write_private_json(progress_path, progress_state)

    # Structured per-target batch outcome. The panel consumes this file
    # directly instead of parsing logs, so a mixed batch renders as
    # "1 complete / 5 needs-parser" rather than one aggregated failure, and
    # off-queue pasted TNRs stay visible.
    result_path = private_root / "result.json"
    result_state: dict[str, Any] = {
        "schemaVersion": 1,
        "startedAt": now_iso(),
        "parserVersion": PARSER_VERSION,
        "replay": args.replay,
        "requested": list(ids),
        "targets": {},
        "summary": {},
        "postSteps": [],
    }

    def record_result(tid: str, **fields: Any) -> None:
        target = {**result_state["targets"].get(tid, {}), **fields, "updatedAt": now_iso()}
        result_state["targets"][tid] = target
        summary: dict[str, int] = {}
        for item in result_state["targets"].values():
            key = str(item.get("status") or "unknown")
            summary[key] = summary.get(key, 0) + 1
        result_state["summary"] = summary
        result_state["updatedAt"] = now_iso()
        if not args.dry_run:
            write_private_json(result_path, result_state)

    def checkpoint() -> None:
        if not args.dry_run:
            capture_state["updatedAt"] = now_iso()
            write_capture_state(capture_state)

    def run_post_step(
        step: str,
        command: list[str],
        *,
        allowed_returncodes: tuple[int, ...] = (0,),
    ) -> int | None:
        """Isolate post-processing so completed captures remain releasable."""
        try:
            return run_command(command, allowed_returncodes=allowed_returncodes)
        except subprocess.CalledProcessError as error:
            failure = {
                "step": step,
                "code": "POST_STEP_FAILED",
                "returnCode": error.returncode,
                "message": f"{step} 失败（exit {error.returncode}）；已完成赛事保持 complete。",
                "command": [str(value) for value in error.cmd],
                "updatedAt": now_iso(),
            }
            result_state["postSteps"].append(failure)
            result_state["updatedAt"] = now_iso()
            if not args.dry_run:
                write_private_json(result_path, result_state)
            print(
                f"WARNING: {failure['code']}: {failure['message']}",
                file=sys.stderr,
            )
            return None

    stats: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    budget_stopped = False
    for tid in ids:
        entry = captured_events.get(tid) if isinstance(captured_events.get(tid), dict) else {}
        # Reuse raw pages from this run and from the previous attempt so a
        # resume never re-requests a page that already passed hash-checked
        # persistence.
        extra_roots: list[pathlib.Path] = []
        previous_root = entry.get("runPrivateRoot") if entry else None
        if previous_root and not args.force_source:
            extra_roots.append(pathlib.Path(previous_root) / "raw" / "chess-results")
        store = PageStore(
            None if args.dry_run else snapshot_output,
            extra_roots,
            offline=args.replay,
            reuse_cache=not args.force_source,
        )
        collector = EventCollector(
            tid, options, store, queue_rounds=rounds_metadata.get(tid, 0), progress=progress_writer,
        )
        output = output_root / f"tnr{tid}.json"
        try:
            if output.exists() and not args.overwrite and not args.force_source and tid not in queued_ids and not args.replay:
                payload = json.loads(output.read_text(encoding="utf-8"))
            else:
                payload = collector.collect()
                payload["releasePolicy"] = chess_results_release_policy()
                if not args.dry_run:
                    output.parent.mkdir(parents=True, exist_ok=True)
                    if publish:
                        # Compare-and-merge against the already-published copy
                        # (the worktree mirrors cloud main for machine paths).
                        # Unchanged events are skipped and never re-enter the
                        # release manifest.
                        try:
                            existing = json.loads(output.read_text(encoding="utf-8"))
                        except (OSError, json.JSONDecodeError):
                            existing = None
                        payload, changed = merge_event_payload(existing, payload)
                        if changed:
                            tmp = output.with_name(f".{output.name}.{os.getpid()}.tmp")
                            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                            os.replace(tmp, output)
                    else:
                        write_private_json(output, payload)
        except EventCaptureError as error:
            entry = record_target_result(
                captured_events, tid,
                status="partial" if error.code in {"PAGE_CACHE_MISS"} else "failed",
                error_code=error.code, failed_page=error.failed_page or "",
                structural=error.structural, private_root=private_root, collector=collector,
            )
            checkpoint()
            record_result(tid, status=entry.get("status"), errorCode=error.code,
                          failedPage=error.failed_page, title=collector.title,
                          pagesFetched=collector.pages_fetched + collector.pages_cached)
            failures.append({"tournamentID": tid, "errorCode": error.code, "failedPage": error.failed_page})
            print(f"WARNING: tnr{tid} 失败（{error.code}，页面 {error.failed_page or '-'}），已记录并继续下一个目标。", file=sys.stderr)
            continue
        except SourceHTTPError as error:
            entry = record_target_result(
                captured_events, tid, status="failed", error_code=error.code,
                structural=False, private_root=private_root, collector=collector,
            )
            checkpoint()
            record_result(tid, status=entry.get("status"), errorCode=error.code, title=collector.title)
            failures.append({"tournamentID": tid, "errorCode": error.code})
            if error.code in {"VISIT_BUDGET_EXHAUSTED", "SOURCE_CIRCUIT_OPEN"}:
                budget_stopped = True
                print(f"{error.code}: 全局配额/熔断生效，停止本批剩余目标。", file=sys.stderr)
                break
            print(f"WARNING: tnr{tid} 网络失败（{error.code}），已记录退避时间并继续下一个目标。", file=sys.stderr)
            continue
        except Exception as error:  # noqa: BLE001 - single-target isolation
            entry = record_target_result(
                captured_events, tid, status="failed", error_code="UNEXPECTED_FAILURE",
                structural=False, private_root=private_root, collector=collector,
            )
            checkpoint()
            record_result(tid, status=entry.get("status"), errorCode="UNEXPECTED_FAILURE",
                          message=str(error)[:200], title=collector.title)
            failures.append({"tournamentID": tid, "errorCode": "UNEXPECTED_FAILURE", "message": str(error)[:200]})
            print(f"WARNING: tnr{tid} 意外失败：{error}；已记录并继续下一个目标。", file=sys.stderr)
            continue

        status = payload.get("captureStatus") or "complete"
        stats.append({
            "tournamentID": tid,
            "status": status,
            "format": payload.get("format"),
            "players": len(payload.get("players", [])),
            "rounds": len(payload.get("rounds", [])),
            "standings": len(payload.get("standings", [])),
            "cachedPages": collector.pages_cached,
        })
        record_result(
            tid, status=status, title=payload.get("sourceName"),
            format=payload.get("format"),
            errorCode=payload.get("captureErrorCode"), failedPage=payload.get("failedPage"),
            players=len(payload.get("players", [])), rounds=len(payload.get("rounds", [])),
            standings=len(payload.get("standings", [])), cachedPages=collector.pages_cached,
            preview=str(output) if not args.dry_run else None,
        )
        if not args.dry_run:
            if status == "complete":
                record_target_result(
                    captured_events, tid, status="complete",
                    private_root=private_root, payload=payload, collector=collector,
                )
            else:
                record_target_result(
                    captured_events, tid, status="partial",
                    error_code=payload.get("captureErrorCode") or "",
                    failed_page=payload.get("failedPage") or "",
                    structural=True, private_root=private_root, payload=payload, collector=collector,
                )
            # Checkpoint each target immediately: a later failure never erases
            # earlier completed captures.
            checkpoint()

    # Publication (--publish) fetches game PGN and optionally rebuilds derived
    # layers; without it, collection stays entirely in the private run area.
    # Manual/community data is never mutated in either mode.
    complete_ids = [item["tournamentID"] for item in stats if item.get("status") == "complete"]
    if publish and not args.dry_run and not args.no_players and complete_ids:
        command = [
            sys.executable,
            "Scripts/sync_chess_results_starting_rank_aliases.py",
            "--only-explicit",
        ]
        for tid in complete_ids:
            command.extend(["--tournament-id", tid])
        aliases_returncode = run_post_step("starting-rank-aliases", command)
        if aliases_returncode is not None:
            run_post_step(
                "domestic-players",
                [sys.executable, "Scripts/sync_domestic_players.py"],
            )
    if publish and not args.dry_run and not args.no_pgn and complete_ids:
        command = [sys.executable, "-u", "Scripts/fetch_event_pgn.py", "--workers", "1", "--full-archive"]
        command.extend(["--private-root", str(private_root)])
        if args.overwrite:
            command.append("--overwrite")
        if args.no_rebuild:
            command.append("--defer-status-rebuild")
        for tid in complete_ids:
            command.extend(["--tournament-id", tid])
        # Exit 4 is fetch_event_pgn's documented partial outcome: event pages
        # may be complete while one PGN endpoint is temporarily unavailable.
        # Do not turn that into a false all-target collection failure.  Instead
        # mark only the event that still lacks an archive for normal retry.
        pgn_returncode = run_post_step(
            "event-pgn",
            command,
            allowed_returncodes=(0, 4),
        )
        if pgn_returncode == 4:
            for tid in complete_ids:
                archive = EVENT_PGN_ARCHIVE / f"tnr{tid}.pgn"
                if archive.is_file() or source_explicitly_omits_pgn(tid):
                    continue
                record_target_result(
                    captured_events,
                    tid,
                    status="failed",
                    error_code="PGN_COLLECTION_INCOMPLETE",
                    private_root=private_root,
                )
                failures.append({"tournamentID": tid, "errorCode": "PGN_COLLECTION_INCOMPLETE"})
            checkpoint()
    if publish and not args.dry_run and not args.no_rebuild and complete_ids:
        # Publication gate order (plan §5.2): by-player packs first, then the
        # CompletenessReport (which decides the publishable set from results,
        # PGN availability and archive matching), and only then the public
        # projections. ``complete_ids`` is a capture checkpoint, never the
        # publication gate — build_event_details refuses to run without a
        # fresh report.
        build_steps = [
            ("static-player-pgn", [sys.executable, "Scripts/build_static_player_pgn.py"]),
            ("completeness-report", [sys.executable, "Scripts/build_completeness_report.py"]),
            ("event-details", [sys.executable, "Scripts/build_event_details.py"]),
            ("event-catalog", [sys.executable, "Scripts/build_event_catalog.py"]),
            ("dashboard", [sys.executable, "Scripts/build_dashboard.py"]),
        ]
        for step, command in build_steps:
            if run_post_step(step, command) is None:
                break

    print(json.dumps({
        "events": stats,
        "failures": failures,
        "budgetStopped": budget_stopped,
        "dryRun": args.dry_run,
        "replay": args.replay,
        "parserVersion": PARSER_VERSION,
        "releasePolicy": chess_results_release_policy(),
        "privateRoot": str(private_root),
        "publicMutation": bool(publish and not args.dry_run),
        "postSteps": result_state["postSteps"],
    }, ensure_ascii=False, indent=2))
    partial_batch = (
        bool(failures)
        or bool(result_state["postSteps"])
        or any(item.get("status") != "complete" for item in stats)
    )
    if failures and not stats:
        return 1
    if partial_batch:
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
