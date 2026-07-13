#!/usr/bin/env python3
"""Legacy full Chess-Results player crawler (disabled by default).

This module is retained only to read/reproduce historical artifacts under a
separately documented publication authorization. Normal maintenance uses the
private target collector in ``Scripts/local/refresh.sh event-queue``. The
one-click entrypoint blocks this crawler, and its machine name map is never an
input to the authoritative registry.

Under the exceptional authorization gate, for every selected player it:

1. POSTs the player's FIDE ID to the Player-Database search form
   (``https://s3.chess-results.com/SpielerSuche.aspx?lan=1``) and parses the
   result table into per-tournament participation rows: tournament id (tnrid),
   tournament name, end date, the player's rank / rounds / field size, club and
   federation.
2. Builds a per-player tournament index (CSV) and a global tnrid catalog (JSON)
   under ``docs/data/index``.
3. Writes sanitized Chinese-character candidates to the legacy machine map;
   candidates still require human review before entering manual/correction data.
4. Optionally (``--fetch-games``) feeds the freshly discovered tnrids into the
   existing per-tournament PGN pipeline (``fetch_event_pgn.process_event``),
   which downloads the tournament PGN and splits games per Chinese player.

Crawl state is persisted for historical reproducibility. Do not use this as a
routine collection command.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import pathlib
import re
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from typing import Any, Iterable

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

# Reuse the proven Chess-Results HTTP + PGN plumbing already in the repo.
from sync_static_pgn import (  # noqa: E402
    REPO_ROOT,
    STATIC_PGN_ROOT,
    USER_AGENT,
    decode_response,
    load_form,
)
from source_http import SourceHTTPError, fetch_bytes  # noqa: E402
from source_policy import require_chess_results_publication  # noqa: E402
import fetch_event_pgn as fep  # noqa: E402

# ---------------------------------------------------------------------------
# Constants / paths
# ---------------------------------------------------------------------------

SPIELER_URL = "https://s3.chess-results.com/SpielerSuche.aspx?lan=1"

REGISTRY_PLAYERS = REPO_ROOT / "docs" / "data" / "registry" / "players.json"
ALIAS_CSV = REPO_ROOT / "data" / "manual" / "player-aliases.csv"

MANUAL_DIR = REPO_ROOT / "data" / "manual"
GENERATED_DIR = REPO_ROOT / "data" / "generated"  # machine-owned outputs
INDEX_DIR = REPO_ROOT / "docs" / "data" / "index"

PLAYER_EVENTS_CSV = GENERATED_DIR / "chess-results-player-events.csv"
NAME_MAP_CSV = GENERATED_DIR / "chess-results-player-name-map.csv"
STATE_JSON = GENERATED_DIR / "chess-results-spielersuche-state.json"
TOURNAMENTS_JSON = INDEX_DIR / "chess-results-tournaments.json"
MANIFEST_JSON = INDEX_DIR / "chess-results-spielersuche-manifest.json"

CJK_RE = re.compile(r"[㐀-鿿豈-﫿]")

# A plausible Chinese person name: 2-6 CJK chars, optional ethnic middle dot.
# Mirrors apply_aliases_to_registry.sanitize_person_name.
PERSON_NAME_RE = re.compile(r"^[\u3400-\u9fff]{1,3}(?:\u00b7[\u3400-\u9fff]{1,4})?[\u3400-\u9fff]{0,3}$")


def sanitize_person_name(value: str) -> str:
    text = " ".join(str(value or "").split()).strip(" ,\uff0c;\uff1b|\u3001.")
    if 2 <= len(text.replace("\u00b7", "")) <= 6 and PERSON_NAME_RE.match(text):
        return text
    return ""

_STATE_LOCK = Lock()


# ---------------------------------------------------------------------------
# Registry helpers
# ---------------------------------------------------------------------------

def clean(value: Any) -> str:
    return html.unescape(str(value or "")).strip()


def load_china_fide_ids() -> list[str]:
    """Ordered, de-duplicated CHN FIDE IDs from the registry (+ alias CSV)."""
    ids: list[str] = []
    seen: set[str] = set()

    def add(fid: str) -> None:
        fid = re.sub(r"\D", "", fid or "")
        if fid and fid not in seen:
            seen.add(fid)
            ids.append(fid)

    if REGISTRY_PLAYERS.exists():
        for player in json.loads(REGISTRY_PLAYERS.read_text(encoding="utf-8")):
            if str(player.get("federation", "")).upper() == "CHN":
                add(str(player.get("fideID") or ""))

    # Fall back to warehouse aliases so curated players outside the raw FIDE
    # federation list are still covered.
    if ALIAS_CSV.exists():
        with ALIAS_CSV.open("r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                add(str(row.get("fide_id") or ""))

    return ids


def registry_chinese_names() -> dict[str, str]:
    """FIDE ID -> curated Chinese name already known to the warehouse."""
    out: dict[str, str] = {}
    if REGISTRY_PLAYERS.exists():
        for player in json.loads(REGISTRY_PLAYERS.read_text(encoding="utf-8")):
            fid = re.sub(r"\D", "", str(player.get("fideID") or ""))
            name = clean(player.get("chineseName"))
            if fid and name:
                out[fid] = name
    if ALIAS_CSV.exists():
        with ALIAS_CSV.open("r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                fid = re.sub(r"\D", "", str(row.get("fide_id") or ""))
                name = clean(row.get("chinese_name"))
                if fid and name:
                    out.setdefault(fid, name)
    return out


# ---------------------------------------------------------------------------
# HTML result-table parsing
# ---------------------------------------------------------------------------

def _extract_rows(html_text: str, base_url: str) -> list[dict[str, Any]]:
    """Turn a SpielerSuche result page into structured participation rows.

    The parser is layout-tolerant: it locates the *tournament* cell by the
    ``tnr<id>`` link that has no ``snr`` query parameter, then reads the
    player's placement columns by offset from it (matching the column order the
    site has served for years: name | . | fide | club | fed | tournament |
    enddate | rank | rounds | field-size).
    """
    # We need hrefs, which the streaming parser above dropped for simplicity;
    # re-parse links per cell with a light regex over each row's raw HTML.
    rows_out: list[dict[str, Any]] = []
    # Split into <tr> chunks to keep link<->cell association simple.
    for tr in re.findall(r"<tr\b.*?</tr>", html_text, flags=re.IGNORECASE | re.DOTALL):
        cells = re.findall(r"<t[dh]\b.*?</t[dh]>", tr, flags=re.IGNORECASE | re.DOTALL)
        if len(cells) < 8:
            continue
        parsed = []
        for cell in cells:
            text = re.sub(r"<[^>]+>", " ", cell)
            text = re.sub(r"\s+", " ", html.unescape(text)).strip()
            hrefs = [
                urllib.parse.urljoin(base_url, html.unescape(m))
                for m in re.findall(r'href="([^"]+)"', cell, flags=re.IGNORECASE)
            ]
            parsed.append({"text": text, "hrefs": hrefs})

        # Locate tournament cell: a tnr link WITHOUT snr= (player links carry snr=).
        t_idx = None
        for i, c in enumerate(parsed):
            for href in c["hrefs"]:
                if re.search(r"tnr\d+", href) and "snr=" not in href.lower():
                    t_idx = i
                    break
            if t_idx is not None:
                break
        if t_idx is None:
            continue

        tnr_href = next(
            h for h in parsed[t_idx]["hrefs"] if re.search(r"tnr\d+", h)
        )
        m = re.search(r"tnr(\d+)", tnr_href)
        if not m:
            continue
        tnrid = m.group(1)

        def cell_text(idx: int) -> str:
            return parsed[idx]["text"] if 0 <= idx < len(parsed) else ""

        # Player cell + serial number: the tnr link carrying snr=.
        player_name = cell_text(0)
        snr = ""
        player_url = ""
        for c in parsed:
            for href in c["hrefs"]:
                if "snr=" in href.lower() and re.search(r"tnr\d+", href):
                    q = urllib.parse.urlparse(href).query
                    snr = urllib.parse.parse_qs(q).get("snr", [""])[0]
                    player_url = href
                    if not player_name:
                        player_name = c["text"]
                    break
            if snr:
                break

        rows_out.append(
            {
                "tnrid": tnrid,
                "tournament": cell_text(t_idx),
                "end_date": _norm_date(cell_text(t_idx + 1)),
                "rank": cell_text(t_idx + 2),
                "rounds": cell_text(t_idx + 3),
                "participants": cell_text(t_idx + 4),
                "player_name": player_name,
                "club": cell_text(t_idx - 2),
                "federation": cell_text(t_idx - 1),
                "fide_id_seen": _first_fide(parsed),
                "player_snr": snr,
                "tournament_url": tnr_href,
                "player_url": player_url,
                "cjk_names": _row_cjk_names(parsed),
            }
        )
    return rows_out


def _first_fide(parsed: list[dict[str, Any]]) -> str:
    for c in parsed:
        t = c["text"].replace(" ", "")
        if re.fullmatch(r"\d{6,9}", t):
            return t
    return ""


def _row_cjk_names(parsed: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for c in parsed:
        if CJK_RE.search(c["text"]):
            name = c["text"].strip()
            if name and name not in out:
                out.append(name)
    return out


def _norm_date(text: str) -> str:
    text = text.strip()
    if not text or "unknown" in text.lower():
        return ""
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%Y.%m.%d", "%d.%m.%Y"):
        try:
            return dt.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return text


# ---------------------------------------------------------------------------
# Network: one player search
# ---------------------------------------------------------------------------

def search_player(fide_id: str, html_sink: list[str] | None = None) -> list[dict[str, Any]]:
    """Return this FIDE ID's full tournament history from the player database.

    ``html_sink``, when given, receives the raw response HTML so callers (the
    community contribution tool) can archive it as offline-verifiable evidence.
    """
    form = load_form(SPIELER_URL)
    fields = dict(form["fields"])
    fields["ctl00$P1$txt_nachname"] = ""
    fields["ctl00$P1$txt_vorname"] = ""
    fields["ctl00$P1$txt_verein"] = ""
    fields["ctl00$P1$txt_fideID"] = fide_id
    fields["ctl00$P1$txt_FED"] = ""
    fields["ctl00$P1$txt_von_tag"] = ""
    fields["ctl00$P1$txt_bis_tag"] = ""
    fields["ctl00$P1$combo_Sort"] = "2"  # FIDE-Id, end date descending
    # Dropdown option values are 1/2/3/5 -> 250/500/1000/2000 rows; "5" = 2000 (max).
    fields["ctl00$P1$combo_anzahl_zeilen"] = "5"
    fields["ctl00$P1$cbox_FIDE"] = "on"
    fields["ctl00$P1$cb_suchen"] = "Search"

    body = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(
        form["action_url"],
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": USER_AGENT,
            "Referer": form["base_url"],
        },
        method="POST",
    )
    def validate(data: bytes, _headers: Any) -> None:
        sample = data[:20000].lower()
        if b"<html" not in sample and b"<table" not in sample:
            raise SourceHTTPError("PARSER_LAYOUT_CHANGED", f"FIDE {fide_id} SpielerSuche 返回内容不可解析。")

    data, _final_url, _headers = fetch_bytes(
        request,
        timeout=60,
        retries=2,
        expected_types=("text/html", "application/xhtml+xml"),
        validator=validate,
    )
    html_text = decode_response(data)
    if html_sink is not None:
        html_sink.append(html_text)

    rows = _extract_rows(html_text, form["base_url"])
    # Keep only rows that actually belong to the searched player when the page
    # exposes a FIDE column; otherwise trust the FIDE-ID query filter.
    filtered = [
        r
        for r in rows
        if not r["fide_id_seen"] or r["fide_id_seen"] == fide_id
    ]
    for r in filtered:
        r["fide_id"] = fide_id
    return filtered


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

EVENT_COLUMNS = [
    "fide_id",
    "tnrid",
    "tournament_name",
    "end_date",
    "rank",
    "rounds",
    "participants",
    "player_name",
    "club",
    "federation",
    "player_snr",
    "source_url",
    "crawled_at",
]

NAME_MAP_COLUMNS = [
    "fide_id",
    "chinese_name",
    "pinyin_name",
    "latin_name",
    "name_variants",
    "evidence_tnrid",
    "source",
    "source_url",
    "notes",
]


def load_state() -> dict[str, Any]:
    if STATE_JSON.exists():
        try:
            return json.loads(STATE_JSON.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"players": {}}


def save_state(state: dict[str, Any]) -> None:
    STATE_JSON.parent.mkdir(parents=True, exist_ok=True)
    STATE_JSON.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _read_events_csv() -> dict[tuple[str, str], dict[str, str]]:
    out: dict[tuple[str, str], dict[str, str]] = {}
    if PLAYER_EVENTS_CSV.exists():
        with PLAYER_EVENTS_CSV.open("r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                key = (row.get("fide_id", ""), row.get("tnrid", ""))
                out[key] = row
    return out


def _write_events_csv(rows: dict[tuple[str, str], dict[str, str]]) -> None:
    PLAYER_EVENTS_CSV.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(rows.values(), key=lambda r: (r["fide_id"], r.get("end_date", ""), r["tnrid"]))
    with PLAYER_EVENTS_CSV.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=EVENT_COLUMNS)
        writer.writeheader()
        for row in ordered:
            writer.writerow({k: row.get(k, "") for k in EVENT_COLUMNS})


def _read_name_map() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    if NAME_MAP_CSV.exists():
        with NAME_MAP_CSV.open("r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                if row.get("fide_id"):
                    out[row["fide_id"]] = row
    return out


def _write_name_map(rows: dict[str, dict[str, str]]) -> None:
    NAME_MAP_CSV.parent.mkdir(parents=True, exist_ok=True)
    with NAME_MAP_CSV.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=NAME_MAP_COLUMNS)
        writer.writeheader()
        for fide_id in sorted(rows):
            writer.writerow({k: rows[fide_id].get(k, "") for k in NAME_MAP_COLUMNS})


def _rebuild_tournament_catalog(events: dict[tuple[str, str], dict[str, str]]) -> dict[str, Any]:
    catalog: dict[str, dict[str, Any]] = {}
    for row in events.values():
        tnrid = row["tnrid"]
        entry = catalog.setdefault(
            tnrid,
            {
                "tournamentID": tnrid,
                "source": "Chess-Results",
                "name": row.get("tournament_name", ""),
                "date": row.get("end_date", ""),
                "rounds": row.get("rounds", ""),
                "participants": row.get("participants", ""),
                "url": f"https://chess-results.com/tnr{tnrid}.aspx?lan=1",
                "players": [],
            },
        )
        # Prefer the longest observed name/date/field-size.
        if len(row.get("tournament_name", "")) > len(entry["name"]):
            entry["name"] = row["tournament_name"]
        if row.get("end_date") and not entry["date"]:
            entry["date"] = row["end_date"]
        if row.get("fide_id") and row["fide_id"] not in entry["players"]:
            entry["players"].append(row["fide_id"])
    for entry in catalog.values():
        entry["players"].sort()
        entry["playerCount"] = len(entry["players"])
    ordered = sorted(catalog.values(), key=lambda e: (e.get("date", ""), e["tournamentID"]), reverse=True)
    return {"tournaments": ordered, "catalog": catalog}


def write_outputs(
    events: dict[tuple[str, str], dict[str, str]],
    name_map: dict[str, dict[str, str]],
    dry_run: bool,
) -> dict[str, int]:
    catalog = _rebuild_tournament_catalog(events)
    totals = {
        "players": len({k[0] for k in events}),
        "participations": len(events),
        "tournaments": len(catalog["catalog"]),
        "chineseNameMappings": len(name_map),
    }
    if dry_run:
        return totals

    _write_events_csv(events)
    _write_name_map(name_map)

    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    TOURNAMENTS_JSON.write_text(
        json.dumps(catalog["tournaments"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    MANIFEST_JSON.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "generatedAt": dt.datetime.now(dt.timezone.utc)
                .replace(microsecond=0)
                .isoformat(),
                "source": {
                    "name": "Chess-Results Player-Database (SpielerSuche)",
                    "url": SPIELER_URL,
                },
                "storage": {
                    "playerEvents": "data/generated/chess-results-player-events.csv",
                    "nameMap": "data/generated/chess-results-player-name-map.csv",
                    "tournamentCatalog": "docs/data/index/chess-results-tournaments.json",
                    "crawlState": "data/generated/chess-results-spielersuche-state.json",
                },
                "totals": totals,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return totals


# ---------------------------------------------------------------------------
# Crawl driver
# ---------------------------------------------------------------------------

def pick_players(args: argparse.Namespace, state: dict[str, Any]) -> list[str]:
    if args.player:
        return [re.sub(r"\D", "", p) for p in args.player if re.sub(r"\D", "", p)]

    ids = load_china_fide_ids()

    if args.refresh_days > 0:
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=args.refresh_days)
        fresh: list[str] = []
        for fid in ids:
            rec = state["players"].get(fid)
            if not rec or not rec.get("crawledAt"):
                fresh.append(fid)
                continue
            try:
                seen = dt.datetime.fromisoformat(rec["crawledAt"])
            except ValueError:
                fresh.append(fid)
                continue
            if seen.tzinfo is None:
                seen = seen.replace(tzinfo=dt.timezone.utc)
            if seen < cutoff:
                fresh.append(fid)
        ids = fresh

    if args.skip_done:
        ids = [fid for fid in ids if fid not in state["players"]]

    if args.max_players:
        ids = ids[: args.max_players]
    return ids


def crawl(args: argparse.Namespace) -> dict[str, Any]:
    # This legacy full-player crawler writes public derived tables.  Keep it
    # behind the same explicit authorization gate as Chess-Results PGN; the
    # normal local panel uses the private, target-queue event collector.
    require_chess_results_publication()
    state = load_state()
    events = _read_events_csv()
    name_map = _read_name_map()
    known_cn = registry_chinese_names()

    players = pick_players(args, state)
    print(f"Players to crawl: {len(players)}", file=sys.stderr)

    stats = {
        "requested": len(players),
        "crawled": 0,
        "withEvents": 0,
        "participations": 0,
        "newNameMappings": 0,
        "errors": [],
        "discoveredTnrids": set(),
    }

    def handle(fide_id: str) -> tuple[str, list[dict[str, Any]], str]:
        try:
            rows = search_player(fide_id)
            return fide_id, rows, ""
        except Exception as exc:  # noqa: BLE001
            return fide_id, [], str(exc)

    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()

    def absorb(fide_id: str, rows: list[dict[str, Any]], error: str) -> None:
        stats["crawled"] += 1
        if error:
            stats["errors"].append(f"{fide_id}: {error}")
            return
        if rows:
            stats["withEvents"] += 1
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
                "crawled_at": now,
            }
            stats["participations"] += 1
            stats["discoveredTnrids"].add(r["tnrid"])

            # Chinese-name evidence. Sanitize at collection time: SpielerSuche
            # name cells carry trailing commas ("薛皓文,") and the CJK sweep
            # also matches tournament-title/club cells, which are not names.
            club_clean = sanitize_person_name(r["club"])
            cjk = [
                v for v in (sanitize_person_name(n) for n in r["cjk_names"])
                if v and v != club_clean
            ]
            if cjk and fide_id not in name_map:
                chinese = known_cn.get(fide_id) or cjk[0]
                name_map[fide_id] = {
                    "fide_id": fide_id,
                    "chinese_name": chinese,
                    "pinyin_name": "",
                    "latin_name": r["player_name"] if not CJK_RE.search(r["player_name"]) else "",
                    "name_variants": "|".join(dict.fromkeys(cjk)),
                    "evidence_tnrid": r["tnrid"],
                    "source": "chess-results-spielersuche",
                    "source_url": r["player_url"] or r["tournament_url"],
                    "notes": f"SpielerSuche FIDE {fide_id} tnr{r['tnrid']} CJK name column",
                }
                stats["newNameMappings"] += 1

        state["players"][fide_id] = {
            "crawledAt": now,
            "events": len(rows),
        }

    if args.workers > 1:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(handle, fid): fid for fid in players}
            done = 0
            for future in as_completed(futures):
                fide_id, rows, error = future.result()
                absorb(fide_id, rows, error)
                done += 1
                if done % args.checkpoint == 0:
                    _checkpoint(state, events, name_map, args.dry_run, done, len(players))
                time.sleep(args.delay)
    else:
        for i, fide_id in enumerate(players, 1):
            fide_id, rows, error = handle(fide_id)
            absorb(fide_id, rows, error)
            if i % args.checkpoint == 0:
                _checkpoint(state, events, name_map, args.dry_run, i, len(players))
            time.sleep(args.delay)

    totals = write_outputs(events, name_map, args.dry_run)
    if not args.dry_run:
        save_state(state)

    stats["discoveredTnrids"] = sorted(stats["discoveredTnrids"])
    stats["totals"] = totals
    return stats


def _checkpoint(
    state: dict[str, Any],
    events: dict[tuple[str, str], dict[str, str]],
    name_map: dict[str, dict[str, str]],
    dry_run: bool,
    done: int,
    total: int,
) -> None:
    if dry_run:
        return
    with _STATE_LOCK:
        write_outputs(events, name_map, dry_run=False)
        save_state(state)
    print(f"  checkpoint {done}/{total} written", file=sys.stderr)


# ---------------------------------------------------------------------------
# Stage 2: chain into the per-tournament games/standings fetch
# ---------------------------------------------------------------------------

def fetch_games(tnrids: Iterable[str], args: argparse.Namespace) -> dict[str, Any]:
    tnrids = [t for t in dict.fromkeys(tnrids) if t]
    if args.max_events:
        tnrids = tnrids[: args.max_events]

    print(f"Fetching games for {len(tnrids)} tournament(s) …", file=sys.stderr)
    china_ids = fep.load_china_fide_ids()
    names = fep.load_name_index()
    out_root = STATIC_PGN_ROOT / "chess-results"

    summary = {"events": len(tnrids), "withGames": 0, "empty": 0, "errors": 0, "playersWritten": 0}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(fep.process_event, tid, china_ids, names, out_root, args.overwrite, args.dry_run): tid
            for tid in tnrids
        }
        for future in as_completed(futures):
            tid = futures[future]
            try:
                res = future.result()
            except Exception as exc:  # noqa: BLE001
                summary["errors"] += 1
                print(f"  tnr{tid}: {exc}", file=sys.stderr)
                continue
            status = res["status"]
            if status == "error":
                summary["errors"] += 1
            elif status == "empty":
                summary["empty"] += 1
            elif status in ("ok",):
                summary["withGames"] += 1
                summary["playersWritten"] += res["players"]
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--player", action="append", default=[], help="explicit FIDE ID to crawl; repeatable")
    p.add_argument("--max-players", type=int, default=0, help="0 = no limit")
    p.add_argument("--refresh-days", type=int, default=0, help="only re-crawl players not seen in N days")
    p.add_argument("--skip-done", action="store_true", help="skip any player already in crawl state")
    p.add_argument("--delay", type=float, default=1.0, help="seconds between player searches")
    p.add_argument("--workers", type=int, default=2, help="parallel workers")
    p.add_argument("--checkpoint", type=int, default=50, help="flush outputs every N players")
    p.add_argument("--fetch-games", action="store_true", help="after crawl, pull PGN for discovered tnrids")
    p.add_argument("--max-events", type=int, default=0, help="cap tournaments fetched in --fetch-games")
    p.add_argument("--overwrite", action="store_true", help="refetch PGN even if files exist")
    p.add_argument("--dry-run", action="store_true", help="do not write any files")
    return p


def main() -> int:
    args = build_parser().parse_args()
    result = crawl(args)

    if args.fetch_games and result["discoveredTnrids"]:
        result["gamesFetch"] = fetch_games(result["discoveredTnrids"], args)

    printable = dict(result)
    printable["discoveredTnrids"] = len(result["discoveredTnrids"])
    print(json.dumps(printable, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
