#!/usr/bin/env python3
"""Fetch full-tournament PGN from Chess-Results — high-efficiency rewrite.

Strategy shift from per-player requests to per-tournament bulk download:

1. For each tournament ID, call download_chess_results_pgn("", tid) to get
   the COMPLETE tournament PGN (empty FIDE ID = no player filter).
2. Parse all games locally, keeping only those where White or Black is a
   known Chinese player (by FIDE ID or normalised name match).
3. Write per-player files following the existing static layout.

This reduces HTTP requests from O(players × events) to O(events).

Usage:
  python3 fetch_event_pgn.py --category li-chengzhi
  python3 fetch_event_pgn.py --tournament-id 1356505
  python3 fetch_event_pgn.py --category national-amateur-master --workers 5
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import html
import json
import os
import pathlib
import re
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from build_static_player_pgn import clean  # noqa: E402
from sync_static_pgn import (  # noqa: E402
    REPO_ROOT,
    STATIC_PGN_ROOT,
    count_pgn_games,
    download_chess_results_pgn,
)
from source_policy import require_chess_results_publication  # noqa: E402
from source_http import SourceHTTPError, fetch_bytes  # noqa: E402
from stable_json import write_json  # noqa: E402

SOURCES_CSV = REPO_ROOT / "data" / "manual" / "chess-results-starting-rank-sources.csv"
ALIAS_CSV = REPO_ROOT / "data" / "manual" / "player-aliases.csv"
REGISTRY_PLAYERS = REPO_ROOT / "docs" / "data" / "registry" / "players.json"
# Complete-tournament PGN archive (every game, not only Chinese players):
# the canonical machine layer for event completeness.
EVENT_PGN_ARCHIVE = REPO_ROOT / "data" / "generated" / "chess-results-event-pgn"
EVENT_DETAILS = REPO_ROOT / "data" / "generated" / "chess-results-event-details"
COLLECTION_STATUS = REPO_ROOT / "data" / "generated" / "pgn-collection-status.json"
FIDE_EVENT_INFO_URL = "https://ratings.fide.com/tournament_information.phtml?event={event_id}"
FIDE_HREF_RE = re.compile(
    r'href\s*=\s*(?:"([^"]+)"|\'([^\']+)\'|([^\s>]+))',
    re.IGNORECASE,
)
USER_AGENT = "ChinaChessPlayerPGN/FIDEEventPGN-1.0"


class FidePGNNotPublished(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Registry / alias helpers
# ---------------------------------------------------------------------------

def normalize_name(value: str) -> str:
    return re.sub(r"[\s,.\-_'\u2019\u00b7()\uff08\uff09]+", "", clean(value)).casefold()


def load_china_fide_ids() -> set[str]:
    ids: set[str] = set()
    if not REGISTRY_PLAYERS.exists():
        return ids
    for player in json.loads(REGISTRY_PLAYERS.read_text(encoding="utf-8")):
        if str(player.get("federation", "")).upper() == "CHN":
            fid = str(player.get("fideID") or "").strip()
            if fid:
                ids.add(fid)
    return ids


def load_name_index() -> dict[str, str]:
    index: dict[str, str] = {}
    ambiguous: set[str] = set()

    def add(alias: str, fide_id: str) -> None:
        key = normalize_name(alias)
        if not key or not fide_id:
            return
        cur = index.get(key)
        if cur is None:
            index[key] = fide_id
        elif cur != fide_id:
            ambiguous.add(key)

    if REGISTRY_PLAYERS.exists():
        for player in json.loads(REGISTRY_PLAYERS.read_text(encoding="utf-8")):
            if str(player.get("federation", "")).upper() != "CHN":
                continue
            fid = str(player.get("fideID") or "").strip()
            for alias in player.get("aliases") or []:
                add(str(alias), fid)
            for key in ("displayName", "name", "chineseName", "pinyin"):
                add(str(player.get(key) or ""), fid)

    if ALIAS_CSV.exists():
        with ALIAS_CSV.open("r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                fid = clean(row.get("fide_id", ""))
                if not fid:
                    continue
                for alias in (row.get("aliases") or "").split("|"):
                    add(alias, fid)
                add(row.get("chinese_name") or "", fid)
                add(row.get("pinyin_name") or "", fid)

    for key in ambiguous:
        index.pop(key, None)
    return index


# ---------------------------------------------------------------------------
# Game parsing helpers
# ---------------------------------------------------------------------------

def split_games(pgn: str) -> list[str]:
    normalized = pgn.replace("\r\n", "\n")
    starts = [
        m.start() for m in re.finditer(r'^\[Event\s+"', normalized, flags=re.MULTILINE)
    ]
    games = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(normalized)
        chunk = normalized[start:end].strip()
        if chunk:
            games.append(chunk)
    return games


def parse_headers(game: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for m in re.finditer(
        r'^\[([A-Za-z0-9_]+)\s+"(.*)"\]', game, flags=re.MULTILINE
    ):
        headers[m.group(1)] = clean(m.group(2))
    return headers


def fide_id_for(
    headers: dict[str, str], side: str, china_ids: set[str], names: dict[str, str]
) -> str | None:
    for tag in (f"{side}FideId", f"{side}FideID", f"{side}FIDEID"):
        digits = re.sub(r"\D", "", headers.get(tag, ""))
        if digits and digits != "0" and digits in china_ids:
            return digits

    name = headers.get(side, "")
    if name:
        key = normalize_name(name)
        hit = names.get(key)
        if hit and hit in china_ids:
            return hit
        parts = [p for p in re.split(r"[,\s]+", name) if p]
        if len(parts) >= 2:
            hit = names.get(normalize_name(" ".join(reversed(parts))))
            if hit and hit in china_ids:
                return hit
    return None


# ---------------------------------------------------------------------------
# Source CSV helpers
# ---------------------------------------------------------------------------

def tournament_ids_from_sources(path: pathlib.Path, category: str) -> list[str]:
    ids: list[str] = []
    if not path.exists():
        return ids
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if category and clean(row.get("category", "")) != category:
                continue
            tid = re.sub(r"\D", "", clean(row.get("tournament_id", "")))
            if tid:
                ids.append(tid)
    return list(dict.fromkeys(ids))


def selected_tournament_ids(
    sources: pathlib.Path,
    category: str,
    explicit_ids: list[str],
    neighbor_window: int,
    max_events: int,
) -> list[str]:
    """Choose either an explicit batch or the source-table default queue.

    ``--tournament-id`` is used by the event collector after it has completed
    a bounded batch.  It must not silently append every historical row from
    the source table, otherwise a ten-event collection turns into a large,
    unrelated source crawl during the PGN stage.
    """
    explicit = [re.sub(r"\D", "", value) for value in explicit_ids]
    ids = [value for value in dict.fromkeys(explicit) if value]
    if not ids:
        ids = tournament_ids_from_sources(sources, category)

    if neighbor_window > 0:
        seeds = list(ids)
        for seed in seeds:
            base = int(seed)
            for tid in range(base - neighbor_window, base + neighbor_window + 1):
                sid = str(tid)
                if sid not in ids:
                    ids.append(sid)

    return ids[:max_events] if max_events else ids


def source_explicitly_omits_pgn(tournament_id: str) -> bool:
    """Return whether the captured event pages explicitly expose no PGN links.

    This is deliberately stricter than merely seeing no local archive: every
    non-bye pairing must have been captured and none may advertise a PGN URL.
    It is therefore a source-gap verdict, not a synthetic empty archive.
    """
    payload = _read_json(EVENT_DETAILS / f"tnr{tournament_id}.json")
    pairings: list[dict[str, Any]] = []
    for round_payload in payload.get("rounds") or []:
        for pairing in round_payload.get("pairings") or []:
            white = pairing.get("white") or {}
            black = pairing.get("black") or {}
            names = {clean(white.get("name") or "").casefold(), clean(black.get("name") or "").casefold()}
            if names & {"bye", "not paired"}:
                continue
            if white.get("playerNo") and black.get("playerNo"):
                pairings.append(pairing)
    return bool(pairings) and not any(
        bool(pairing.get("hasPGN")) or bool(clean(pairing.get("pgnURL") or ""))
        for pairing in pairings
    )


def _read_json(path: pathlib.Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def fide_event_id_for(tournament_id: str) -> str:
    value = clean(_read_json(EVENT_DETAILS / f"tnr{tournament_id}.json").get("fideEventID") or "")
    return value if re.fullmatch(r"\d{4,12}", value) else ""


def _save_private_fide_page(private_root: pathlib.Path | None, event_id: str, body: bytes, url: str) -> None:
    if private_root is None:
        return
    root = private_root / "raw" / "fide-events" / f"event{event_id}"
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    target = root / "information.html.gz"
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    tmp.write_bytes(gzip.compress(body, compresslevel=9, mtime=0))
    os.replace(tmp, target)
    target.chmod(0o600)
    meta = root / "page.json"
    meta_tmp = meta.with_name(f".{meta.name}.{os.getpid()}.tmp")
    meta_tmp.write_text(json.dumps({"url": url, "bytes": len(body)}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(meta_tmp, meta)
    meta.chmod(0o600)


def fide_pgn_links(info_html: str, base_url: str) -> list[str]:
    """Extract only official FIDE PGN links from a tournament page."""
    links: list[str] = []
    for href_groups in FIDE_HREF_RE.findall(info_html):
        raw_href = next((value for value in href_groups if value), "")
        if "pgn" not in raw_href.casefold():
            continue
        candidate = urllib.parse.urljoin(base_url, html.unescape(raw_href))
        parsed = urllib.parse.urlparse(candidate)
        host = (parsed.hostname or "").lower()
        if parsed.scheme == "https" and (host == "fide.com" or host.endswith(".fide.com")):
            links.append(candidate)
    return list(dict.fromkeys(links))


def _decode_pgn_bytes(body: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = body.decode(encoding)
        except UnicodeDecodeError:
            continue
        if split_games(text) and count_pgn_games(text) > 0:
            return text
    raise ValueError("FIDE_PGN_INVALID: response is not a parseable PGN")


def download_fide_event_pgn(event_id: str, private_root: pathlib.Path | None = None) -> str:
    """Follow an official FIDE tournament page to its advertised PGN."""
    info_url = FIDE_EVENT_INFO_URL.format(event_id=event_id)
    request = urllib.request.Request(info_url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    body, final_url, headers = fetch_bytes(
        request,
        expected_types=("text/html", "application/xhtml+xml"),
    )
    _save_private_fide_page(private_root, event_id, body, final_url)
    text = body.decode(headers.get_content_charset() or "utf-8", errors="replace")
    links = fide_pgn_links(text, final_url)
    if not links:
        raise FidePGNNotPublished(f"FIDE_EVENT_PGN_NOT_PUBLISHED: event {event_id} has no official PGN link")
    errors: list[str] = []
    for url in links[:4]:
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": "application/x-chess-pgn,text/plain,*/*"},
            )
            pgn_body, _final_url, _headers = fetch_bytes(request)
            return _decode_pgn_bytes(pgn_body)
        except (SourceHTTPError, ValueError) as error:
            errors.append(str(error))
    raise ValueError("FIDE_PGN_DOWNLOAD_INVALID: " + "; ".join(errors[:4]))


def _round_id(value: Any) -> str:
    match = re.match(r"(\d+)", clean(value))
    return match.group(1) if match else clean(value)


def _result_id(value: Any) -> str:
    return re.sub(r"\s+", "", clean(value)).replace("½", "1/2")


def _header_fide_id(headers: dict[str, str], side: str) -> str:
    for key in (f"{side}FideId", f"{side}FideID", f"{side}FIDEID"):
        value = re.sub(r"\D", "", headers.get(key, ""))
        if value and value != "0":
            return value
    return ""


def validate_fide_supplement(payload: dict[str, Any], pgn: str) -> tuple[str, dict[str, int]]:
    """Require every FIDE PGN game to match one unique captured pairing.

    FIDE ID pairs are preferred; normalized names are the fallback. Round is
    mandatory, results must agree, and duplicate/unmatched games reject the
    whole supplement. Missing Board tags are filled from the matched pairing
    so downstream completeness checks keep their round+board natural key.
    """
    candidates: list[dict[str, str]] = []
    for round_row in payload.get("rounds") or []:
        rid = _round_id(round_row.get("round"))
        for pairing in round_row.get("pairings") or []:
            white = pairing.get("white") or {}
            black = pairing.get("black") or {}
            if not white.get("playerNo") or not black.get("playerNo"):
                continue
            result = _result_id(pairing.get("result"))
            if not result or result in {"+--", "--+", "---"}:
                continue
            candidates.append({
                "round": rid,
                "board": clean(pairing.get("board")),
                "names": "|".join(sorted([normalize_name(white.get("name")), normalize_name(black.get("name"))])),
                "fide": "|".join(sorted(filter(None, [clean(white.get("fideID")), clean(black.get("fideID"))]))),
                "result": result,
            })
    games = split_games(pgn)
    if not games:
        raise ValueError("FIDE_PGN_EMPTY: no games")
    used: set[tuple[str, str]] = set()
    normalized_games: list[str] = []
    for game in games:
        headers = parse_headers(game)
        rid = _round_id(headers.get("Round"))
        if not rid:
            raise ValueError("FIDE_PGN_UNMATCHED: game has no round")
        fide_key = "|".join(sorted(filter(None, [_header_fide_id(headers, "White"), _header_fide_id(headers, "Black")])))
        fide_key = fide_key if "|" in fide_key else ""
        name_key = "|".join(sorted([normalize_name(headers.get("White")), normalize_name(headers.get("Black"))]))
        matches = [row for row in candidates if row["round"] == rid and ((fide_key and row["fide"] == fide_key) or row["names"] == name_key)]
        if len(matches) != 1:
            raise ValueError(f"FIDE_PGN_UNMATCHED: round {rid} has {len(matches)} pairing matches")
        match = matches[0]
        natural_key = (rid, match["board"])
        if natural_key in used:
            raise ValueError(f"FIDE_PGN_DUPLICATE: round {rid} board {match['board']}")
        if headers.get("Result") and match["result"] and _result_id(headers["Result"]) != match["result"]:
            raise ValueError(f"FIDE_PGN_RESULT_MISMATCH: round {rid} board {match['board']}")
        used.add(natural_key)
        if match["board"] and not clean(headers.get("Board")):
            insertion = f'[Board "{match["board"]}"]\n'
            split_at = game.find("\n\n")
            game = game[:split_at + 1] + insertion + game[split_at + 1:] if split_at >= 0 else insertion + game
        normalized_games.append(game.strip())
    return "\n\n".join(normalized_games) + "\n", {"matched": len(used), "played": len(candidates)}


# ---------------------------------------------------------------------------
# Per-event worker
# ---------------------------------------------------------------------------

def process_event(
    tournament_id: str,
    china_ids: set[str],
    names: dict[str, str],
    out_root: pathlib.Path,
    overwrite: bool,
    dry_run: bool,
    pgn_text: str | None = None,
    full_archive: bool = False,
    private_root: pathlib.Path | None = None,
) -> dict[str, Any]:
    """Split a tournament PGN per Chinese player.

    ``pgn_text`` lets callers reuse an already-downloaded PGN (the community
    contribution tool archives the raw PGN as evidence, then splits the same
    bytes) instead of hitting Chess-Results a second time.

    ``full_archive`` additionally writes the complete tournament PGN (every
    game) to ``data/generated/chess-results-event-pgn/tnr<id>.pgn`` so event
    data stays complete regardless of player nationality.
    """
    require_chess_results_publication()
    result: dict[str, Any] = {
        "tid": tournament_id,
        "status": "ok",
        "games": 0,
        "assigned": 0,
        "unassigned": 0,
        "players": 0,
        "error": "",
        "archiveSource": "chess-results",
    }

    out_dir = out_root / f"tnr{tournament_id}"
    archive_path = EVENT_PGN_ARCHIVE / f"tnr{tournament_id}.pgn"
    if pgn_text is not None:
        pgn = pgn_text
    elif full_archive and not overwrite and archive_path.is_file():
        # Repair/rebuild player splits from a validated local full-event
        # archive without another source request.  This also fixes old partial
        # per-player files which previously looked "existing" and were skipped.
        print(f"tnr{tournament_id}: 复用本地完整 PGN 归档。", file=sys.stderr, flush=True)
        pgn = archive_path.read_text(encoding="utf-8", errors="replace")
    elif full_archive and source_explicitly_omits_pgn(tournament_id):
        # A pairing page that was fully captured but exposes no per-game PGN
        # links is primary evidence that this source does not publish PGN for
        # the event.  Do not create an empty archive or hammer the generic
        # search form; the caller records it as a source gap and moves on.
        fide_event_id = fide_event_id_for(tournament_id)
        if not fide_event_id:
            result["status"] = "source_unavailable"
            result["error"] = "SOURCE_PGN_NOT_PUBLISHED"
            print(f"tnr{tournament_id}: 来源页面未公开棋谱，且无 FIDE-Event-ID。", file=sys.stderr, flush=True)
            return result
        try:
            print(f"tnr{tournament_id}: 使用 FIDE-Event-ID {fide_event_id} 检查官方 PGN…", file=sys.stderr, flush=True)
            pgn = download_fide_event_pgn(fide_event_id, private_root)
            pgn, coverage = validate_fide_supplement(_read_json(EVENT_DETAILS / f"tnr{tournament_id}.json"), pgn)
            result["archiveSource"] = "fide-event-id"
            result["supplementMatched"] = coverage["matched"]
            result["supplementPlayed"] = coverage["played"]
        except FidePGNNotPublished as exc:
            result["status"] = "source_unavailable"
            result["error"] = "FIDE_EVENT_PGN_NOT_PUBLISHED"
            print(f"tnr{tournament_id}: {exc}", file=sys.stderr, flush=True)
            return result
        except Exception as exc:
            result["status"] = "error"
            result["error"] = str(exc)
            print(f"tnr{tournament_id}: FIDE 官方 PGN 补源失败：{exc}", file=sys.stderr, flush=True)
            return result
    elif (
        not full_archive
        and not overwrite
        and out_dir.exists()
        and any(out_dir.glob("*.pgn"))
    ):
        # Player-specific splits are not evidence of a complete event archive.
        # When a caller requests --full-archive, continue to the source fetch
        # unless the canonical archive above is already present.
        result["status"] = "skipped_existing"
        return result
    else:
        try:
            print(f"tnr{tournament_id}: 正在抓取完整 PGN 棋谱…", file=sys.stderr, flush=True)
            pgn = download_chess_results_pgn("", tournament_id)
        except Exception as exc:
            result["status"] = "error"
            result["error"] = str(exc)
            print(f"tnr{tournament_id}: PGN 抓取失败：{exc}", file=sys.stderr, flush=True)
            return result

    games = split_games(pgn)
    if not games or count_pgn_games(pgn) == 0:
        result["status"] = "empty"
        return result

    if full_archive and not dry_run:
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = archive_path.with_name(f".{archive_path.name}.tmp")
        tmp.write_text(pgn.rstrip("\n") + "\n", encoding="utf-8")
        tmp.replace(archive_path)

    result["games"] = len(games)
    per_player: dict[str, list[str]] = defaultdict(list)

    for game in games:
        headers = parse_headers(game)
        matched_ids: list[str] = []
        for side in ("White", "Black"):
            fid = fide_id_for(headers, side, china_ids, names)
            if fid and fid not in matched_ids:
                matched_ids.append(fid)

        if matched_ids:
            for fid in matched_ids:
                per_player[fid].append(game)
                result["assigned"] += 1
        else:
            result["unassigned"] += 1

    result["players"] = len(per_player)

    if not dry_run and per_player:
        out_dir.mkdir(parents=True, exist_ok=True)
        for fid, player_games in per_player.items():
            path = out_dir / f"fide-{fid}-{tournament_id}.pgn"
            path.write_text("\n\n".join(player_games) + "\n", encoding="utf-8")

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bulk-fetch tournament PGN from Chess-Results and split per Chinese player."
    )
    parser.add_argument("--sources", type=pathlib.Path, default=SOURCES_CSV)
    parser.add_argument("--category", default="", help="filter sources CSV by category")
    parser.add_argument("--tournament-id", action="append", default=[], help="explicit tnr ID; repeatable")
    parser.add_argument("--neighbor-window", type=int, default=0, help="also probe IDs within +/-N of each source")
    parser.add_argument("--max-events", type=int, default=0, help="0 = no limit")
    parser.add_argument("--overwrite", action="store_true", help="refetch even if files already exist")
    parser.add_argument("--workers", type=int, default=3, help="parallel download workers")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--private-root", type=pathlib.Path, help="private run directory for raw FIDE evidence")
    parser.add_argument(
        "--full-archive",
        action="store_true",
        help="also archive the complete tournament PGN under data/generated/chess-results-event-pgn/",
    )
    parser.add_argument(
        "--defer-status-rebuild",
        action="store_true",
        help="do not rewrite the derived PGN status index; cloud snapshot rebuild will regenerate it",
    )
    args = parser.parse_args()

    ids = selected_tournament_ids(
        args.sources, args.category, args.tournament_id,
        args.neighbor_window, args.max_events,
    )

    if not ids:
        print(json.dumps({"error": "no tournament IDs to fetch"}, ensure_ascii=False, indent=2))
        return 1

    mode = "显式批次" if args.tournament_id else "来源表队列"
    print(f"PGN 队列：{len(ids)} 场（{mode}）。", file=sys.stderr, flush=True)

    print(f"Loading registry + aliases …", file=sys.stderr)
    china_ids = load_china_fide_ids()
    names = load_name_index()
    print(
        f"Registry: {len(china_ids)} CHN FIDE IDs, "
        f"{len(names)} name aliases ready.",
        file=sys.stderr,
    )

    stats = {
        "events": len(ids),
        "eventsWithGames": 0,
        "eventsSkippedExisting": 0,
        "eventsEmpty": 0,
        "eventsError": 0,
        "eventsSourceUnavailable": 0,
        "games": 0,
        "gamesAssigned": 0,
        "gamesUnassigned": 0,
        "playersWritten": 0,
        "errors": [],
    }
    outcomes: dict[str, dict[str, Any]] = {}

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                process_event,
                tid,
                china_ids,
                names,
                STATIC_PGN_ROOT / "chess-results",
                args.overwrite,
                args.dry_run,
                None,
                args.full_archive,
                args.private_root,
            ): tid
            for tid in ids
        }
        for future in as_completed(futures):
            tid = futures[future]
            try:
                res = future.result()
            except Exception as exc:
                stats["errors"].append(f"tnr{tid}: {exc}")
                stats["eventsError"] += 1
                outcomes[tid] = {"status": "fetch-failed", "errorCode": type(exc).__name__}
                continue

            status = res["status"]
            if status == "skipped_existing":
                stats["eventsSkippedExisting"] += 1
                outcomes[tid] = {"status": "archive-present"}
            elif status == "empty":
                stats["eventsEmpty"] += 1
                outcomes[tid] = {"status": "empty-response", "errorCode": "SOURCE_PGN_EMPTY"}
            elif status == "error":
                stats["eventsError"] += 1
                stats["errors"].append(f"tnr{tid}: {res['error']}")
                outcomes[tid] = {"status": "fetch-failed", "errorCode": "SOURCE_PGN_FETCH_FAILED"}
            elif status == "source_unavailable":
                stats["eventsSourceUnavailable"] += 1
                outcomes[tid] = {"status": "not-published", "errorCode": res["error"] or "SOURCE_PGN_NOT_PUBLISHED"}
            else:
                stats["eventsWithGames"] += 1
                outcomes[tid] = {"status": "fetched", "games": res["games"], "via": res.get("archiveSource") or "chess-results"}
                stats["games"] += res["games"]
                stats["gamesAssigned"] += res["assigned"]
                stats["gamesUnassigned"] += res["unassigned"]
                stats["playersWritten"] += res["players"]
                print(
                    f"tnr{tid}: {res['games']} games, {res['players']} CHN players, "
                    f"assigned={res['assigned']} unassigned={res['unassigned']}",
                    file=sys.stderr,
                )

    if not args.dry_run and not args.defer_status_rebuild:
        previous = _read_json(COLLECTION_STATUS)
        events = previous.get("events") if isinstance(previous.get("events"), dict) else {}
        attempted_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
        for tid, outcome in outcomes.items():
            events[tid] = {"tournamentID": tid, "attemptedAt": attempted_at, **outcome}
        write_json(COLLECTION_STATUS, {
            "schemaVersion": 1,
            "updatedAt": attempted_at,
            "events": dict(sorted(events.items())),
        }, ensure_ascii=False, indent=2)

    print(json.dumps(stats, ensure_ascii=False, indent=2))
    # A structured event is not eligible for a complete batch until its full
    # tournament archive exists.  Missing/unavailable PGN stays resumable.
    return 4 if stats["eventsEmpty"] or stats["eventsError"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
