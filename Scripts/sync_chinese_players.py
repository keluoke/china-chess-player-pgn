#!/usr/bin/env python3
"""Build the static Chinese player registry from FIDE rating-list exports.

The registry is the identity layer for every frontend product:

docs/data/registry/manifest.json
docs/data/registry/players.json
docs/data/registry/shards/fide-prefix-<prefix>.json

FIDE ID is the unique key. Chinese names and pinyin are enrichment data layered
from reviewed CSV files, not identity keys.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import os
import pathlib
import re
import shutil
import ssl
import sys
import tempfile
import time
import urllib.request
import zipfile
from dataclasses import dataclass, field
from typing import Any
from xml.etree import ElementTree

from source_http import record_provider_result, reserve_provider_request
from source_policy import require_local_collector
from stable_json import write_json as write_stable_json


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
REGISTRY_ROOT = REPO_ROOT / "docs" / "data" / "registry"
SHARD_ROOT = REGISTRY_ROOT / "shards"
MANUAL_ALIAS_CSV = REPO_ROOT / "data" / "manual" / "player-aliases.csv"
FEDERATION_OVERRIDES_CSV = REPO_ROOT / "data" / "community" / "federation-overrides.csv"
NAME_CORRECTIONS_CSV = REPO_ROOT / "data" / "community" / "name-corrections.csv"
SNAPSHOT_DIR = REPO_ROOT / "data" / "generated" / "federation-snapshots"
TRANSFER_CANDIDATES_JSON = REPO_ROOT / "data" / "generated" / "transfer-candidates.json"
DEFAULT_CACHE = pathlib.Path.home() / "Library" / "Caches" / "ChinaChessPlayerPGN" / "fide"
DEFAULT_FIDE_XML_LEGACY_URL = "https://ratings.fide.com/download/players_list_xml_legacy.zip"
USER_AGENT = "ChinaChessPlayerPGNPlayerRegistry/1.0"
_TLS_CONTEXT: ssl.SSLContext | None = None

# FIDE IDs that must be kept in the registry even though their CURRENT FIDE
# federation is no longer the target one (players who transferred out of CHN).
# Populated from data/community/federation-overrides.csv before parsing.
EXTRA_FIDE_IDS: set[str] = set()


@dataclass
class RegistryPlayer:
    fide_id: str
    name: str
    federation: str
    sex: str = ""
    title: str = ""
    women_title: str = ""
    foa_title: str = ""
    birth_year: int | None = None
    standard: int | None = None
    rapid: int | None = None
    blitz: int | None = None
    standard_games: int | None = None
    rapid_games: int | None = None
    blitz_games: int | None = None
    inactive: bool = False
    chinese_name: str = ""
    pinyin: str = ""
    aliases: list[str] = field(default_factory=list)
    former_federation: str = ""
    transfer: dict[str, Any] | None = None

    @property
    def display_name(self) -> str:
        return self.chinese_name or self.name or f"FIDE {self.fide_id}"

    @property
    def shard_prefix(self) -> str:
        return self.fide_id[:3] if len(self.fide_id) >= 3 else "other"

    def compact_payload(self) -> dict[str, Any]:
        payload = {
            "fideID": self.fide_id,
            "displayName": self.display_name,
            "name": self.name,
            "chineseName": self.chinese_name,
            "pinyin": self.pinyin,
            "federation": self.federation,
            "sex": self.sex,
            "title": self.title,
            "womenTitle": self.women_title,
            "birthYear": self.birth_year,
            "standard": self.standard,
            "rapid": self.rapid,
            "blitz": self.blitz,
            "inactive": self.inactive,
            "formerFederation": self.former_federation,
            "transfer": self.transfer,
            "aliases": ordered_unique(self.aliases_for_search()),
            "registryShard": f"data/registry/shards/fide-prefix-{self.shard_prefix}.json",
        }
        return without_empty(payload)

    def full_payload(self) -> dict[str, Any]:
        payload = self.compact_payload()
        payload.update(
            without_empty(
                {
                    "foaTitle": self.foa_title,
                    "standardGames": self.standard_games,
                    "rapidGames": self.rapid_games,
                    "blitzGames": self.blitz_games,
                }
            )
        )
        return payload

    def aliases_for_search(self) -> list[str]:
        values = [
            self.fide_id,
            self.name,
            self.name.replace(",", ""),
            english_reversed(self.name),
            self.chinese_name,
            self.pinyin,
            self.pinyin.replace(" ", ""),
            *self.aliases,
        ]
        return [value for value in values if value]


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync the static Chinese FIDE player registry.")
    parser.add_argument("--url", default=DEFAULT_FIDE_XML_LEGACY_URL, help="FIDE export URL")
    parser.add_argument("--input", type=pathlib.Path, help="local .zip/.xml/.txt/.json export")
    parser.add_argument("--cache-dir", type=pathlib.Path, default=DEFAULT_CACHE)
    parser.add_argument("--federation", default="CHN")
    parser.add_argument("--manual-aliases", type=pathlib.Path, default=MANUAL_ALIAS_CSV)
    parser.add_argument("--output-root", type=pathlib.Path, default=REGISTRY_ROOT)
    parser.add_argument(
        "--previous-registry",
        type=pathlib.Path,
        default=REGISTRY_ROOT,
        help="previous public registry used only for population-regression checks",
    )
    parser.add_argument("--snapshot-dir", type=pathlib.Path, default=SNAPSHOT_DIR)
    parser.add_argument("--transfer-candidates", type=pathlib.Path, default=TRANSFER_CANDIDATES_JSON)
    parser.add_argument("--max-players", type=int, default=0, help="test limit after federation filtering")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    federation = args.federation.upper()
    overrides = load_federation_overrides(FEDERATION_OVERRIDES_CSV, federation)
    EXTRA_FIDE_IDS.update(
        fide_id for fide_id, o in overrides.items() if o.get("type") == "transferred_out"
    )

    if args.input:
        source_path = args.input
    else:
        source_path = download_to_cache(
            args.url,
            args.cache_dir,
            validator=lambda path: validate_registry_population(
                read_players(path, federation), args.previous_registry
            ),
        )
    aliases = load_manual_aliases(args.manual_aliases)
    corrections = load_name_corrections(NAME_CORRECTIONS_CSV)
    players = read_players(source_path, federation)
    validate_registry_population(players, args.previous_registry)
    if args.max_players:
        if not args.dry_run:
            raise SystemExit("VALIDATION_REGRESSION: --max-players 只允许与 --dry-run 一起用于诊断")
        players = players[: args.max_players]
    apply_aliases(players, aliases)
    apply_name_corrections(players, corrections)
    annotate_transfers(players, overrides, federation)
    transfer_report = update_federation_snapshot(
        players,
        overrides,
        federation,
        args.dry_run,
        snapshot_dir=args.snapshot_dir,
        candidates_path=args.transfer_candidates,
    )
    write_registry(players, args.output_root, source_path, args.url, federation, args.dry_run)

    stats = {
        "players": len(players),
        "withChineseName": sum(1 for player in players if player.chinese_name),
        "ratedStandard": sum(1 for player in players if player.standard is not None),
        "ratedRapid": sum(1 for player in players if player.rapid is not None),
        "ratedBlitz": sum(1 for player in players if player.blitz is not None),
        "inactive": sum(1 for player in players if player.inactive),
        "transferredOut": sum(1 for player in players if (player.transfer or {}).get("type") == "transferred_out"),
        "transferredIn": sum(1 for player in players if (player.transfer or {}).get("type") == "transferred_in"),
        "transferCandidates": transfer_report,
        "source": str(source_path),
    }
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def load_federation_overrides(path: pathlib.Path, federation: str) -> dict[str, dict[str, str]]:
    """Read data/community/federation-overrides.csv.

    Columns: fide_id,type,former_federation,current_federation,effective,evidence_url,notes
      type=transferred_out  player left the target federation but stays in the
                            registry (marked, filterable in the frontend)
      type=transferred_in   player joined the target federation from another
                            one; marked with formerFederation
    """
    overrides: dict[str, dict[str, str]] = {}
    if not path.exists():
        return overrides
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            fide_id = clean_text(row.get("fide_id"))
            kind = clean_text(row.get("type")).lower()
            if not fide_id or kind not in {"transferred_out", "transferred_in"}:
                continue
            overrides[fide_id] = {
                "type": kind,
                "former_federation": clean_text(row.get("former_federation")).upper() or (federation if kind == "transferred_out" else ""),
                "current_federation": clean_text(row.get("current_federation")).upper(),
                "effective": clean_text(row.get("effective")),
                "evidence_url": clean_text(row.get("evidence_url")),
                "notes": clean_text(row.get("notes")),
            }
    return overrides


def annotate_transfers(players: list[RegistryPlayer], overrides: dict[str, dict[str, str]], federation: str) -> None:
    for player in players:
        override = overrides.get(player.fide_id)
        if not override:
            continue
        transfer = without_empty(
            {
                "type": override["type"],
                "effective": override["effective"],
                "evidence": override["evidence_url"],
                "notes": override["notes"],
            }
        )
        if override["type"] == "transferred_out":
            player.former_federation = override["former_federation"] or federation
            player.transfer = transfer
        elif override["type"] == "transferred_in" and player.federation == federation:
            player.former_federation = override["former_federation"]
            player.transfer = transfer


def update_federation_snapshot(
    players: list[RegistryPlayer],
    overrides: dict[str, dict[str, str]],
    federation: str,
    dry_run: bool,
    snapshot_dir: pathlib.Path = SNAPSHOT_DIR,
    candidates_path: pathlib.Path = TRANSFER_CANDIDATES_JSON,
) -> dict[str, Any]:
    """Keep a monthly snapshot of federation membership and diff against the
    previous one so transfers surface automatically as review candidates."""
    month = dt.date.today().strftime("%Y-%m")
    current_ids = sorted(
        (p.fide_id for p in players if p.federation == federation),
        key=numeric_sort_key,
    )

    previous_ids: set[str] = set()
    previous_month = ""
    if snapshot_dir.exists():
        snaps = sorted(p for p in snapshot_dir.glob("*.json") if p.stem != month)
        if snaps:
            data = json.loads(snaps[-1].read_text(encoding="utf-8"))
            previous_month = data.get("month", snaps[-1].stem)
            previous_ids = set(data.get("ids", []))

    report: dict[str, Any] = {"departed": 0, "appeared": 0, "comparedTo": previous_month}
    if previous_ids:
        known = set(overrides)
        departed = sorted(previous_ids - set(current_ids) - known, key=numeric_sort_key)
        appeared = sorted(set(current_ids) - previous_ids, key=numeric_sort_key)
        report.update({"departed": len(departed), "appeared": len(appeared)})
        if not dry_run and (departed or appeared):
            candidates_path.parent.mkdir(parents=True, exist_ok=True)
            candidates_path.write_text(
                json.dumps(
                    {
                        "generatedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
                        "comparedTo": previous_month,
                        "note": "departed = was in the federation last snapshot, gone now, and not covered by federation-overrides.csv — review and add overrides; appeared = new federation members (possible transfers in or new registrations).",
                        "departed": departed,
                        "appeared": appeared,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

    if not dry_run:
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        (snapshot_dir / f"{month}.json").write_text(
            json.dumps({"month": month, "federation": federation, "count": len(current_ids), "ids": current_ids}, ensure_ascii=False)
            + "\n",
            encoding="utf-8",
        )
    return report


def validate_fide_archive(path: pathlib.Path) -> None:
    """Reject HTML/error bodies, truncated zips and archives without a list."""
    if path.stat().st_size < 1024 * 1024:
        raise IOError(f"FIDE 文件异常小：{path.stat().st_size} 字节")
    if path.suffix.lower() != ".zip":
        return
    if not zipfile.is_zipfile(path):
        raise IOError("FIDE 文件不是完整 ZIP（可能下载中断或返回错误页）")
    with zipfile.ZipFile(path) as archive:
        bad_member = archive.testzip()
        if bad_member:
            raise IOError(f"FIDE ZIP 成员校验失败：{bad_member}")
        candidates = [
            item for item in archive.infolist()
            if not item.is_dir() and item.filename.lower().endswith((".xml", ".txt", ".csv", ".tsv"))
        ]
        if not candidates:
            raise IOError("FIDE ZIP 中没有 XML/TXT 等等级分名单")
        if max(item.file_size for item in candidates) < 5 * 1024 * 1024:
            raise IOError("FIDE ZIP 内名单异常小，拒绝替换 last-good 缓存")


def validate_registry_population(players: list[RegistryPlayer], previous_registry: pathlib.Path) -> None:
    """Semantic guardrail after archive validation and XML parsing."""
    count = len(players)
    if count < 5000 or count > 50000:
        raise ValueError(f"FIDE CHN 棋手数异常：{count}（安全范围 5000-50000）")
    ids = [player.fide_id for player in players]
    if len(set(ids)) != len(ids):
        raise ValueError("FIDE 名单包含重复 FIDE ID")
    if sum(bool(player.name) for player in players) < int(count * 0.98):
        raise ValueError("FIDE 名单姓名缺失比例异常")
    manifest_path = previous_registry / "manifest.json"
    if manifest_path.exists():
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
            previous_count = int((previous.get("totals") or {}).get("players") or 0)
        except (OSError, ValueError, json.JSONDecodeError):
            previous_count = 0
        if previous_count and not (previous_count * 0.7 <= count <= previous_count * 1.3):
            raise ValueError(f"FIDE CHN 棋手数相对上次突变：{previous_count} -> {count}")


def _cache_candidates(target: pathlib.Path) -> list[pathlib.Path]:
    versions = target.parent / "versions"
    candidates = [target] if target.exists() else []
    if versions.exists():
        candidates.extend(sorted(versions.glob(f"*{target.suffix}"), reverse=True))
    return candidates


def file_sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_cached_copy(
    target: pathlib.Path,
    validator: Any | None,
) -> pathlib.Path | None:
    for candidate in _cache_candidates(target):
        try:
            validate_fide_archive(candidate)
            if validator:
                validator(candidate)
            return candidate
        except Exception as error:
            print(f"WARNING: 忽略无效 FIDE 缓存 {candidate.name}: {error}", file=sys.stderr)
    return None


def _promote_cache_version(
    tmp: pathlib.Path,
    target: pathlib.Path,
    last_good: pathlib.Path | None,
) -> pathlib.Path:
    versions = target.parent / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    # Preserve only the current copy that passed the semantic validator.  A
    # structurally valid but population-corrupt target must not be promoted to
    # the versioned last-good set when a replacement arrives.
    if last_good == target and target.exists():
        old_digest = file_sha256(target)[:12]
        if not any(old_digest in path.name for path in versions.glob(f"*{target.suffix}")):
            old_version = versions / f"{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-previous-{old_digest}{target.suffix}"
            shutil.copy2(target, old_version)
    digest = file_sha256(tmp)[:12]
    version = versions / f"{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{digest}{target.suffix}"
    shutil.copy2(tmp, version)
    os.replace(tmp, target)
    for old in sorted(versions.glob(f"*{target.suffix}"), reverse=True)[3:]:
        old.unlink(missing_ok=True)
    return target


def download_to_cache(
    url: str,
    cache_dir: pathlib.Path,
    validator: Any | None = None,
) -> pathlib.Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    file_name = pathlib.Path(urllib.request.urlparse(url).path).name or "fide_players.zip"
    target = cache_dir / file_name
    require_local_collector("fide")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_error: Exception | None = None
    for attempt in range(3):
        fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.download-", suffix=target.suffix, dir=cache_dir)
        os.close(fd)
        tmp = pathlib.Path(tmp_name)
        try:
            reserve_provider_request("fide")
            with urllib.request.urlopen(request, timeout=180, context=tls_context()) as response, tmp.open("wb") as handle:
                content_type = (response.headers.get_content_type() or "").lower()
                if content_type.startswith("text/html"):
                    raise IOError("FIDE 返回 HTML，可能是错误页或限流页")
                expected = response.headers.get("Content-Length")
                written = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    handle.write(chunk)
            # FIDE sometimes serves an HTML error/ratelimit page as 200, or the
            # connection drops mid-body (a zip missing its central directory).
            # Validate BEFORE replacing any previously good cached copy.
            if expected is not None and written != int(expected):
                raise IOError(f"下载不完整:收到 {written} 字节,应为 {expected} 字节")
            validate_fide_archive(tmp)
            previous = _valid_cached_copy(target, validator)
            if previous and previous.exists():
                previous_size = previous.stat().st_size
                if tmp.stat().st_size < previous_size * 0.6:
                    raise IOError(f"FIDE 文件相对 last-good 异常缩小：{previous_size} -> {tmp.stat().st_size}")
            if validator:
                validator(tmp)
            promoted = _promote_cache_version(tmp, target, previous)
            record_provider_result("fide", True)
            return promoted
        except Exception as error:
            last_error = error
            # Budget/circuit errors did not perform a request and must not
            # extend the breaker; all other failures count toward it.
            if getattr(error, "code", "") not in {"VISIT_BUDGET_EXHAUSTED", "SOURCE_CIRCUIT_OPEN"}:
                record_provider_result("fide", False)
            tmp.unlink(missing_ok=True)
            if attempt < 2:
                print(f"下载失败(第 {attempt + 1} 次):{error},稍候重试…")
                time.sleep(1.5 * (attempt + 1))

    assert last_error is not None
    fallback = _valid_cached_copy(target, validator)
    if fallback:
        age_hours = (time.time() - fallback.stat().st_mtime) / 3600
        print(f"WARNING: 本次下载失败({last_error});改用 {age_hours:.0f} 小时前 last-good FIDE 名单:{fallback}")
        return fallback
    raise last_error


def read_players(path: pathlib.Path, federation: str) -> list[RegistryPlayer]:
    if path.suffix.lower() == ".zip":
        return read_players_from_zip(path, federation)
    if path.suffix.lower() == ".xml":
        return read_players_from_xml(path, federation)
    if path.suffix.lower() in {".txt", ".csv", ".tsv"}:
        return read_players_from_text(path.read_text(encoding="utf-8", errors="replace"), federation)
    if path.suffix.lower() == ".json":
        return read_players_from_json(path, federation)
    raise ValueError(f"Unsupported input format: {path}")


def read_players_from_zip(path: pathlib.Path, federation: str) -> list[RegistryPlayer]:
    with zipfile.ZipFile(path) as archive:
        names = sorted(archive.namelist())
        xml_name = next((name for name in names if name.lower().endswith(".xml")), None)
        if xml_name:
            with archive.open(xml_name) as handle:
                return read_players_from_xml(handle, federation)
        text_name = next((name for name in names if name.lower().endswith((".txt", ".csv", ".tsv"))), None)
        if text_name:
            with archive.open(text_name) as handle:
                return read_players_from_text(handle.read().decode("utf-8", errors="replace"), federation)
    raise ValueError(f"ZIP has no XML/TXT rating list: {path}")


def read_players_from_xml(source: pathlib.Path | io.BufferedIOBase, federation: str) -> list[RegistryPlayer]:
    players: list[RegistryPlayer] = []
    for _, element in ElementTree.iterparse(source, events=("end",)):
        if strip_namespace(element.tag).lower() != "player":
            continue
        fields = {normalize_key(strip_namespace(child.tag)): clean_text(child.text) for child in element}
        if pick(fields, "country", "fed", "federation") != federation and (
            pick(fields, "fideid", "fide_id", "idnumber", "id_number", "id") not in EXTRA_FIDE_IDS
        ):
            element.clear()
            continue
        player = player_from_fields(fields, federation)
        if player:
            players.append(player)
        element.clear()
    return sorted(players, key=lambda player: numeric_sort_key(player.fide_id))


def read_players_from_text(text: str, federation: str) -> list[RegistryPlayer]:
    stripped = text.lstrip("\ufeff\n\r ")
    if not stripped:
        return []

    sample = stripped[:4096]
    for delimiter in ("\t", ";", ","):
        if delimiter in sample.splitlines()[0]:
            rows = csv.DictReader(io.StringIO(stripped), delimiter=delimiter)
            players = [
                player_from_fields({normalize_key(key): clean_text(value) for key, value in row.items()}, federation)
                for row in rows
            ]
            return sorted([player for player in players if player and (player.federation == federation or player.fide_id in EXTRA_FIDE_IDS)], key=lambda player: numeric_sort_key(player.fide_id))

    players: list[RegistryPlayer] = []
    for line in stripped.splitlines():
        player = player_from_fixed_width_line(line, federation)
        if player:
            players.append(player)
    return sorted(players, key=lambda player: numeric_sort_key(player.fide_id))


def read_players_from_json(path: pathlib.Path, federation: str) -> list[RegistryPlayer]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("players", data if isinstance(data, list) else [])
    players = [
        player_from_fields({normalize_key(key): clean_text(value) for key, value in row.items()}, federation)
        for row in rows
    ]
    return sorted([player for player in players if player and (player.federation == federation or player.fide_id in EXTRA_FIDE_IDS)], key=lambda player: numeric_sort_key(player.fide_id))


def player_from_fixed_width_line(line: str, federation: str) -> RegistryPlayer | None:
    if not re.match(r"^\s*\d{3,}", line):
        return None
    tokens = line.split()
    if len(tokens) < 4 or federation not in tokens:
        return None
    fed_index = tokens.index(federation)
    fide_id = tokens[0]
    name = " ".join(tokens[1:fed_index]).strip()
    sex = tokens[fed_index + 1] if len(tokens) > fed_index + 1 else ""
    integers = [int(token) for token in tokens[fed_index + 2 :] if token.isdigit()]
    birth_year = next((value for value in reversed(integers) if 1900 <= value <= dt.datetime.now().year), None)
    ratings = [value for value in integers if 100 <= value <= 3500 and value != birth_year]
    return RegistryPlayer(
        fide_id=fide_id,
        name=name,
        federation=federation,
        sex=sex,
        standard=ratings[0] if ratings else None,
        rapid=ratings[1] if len(ratings) > 1 else None,
        blitz=ratings[2] if len(ratings) > 2 else None,
        birth_year=birth_year,
        inactive="i" in [token.lower() for token in tokens],
    )


def player_from_fields(fields: dict[str, str], federation: str) -> RegistryPlayer | None:
    fide_id = pick(fields, "fideid", "fide_id", "idnumber", "id_number", "id")
    name = pick(fields, "name", "fullname", "full_name")
    fed = pick(fields, "country", "fed", "federation") or federation
    if not fide_id or not name:
        return None
    if fed != federation and fide_id not in EXTRA_FIDE_IDS:
        return None

    return RegistryPlayer(
        fide_id=fide_id,
        name=name,
        federation=fed,
        sex=pick(fields, "sex", "gender"),
        title=pick(fields, "title", "tit"),
        women_title=pick(fields, "wtitle", "w_title", "wtit", "women_title"),
        foa_title=pick(fields, "foatitle", "foa_title"),
        birth_year=parse_year(pick(fields, "birthday", "birthyear", "birth_year", "year")),
        standard=parse_int(pick(fields, "rating", "standard", "standardrating", "standard_rating")),
        rapid=parse_int(pick(fields, "rapidrating", "rapid_rating", "rapid")),
        blitz=parse_int(pick(fields, "blitzrating", "blitz_rating", "blitz")),
        standard_games=parse_int(pick(fields, "games", "standardgames", "standard_games")),
        rapid_games=parse_int(pick(fields, "rapidgames", "rapid_games")),
        blitz_games=parse_int(pick(fields, "blitzgames", "blitz_games")),
        inactive=parse_inactive(pick(fields, "flag", "flags", "inactive")),
    )


def load_manual_aliases(path: pathlib.Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    aliases: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            fide_id = clean_text(row.get("fide_id"))
            if not fide_id:
                continue
            aliases[fide_id] = {
                "chineseName": clean_text(row.get("chinese_name")),
                "pinyin": clean_text(row.get("pinyin_name")),
                "aliases": split_aliases(row.get("aliases")),
            }
    return aliases


def load_name_corrections(path: pathlib.Path) -> dict[str, dict[str, str]]:
    corrections: dict[str, dict[str, str]] = {}
    if not path.exists():
        return corrections
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            fide_id = clean_text(row.get("fide_id"))
            correct = clean_text(row.get("correct_chinese_name"))
            if fide_id and correct:
                corrections[fide_id] = {
                    "wrong": clean_text(row.get("wrong_chinese_name")),
                    "correct": correct,
                }
    return corrections


def apply_aliases(players: list[RegistryPlayer], aliases: dict[str, dict[str, Any]]) -> None:
    for player in players:
        entry = aliases.get(player.fide_id)
        if not entry:
            continue
        player.chinese_name = entry.get("chineseName") or player.chinese_name
        player.pinyin = entry.get("pinyin") or player.pinyin
        player.aliases = ordered_unique([*player.aliases, *entry.get("aliases", [])])


def apply_name_corrections(
    players: list[RegistryPlayer], corrections: dict[str, dict[str, str]]
) -> None:
    """Force corrections last so no scraped/manual alias can revive a bad identity."""
    by_id = {player.fide_id: player for player in players}
    for fide_id, correction in corrections.items():
        player = by_id.get(fide_id)
        if not player:
            continue
        wrong = correction.get("wrong", "")
        correct = correction["correct"]
        player.chinese_name = correct
        player.aliases = ordered_unique(
            [value for value in player.aliases if value and value != wrong] + [correct]
        )


def write_registry(
    players: list[RegistryPlayer],
    output_root: pathlib.Path,
    source_path: pathlib.Path,
    source_url: str,
    federation: str,
    dry_run: bool,
) -> None:
    generated_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    manifest = {
        "schemaVersion": 1,
        "generatedAt": generated_at,
        "federation": federation,
        "source": {
            "name": "FIDE Rating List legacy XML",
            "url": source_url,
            "inputFile": source_path.name,
            "inputBytes": source_path.stat().st_size if source_path.exists() else None,
            "downloadPage": "https://ratings.fide.com/download_lists.phtml",
        },
        "storage": {
            "playerList": "data/registry/players.json",
            "shardPattern": "data/registry/shards/fide-prefix-<first3>.json",
        },
        "totals": {
            "players": len(players),
            "withChineseName": sum(1 for player in players if player.chinese_name),
            "ratedStandard": sum(1 for player in players if player.standard is not None),
            "ratedRapid": sum(1 for player in players if player.rapid is not None),
            "ratedBlitz": sum(1 for player in players if player.blitz is not None),
            "inactive": sum(1 for player in players if player.inactive),
        },
    }

    compact_players = [player.compact_payload() for player in players]
    shards: dict[str, list[dict[str, Any]]] = {}
    for player in players:
        shards.setdefault(player.shard_prefix, []).append(player.full_payload())

    if dry_run:
        return

    output_root.mkdir(parents=True, exist_ok=True)
    shard_root = output_root / "shards"
    shard_root.mkdir(parents=True, exist_ok=True)
    for old_file in shard_root.glob("fide-prefix-*.json"):
        old_file.unlink()

    write_json(output_root / "manifest.json", manifest)
    write_json(output_root / "players.json", compact_players)
    for prefix, shard_players in sorted(shards.items()):
        write_json(shard_root / f"fide-prefix-{prefix}.json", shard_players)


def pick(fields: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = fields.get(normalize_key(key), "")
        if value:
            return value
    return ""


def parse_year(value: str) -> int | None:
    if not value:
        return None
    match = re.search(r"(19|20)\d{2}", value)
    if not match:
        return None
    year = int(match.group(0))
    return year if 1900 <= year <= dt.datetime.now().year else None


def parse_int(value: str) -> int | None:
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def parse_inactive(value: str) -> bool:
    normalized = value.strip().lower()
    return normalized in {"i", "inactive", "true", "1"}


def split_aliases(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in re.split(r"[|;]", value) if item.strip()]


def english_reversed(name: str) -> str:
    pieces = [piece.strip() for piece in name.replace(",", " ").split() if piece.strip()]
    if len(pieces) < 2:
        return ""
    return " ".join([*pieces[1:], pieces[0]])


def normalize_key(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def normalized_alias(value: str) -> str:
    return re.sub(r"[\s,.'·，。_\-]+", "", str(value or "").casefold()).strip()


def alias_type(alias: str, player: RegistryPlayer) -> str:
    if alias == player.fide_id:
        return "fide"
    if alias in {player.chinese_name, player.pinyin}:
        return "manual"
    return "registry"


def strip_namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = clean_text(value)
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


def without_empty(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value not in (None, "", [], {})}


def numeric_sort_key(value: str) -> tuple[int, str]:
    return (int(value), value) if value.isdigit() else (sys.maxsize, value)


def write_json(path: pathlib.Path, data: Any) -> None:
    write_stable_json(path, data, ensure_ascii=False, indent=2)


def tls_context() -> ssl.SSLContext:
    global _TLS_CONTEXT
    if _TLS_CONTEXT is not None:
        return _TLS_CONTEXT

    try:
        import certifi  # type: ignore[import-not-found]

        _TLS_CONTEXT = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        _TLS_CONTEXT = ssl.create_default_context()
    if hasattr(ssl, "OP_IGNORE_UNEXPECTED_EOF"):
        _TLS_CONTEXT.options |= ssl.OP_IGNORE_UNEXPECTED_EOF
    return _TLS_CONTEXT


if __name__ == "__main__":
    raise SystemExit(main())
