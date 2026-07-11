#!/usr/bin/env python3
"""Import Chinese player names from Chess-Results starting-rank tables.

China Chess Association master-event pages often put the player's Chinese name
in the Swiss-Manager "Typ" column. This script treats those rows as identity
evidence:

- rows with FIDE ID update data/manual/player-aliases.csv;
- rows without FIDE ID become immutable domestic sightings.

Some lower/candidate groups have no Typ column and put Chinese names directly
in the Name column. Those rows are still useful as domestic sightings.

The script is conservative. It never merges no-FIDE players by name and it does
not overwrite an existing reviewed Chinese name with a newly scraped value.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import html
import http.client
import json
import pathlib
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

from apply_aliases_to_registry import sanitize_person_name


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE_CSV = REPO_ROOT / "data" / "manual" / "chess-results-starting-rank-sources.csv"
ALIAS_CSV = REPO_ROOT / "data" / "manual" / "player-aliases.csv"
SIGHTINGS_CSV = REPO_ROOT / "data" / "manual" / "domestic-player-sightings.csv"
SOURCE_NAME = "chess-results-starting-rank"
USER_AGENT = "ChinaChessPlayerPGN/StartingRankAliasSync"
MASTER_TITLE_TERMS = (
    "national amateur chess master tournament",
    "全国国际象棋棋协大师赛",
    "棋协大师赛",
    "chinese chess league",
    "china chess league",
    "国际象棋甲级联赛",
    "国际象棋联赛",
    "李成智",
    "全国少年儿童冠军赛",
    "全国国际象棋青少年锦标赛",
    "chinese national youth chess championship",
)
# Swiss-Manager player-list views that carry the Typ column:
# art=0 starting rank; art=15/16 alphabetical player lists on team events.
PLAYER_LIST_ARTS = {"0", "15", "16"}
ALIAS_FIELDS = ["fide_id", "chinese_name", "pinyin_name", "aliases", "source", "confidence", "notes"]
SIGHTING_FIELDS = [
    "sighting_id",
    "source",
    "event_id",
    "event_name",
    "event_date",
    "group",
    "age_stage",
    "player_name",
    "chinese_name",
    "pinyin_name",
    "sex",
    "birth_year",
    "province",
    "club",
    "rank",
    "score",
    "source_player_no",
    "source_url",
    "notes",
]


@dataclass(frozen=True)
class SourcePage:
    tournament_id: str
    url: str
    category: str = ""
    notes: str = ""


@dataclass(frozen=True)
class StartingRankRow:
    tournament_id: str
    snode: str
    url: str
    event_name: str
    player_no: str
    title: str
    name: str
    fide_id: str
    federation: str
    rating: str
    sex: str
    chinese_name: str
    chinese_name_source: str
    club: str


class ChessResultsHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table_stack: list[list[list[str]]] = []
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None
        self._h2_depth = 0
        self._title_depth = 0
        self._h2_parts: list[str] = []
        self.h2s: list[str] = []
        self._title_parts: list[str] = []
        self.title = ""
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key.lower(): value or "" for key, value in attrs}
        if tag == "table":
            self._table_stack.append([])
        elif tag == "tr" and self._table_stack:
            self._current_row = []
        elif tag in {"td", "th"} and self._current_row is not None:
            self._current_cell = []
        elif tag == "h2":
            self._h2_depth += 1
            self._h2_parts = []
        elif tag == "title":
            self._title_depth += 1
            self._title_parts = []
        elif tag == "a":
            href = attr.get("href", "")
            if href:
                self.links.append(html.unescape(href))

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._current_cell is not None and self._current_row is not None:
            self._current_row.append(clean(" ".join(self._current_cell)))
            self._current_cell = None
        elif tag == "tr" and self._table_stack and self._current_row is not None:
            if any(cell for cell in self._current_row):
                self._table_stack[-1].append(self._current_row)
            self._current_row = None
        elif tag == "table" and self._table_stack:
            table = self._table_stack.pop()
            if table:
                self.tables.append(table)
        elif tag == "h2" and self._h2_depth:
            value = clean(" ".join(self._h2_parts))
            if value:
                self.h2s.append(value)
            self._h2_depth -= 1
        elif tag == "title" and self._title_depth:
            self.title = clean(" ".join(self._title_parts))
            self._title_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell.append(data)
        if self._h2_depth:
            self._h2_parts.append(data)
        if self._title_depth:
            self._title_parts.append(data)


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync Chinese names from Chess-Results starting-rank Typ columns.")
    parser.add_argument("--sources", type=pathlib.Path, default=SOURCE_CSV)
    parser.add_argument("--player-aliases", type=pathlib.Path, default=ALIAS_CSV)
    parser.add_argument("--domestic-sightings", type=pathlib.Path, default=SIGHTINGS_CSV)
    parser.add_argument("--url", action="append", default=[], help="extra Chess-Results starting-rank URL")
    parser.add_argument("--tournament-id", action="append", default=[], help="extra Chess-Results tournament ID")
    parser.add_argument(
        "--only-explicit",
        action="store_true",
        help="skip the source CSV and fetch only --url/--tournament-id targets",
    )
    parser.add_argument("--include-non-master", action="store_true", help="accept pages whose title is not a master tournament")
    parser.add_argument("--no-discover-linked-groups", action="store_true", help="do not follow linked group starting-rank pages")
    parser.add_argument("--neighbor-window", type=int, default=0, help="probe tournament IDs around every source ID")
    parser.add_argument("--timeout", type=float, default=25.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--delay", type=float, default=0.4)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    sources = [] if args.only_explicit else load_sources(args.sources)
    sources.extend(SourcePage(tournament_id_from_url(url) or "", normalize_starting_rank_url(url)) for url in args.url)
    sources.extend(SourcePage(tournament_id=tid, url=url_for_tournament(tid)) for tid in args.tournament_id)
    if args.neighbor_window > 0:
        sources.extend(neighbor_sources(sources, args.neighbor_window))

    rows, page_stats = collect_rows(
        sources,
        include_non_master=args.include_non_master,
        discover_linked_groups=not args.no_discover_linked_groups,
        timeout=args.timeout,
        retries=args.retries,
        delay=args.delay,
    )

    alias_stats = update_aliases(args.player_aliases, rows, args.dry_run)
    sighting_stats = update_domestic_sightings(args.domestic_sightings, rows, args.dry_run)
    stats = {
        **page_stats,
        **alias_stats,
        **sighting_stats,
        "dryRun": args.dry_run,
    }
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def load_sources(path: pathlib.Path) -> list[SourcePage]:
    if not path.exists():
        return []
    sources: list[SourcePage] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            tournament_id = clean(row.get("tournament_id"))
            url = clean(row.get("url")) or (url_for_tournament(tournament_id) if tournament_id else "")
            if not url and not tournament_id:
                continue
            url = normalize_starting_rank_url(url or url_for_tournament(tournament_id))
            sources.append(
                SourcePage(
                    tournament_id=tournament_id or tournament_id_from_url(url) or "",
                    url=url,
                    category=clean(row.get("category")),
                    notes=clean(row.get("notes")),
                )
            )
    return sources


def neighbor_sources(sources: list[SourcePage], window: int) -> list[SourcePage]:
    result: list[SourcePage] = []
    seen = {source.tournament_id for source in sources if source.tournament_id}
    for source in sources:
        if not source.tournament_id.isdigit():
            continue
        base = int(source.tournament_id)
        for tid in range(base - window, base + window + 1):
            text_id = str(tid)
            if text_id in seen:
                continue
            seen.add(text_id)
            result.append(
                SourcePage(
                    tournament_id=text_id,
                    url=url_for_tournament(text_id),
                    category="neighbor-probe",
                    notes=f"neighbor of tnr{source.tournament_id}",
                )
            )
    return result


def collect_rows(
    sources: list[SourcePage],
    *,
    include_non_master: bool,
    discover_linked_groups: bool,
    timeout: float,
    retries: int,
    delay: float,
) -> tuple[list[StartingRankRow], dict[str, int]]:
    rows: list[StartingRankRow] = []
    queue = list(dict.fromkeys(normalize_starting_rank_url(source.url) for source in sources if source.url))
    seen_urls: set[str] = set()
    skipped_non_master = 0
    failed_pages = 0
    parsed_pages = 0
    duplicate_page_urls = 0

    while queue:
        url = queue.pop(0)
        if url in seen_urls:
            duplicate_page_urls += 1
            continue
        seen_urls.add(url)

        try:
            text, final_url = fetch_text(url, timeout=timeout, retries=retries)
        except Exception as error:
            failed_pages += 1
            print(f"warn: failed {url}: {error}", file=sys.stderr)
            continue

        document = parse_html(text)
        event_name = event_name_from_document(document)
        if not include_non_master and not is_master_event(event_name, document.title):
            skipped_non_master += 1
            continue

        parsed = parse_starting_rank_rows(document, final_url)
        rows.extend(parsed)
        parsed_pages += 1

        if discover_linked_groups:
            for linked_url in starting_rank_links(document.links, final_url):
                if linked_url not in seen_urls and linked_url not in queue:
                    queue.append(linked_url)

        if delay > 0 and queue:
            time.sleep(delay)

    unique_rows = dedupe_rows(rows)
    return unique_rows, {
        "sourcePages": len(sources),
        "fetchedPages": len(seen_urls),
        "parsedPages": parsed_pages,
        "failedPages": failed_pages,
        "skippedNonMasterPages": skipped_non_master,
        "duplicatePageURLs": duplicate_page_urls,
        "startingRankRows": len(unique_rows),
        "startingRankRowsWithFideID": sum(1 for row in unique_rows if row.fide_id),
        "startingRankRowsWithoutFideID": sum(1 for row in unique_rows if not row.fide_id),
    }


def fetch_text(url: str, *, timeout: float, retries: int) -> tuple[str, str]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Encoding": "identity",
            "Connection": "close",
        },
    )
    context = ssl._create_unverified_context()
    last_error: Exception | None = None
    for attempt in range(max(1, retries + 1)):
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                content = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
                return content.decode(charset, errors="replace"), response.geturl()
        except (http.client.IncompleteRead, urllib.error.URLError, TimeoutError, OSError) as error:
            last_error = error
            if attempt < retries:
                time.sleep(1.2 * (attempt + 1))
    assert last_error is not None
    raise last_error


def parse_html(text: str) -> ChessResultsHTMLParser:
    parser = ChessResultsHTMLParser()
    parser.feed(text)
    parser.close()
    return parser


def parse_starting_rank_rows(document: ChessResultsHTMLParser, url: str) -> list[StartingRankRow]:
    event_name = event_name_from_document(document)
    tournament_id = tournament_id_from_url(url) or ""
    snode = snode_from_url(url)
    result: list[StartingRankRow] = []

    for table in document.tables:
        header_index, header = starting_rank_header(table)
        if header_index is None:
            continue
        columns = {normalize_header(name): index for index, name in enumerate(header)}
        group_name_counts: dict[str, int] = {}
        typ_name_counts: dict[str, int] = {}
        for raw in table[header_index + 1 :]:
            row = padded_row(raw, len(header))
            value = sanitize_person_name(field(row, columns, "gr", "group"))
            if value:
                group_name_counts[value] = group_name_counts.get(value, 0) + 1
            typ_value = sanitize_person_name(field(row, columns, "typ", "type"))
            if typ_value:
                typ_name_counts[typ_value] = typ_name_counts.get(typ_value, 0) + 1
        for raw in table[header_index + 1 :]:
            if len(raw) < 3:
                continue
            row = padded_row(raw, len(header))
            name = clean_name_cell(field(row, columns, "name", "playername"))
            typ_name = sanitize_person_name(field(row, columns, "typ", "type"))
            if is_non_person_label(typ_name) or typ_name_counts.get(typ_name, 0) >= 3:
                typ_name = ""
            group_name = sanitize_person_name(field(row, columns, "gr", "group"))
            if is_non_person_label(group_name) or group_name_counts.get(group_name, 0) >= 3:
                group_name = ""
            name_as_chinese = sanitize_person_name(name)
            if typ_name:
                chinese_name = typ_name
                chinese_name_source = "Typ"
            elif group_name:
                # Many recent Chess-Results templates put the Chinese
                # registration name in Swiss-Manager's Gr column.
                chinese_name = group_name
                chinese_name_source = "Gr"
            elif name_as_chinese:
                chinese_name = name_as_chinese
                chinese_name_source = "Name"
            else:
                chinese_name = ""
                chinese_name_source = ""
            if not name or not chinese_name:
                continue
            fide_id = digits_only(field(row, columns, "fideid", "fide", "idnumber"))
            player_no = field(row, columns, "no", "rank", "startno", "start")
            result.append(
                StartingRankRow(
                    tournament_id=tournament_id,
                    snode=snode,
                    url=url,
                    event_name=event_name,
                    player_no=player_no,
                    title=field(row, columns, "title", ""),
                    name=name,
                    fide_id=fide_id,
                    federation=field(row, columns, "fed", "federation"),
                    rating=field(row, columns, "rtgi", "rating", "rtg"),
                    sex=normalize_sex(field(row, columns, "sex", "gender")),
                    chinese_name=chinese_name,
                    chinese_name_source=chinese_name_source,
                    club=field(row, columns, "clubcity", "club", "clubcitynation", "clubcityfed", "team"),
                )
            )
    return result


def starting_rank_header(table: list[list[str]]) -> tuple[int | None, list[str]]:
    for index, row in enumerate(table[:5]):
        normalized = [normalize_header(cell) for cell in row]
        if "name" in normalized and ({"typ", "type", "gr", "clubcity", "club"} & set(normalized)):
            return index, row
    return None, []


def update_aliases(path: pathlib.Path, rows: list[StartingRankRow], dry_run: bool) -> dict[str, int]:
    existing = read_csv_rows(path, ALIAS_FIELDS)
    by_fide: dict[str, dict[str, str]] = {}
    order: list[str] = []
    for row in existing:
        fide_id = clean(row.get("fide_id"))
        if not fide_id:
            continue
        if fide_id not in by_fide:
            by_fide[fide_id] = ensure_fields(row, ALIAS_FIELDS)
            order.append(fide_id)

    added = 0
    updated = 0
    conflicts = 0
    for source_row in rows:
        if not source_row.fide_id:
            continue
        aliases = alias_candidates(source_row)
        evidence = evidence_note(source_row)
        current = by_fide.get(source_row.fide_id)
        if current is None:
            by_fide[source_row.fide_id] = {
                "fide_id": source_row.fide_id,
                "chinese_name": source_row.chinese_name,
                "pinyin_name": "",
                "aliases": "|".join(aliases),
                "source": SOURCE_NAME,
                "confidence": "derived",
                "notes": evidence,
            }
            order.append(source_row.fide_id)
            added += 1
            continue

        changed = False
        existing_cn = clean(current.get("chinese_name"))
        if not existing_cn:
            current["chinese_name"] = source_row.chinese_name
            changed = True
        elif existing_cn != source_row.chinese_name:
            conflicts += 1
            aliases.append(source_row.chinese_name)
            evidence = f"{evidence}; Typ conflict kept as alias: {source_row.chinese_name}"

        merged_aliases = merge_pipe_values(current.get("aliases", ""), aliases)
        if merged_aliases != clean(current.get("aliases")):
            current["aliases"] = merged_aliases
            changed = True

        source = merge_pipe_values(current.get("source", ""), [SOURCE_NAME])
        if source != clean(current.get("source")):
            current["source"] = source
            changed = True

        if not clean(current.get("confidence")):
            current["confidence"] = "derived"
            changed = True

        notes = append_note(current.get("notes", ""), evidence)
        if notes != clean(current.get("notes")):
            current["notes"] = notes
            changed = True

        if changed:
            updated += 1

    if not dry_run:
        write_csv_rows(path, [by_fide[fide_id] for fide_id in order], ALIAS_FIELDS)

    return {
        "fideAliasRowsAdded": added,
        "fideAliasRowsUpdated": updated,
        "fideAliasChineseNameConflicts": conflicts,
        "fideAliasRowsTotal": len(order),
    }


def update_domestic_sightings(path: pathlib.Path, rows: list[StartingRankRow], dry_run: bool) -> dict[str, int]:
    all_existing = read_csv_rows(path, SIGHTING_FIELDS)
    existing = [row for row in all_existing if not invalid_scraped_sighting(row)]
    invalid_removed = len(all_existing) - len(existing)
    # Sightings are immutable evidence. Never rebuild this file from only the
    # pages that happened to succeed in the current network run: doing so made
    # a partial refresh silently delete every previously discovered player.
    # New observations are appended idempotently by their stable sighting ID.
    seen = {clean(row.get("sighting_id")) for row in existing if clean(row.get("sighting_id"))}
    seen_observations = {domestic_observation_key(row) for row in existing}
    additions: list[dict[str, str]] = []
    for row in rows:
        if row.fide_id:
            continue
        sighting_id = domestic_sighting_id(row)
        candidate = {
            "sighting_id": sighting_id,
            "source": SOURCE_NAME,
            "event_id": f"chess-results-tnr{row.tournament_id}",
            "event_name": row.event_name,
            "event_date": "",
            "group": group_name(row),
            "age_stage": age_stage_from_event(row.event_name),
            "player_name": row.name,
            "chinese_name": row.chinese_name,
            "pinyin_name": "",
            "sex": row.sex,
            "birth_year": "",
            "province": "",
            "club": row.club,
            "rank": "",
            "score": "",
            "source_player_no": row.player_no,
            "source_url": row.url,
            "notes": evidence_note(row),
        }
        observation_key = domestic_observation_key(candidate)
        if sighting_id in seen or observation_key in seen_observations:
            continue
        additions.append(candidate)
        seen.add(sighting_id)
        seen_observations.add(observation_key)

    if not dry_run:
        write_csv_rows(path, existing + additions, SIGHTING_FIELDS)

    return {
        "domesticSightingsAdded": len(additions),
        "domesticSightingsTotal": len(existing) + len(additions),
        "invalidDomesticSightingsRemoved": invalid_removed,
    }


def domestic_observation_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    """Semantic key stable across Chess-Results host/SNode URL redirects."""
    return (
        clean(row.get("source")).casefold(),
        clean(row.get("event_id")).casefold(),
        clean(row.get("source_player_no")),
        clean(row.get("chinese_name") or row.get("player_name")).casefold(),
    )


def is_non_person_label(value: Any) -> bool:
    text = clean(value)
    return bool(re.search(r"(?:棋协|候补|大师|棋士|男子|女子|公开|一级|二级|三级|四级|混合|组)$", text))


def invalid_scraped_sighting(row: dict[str, Any]) -> bool:
    return (
        clean(row.get("source")) == SOURCE_NAME
        and is_non_person_label(row.get("chinese_name") or row.get("player_name"))
    )


def read_csv_rows(path: pathlib.Path, fields: list[str]) -> list[dict[str, str]]:
    if not path.exists():
        return []
    rows: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if not any(clean(value) for value in row.values()):
                continue
            rows.append(ensure_fields(row, fields))
    return rows


def write_csv_rows(path: pathlib.Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: clean(row.get(field)) for field in fields})


def ensure_fields(row: dict[str, Any], fields: list[str]) -> dict[str, str]:
    return {field: clean(row.get(field)) for field in fields}


def starting_rank_links(links: list[str], base_url: str) -> list[str]:
    result: list[str] = []
    base_tournament_id = tournament_id_from_url(base_url)
    for link in links:
        absolute = urllib.parse.urljoin(base_url, link)
        if tournament_id_from_url(absolute) != base_tournament_id:
            continue
        parsed = urllib.parse.urlparse(absolute)
        query = urllib.parse.parse_qs(parsed.query)
        if query.get("art", [""])[0] != "0" and "art=0" not in absolute:
            continue
        result.append(normalize_starting_rank_url(absolute))
    return list(dict.fromkeys(result))


def url_for_tournament(tournament_id: str) -> str:
    return f"https://chess-results.com/tnr{digits_only(tournament_id)}.aspx?lan=1&art=0"


def normalize_starting_rank_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url.strip())
    if not parsed.scheme:
        parsed = urllib.parse.urlparse("https://" + url.strip())
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    # Force the English UI so table headers stay parseable (Name/FideID/Typ).
    query["lan"] = ["1"]
    art = query.get("art", ["0"])[0]
    query["art"] = [art if art in PLAYER_LIST_ARTS else "0"]
    # Ask for the complete list; large events paginate otherwise.
    query["zeilen"] = ["99999"]
    query.pop("turdet", None)
    normalized_query = urllib.parse.urlencode({key: values[-1] for key, values in query.items()})
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", normalized_query, ""))


def tournament_id_from_url(url: str) -> str | None:
    match = re.search(r"tnr(\d+)\.aspx", url, re.IGNORECASE)
    return match.group(1) if match else None


def snode_from_url(url: str) -> str:
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    return clean(query.get("SNode", [""])[0])


def event_name_from_document(document: ChessResultsHTMLParser) -> str:
    for value in document.h2s:
        if value.lower() != "starting rank":
            return value
    title = document.title
    return clean(title.replace("Chess-Results Server Chess-results.com -", ""))


def is_master_event(*values: str) -> bool:
    text = " ".join(clean(value).casefold() for value in values)
    return any(term.casefold() in text for term in MASTER_TITLE_TERMS)


def field(row: list[str], columns: dict[str, int], *names: str) -> str:
    for name in names:
        normalized = normalize_header(name)
        if normalized in columns and columns[normalized] < len(row):
            return clean(row[columns[normalized]])
    return ""


def padded_row(row: list[str], length: int) -> list[str]:
    if len(row) >= length:
        return row
    return [*row, *([""] * (length - len(row)))]


def normalize_header(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "", clean(value).casefold())
    if normalized == "":
        return "title"
    if normalized in {"no", "nr"}:
        return "no"
    if normalized in {"fideid", "fideidno", "fide"}:
        return "fideid"
    if normalized in {"clubcity", "clubcityfed", "clubcitynation"}:
        return "clubcity"
    return normalized


def normalize_sex(value: str) -> str:
    text = clean(value).casefold()
    if text in {"w", "f", "female", "girl"}:
        return "F"
    if text in {"m", "male", "boy"}:
        return "M"
    return clean(value)


def alias_candidates(row: StartingRankRow) -> list[str]:
    english = clean(row.name)
    values = [row.chinese_name, english]
    if "," in english:
        left, right = [clean(part) for part in english.split(",", 1)]
        values.extend([f"{right} {left}", f"{left} {right}"])
    return ordered_unique(values)


def domestic_sighting_id(row: StartingRankRow) -> str:
    basis = "|".join(
        [
            SOURCE_NAME,
            row.tournament_id,
            row.snode,
            row.player_no,
            row.name,
            row.chinese_name,
            row.club,
        ]
    )
    return "sighting-cr-start-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def evidence_note(row: StartingRankRow) -> str:
    source_column = f"{row.chinese_name_source or 'Typ'} column"
    details = [f"tnr{row.tournament_id}", group_name(row), f"No.{row.player_no}" if row.player_no else "", source_column]
    return " ".join(part for part in details if part)


def group_name(row: StartingRankRow) -> str:
    value = row.event_name
    if row.snode:
        value = f"{value} {row.snode}"
    return clean(value)


def age_stage_from_event(event_name: str) -> str:
    match = re.search(r"\bU\s*(8|10|12|14|16|18)\b", event_name, re.IGNORECASE)
    return f"U{match.group(1)}" if match else ""


def merge_pipe_values(existing: str, values: list[str]) -> str:
    return "|".join(ordered_unique([*split_pipe(existing), *values]))


def split_pipe(value: str) -> list[str]:
    return [clean(part) for part in clean(value).split("|") if clean(part)]


def append_note(existing: str, note: str) -> str:
    parts = [part.strip() for part in re.split(r";\s*", clean(existing)) if part.strip()]
    if note and note not in parts:
        parts.append(note)
    return "; ".join(parts)


def dedupe_rows(rows: list[StartingRankRow]) -> list[StartingRankRow]:
    seen: set[tuple[str, str, str, str, str]] = set()
    result: list[StartingRankRow] = []
    for row in rows:
        key = (row.tournament_id, row.snode, row.fide_id, row.name.casefold(), row.chinese_name)
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = clean(value)
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


def digits_only(value: str) -> str:
    match = re.search(r"\d+", clean(value))
    return match.group(0) if match else ""


def clean_name_cell(value: str) -> str:
    return clean(value).strip(" ,，")


def has_cjk(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip()


if __name__ == "__main__":
    raise SystemExit(main())
