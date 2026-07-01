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
import io
import json
import pathlib
import re
import ssl
import sys
import time
import urllib.request
import zipfile
from dataclasses import dataclass, field
from typing import Any
from xml.etree import ElementTree


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
REGISTRY_ROOT = REPO_ROOT / "docs" / "data" / "registry"
SHARD_ROOT = REGISTRY_ROOT / "shards"
MANUAL_ALIAS_CSV = REPO_ROOT / "data" / "manual" / "player-aliases.csv"
DEFAULT_CACHE = pathlib.Path.home() / "Library" / "Caches" / "ChinaChessPlayerPGN" / "fide"
DEFAULT_FIDE_XML_LEGACY_URL = "https://ratings.fide.com/download/players_list_xml_legacy.zip"
USER_AGENT = "ChinaChessPlayerPGNPlayerRegistry/1.0"
_TLS_CONTEXT: ssl.SSLContext | None = None


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
    parser.add_argument("--max-players", type=int, default=0, help="test limit after federation filtering")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    source_path = args.input or download_to_cache(args.url, args.cache_dir)
    aliases = load_manual_aliases(args.manual_aliases)
    players = read_players(source_path, args.federation.upper())
    if args.max_players:
        players = players[: args.max_players]
    apply_aliases(players, aliases)
    write_registry(players, args.output_root, source_path, args.url, args.federation.upper(), args.dry_run)

    stats = {
        "players": len(players),
        "withChineseName": sum(1 for player in players if player.chinese_name),
        "ratedStandard": sum(1 for player in players if player.standard is not None),
        "ratedRapid": sum(1 for player in players if player.rapid is not None),
        "ratedBlitz": sum(1 for player in players if player.blitz is not None),
        "inactive": sum(1 for player in players if player.inactive),
        "source": str(source_path),
    }
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def download_to_cache(url: str, cache_dir: pathlib.Path) -> pathlib.Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    file_name = pathlib.Path(urllib.request.urlparse(url).path).name or "fide_players.zip"
    target = cache_dir / file_name
    tmp = target.with_suffix(target.suffix + ".tmp")

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=180, context=tls_context()) as response, tmp.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
            tmp.replace(target)
            return target
        except Exception as error:
            last_error = error
            if tmp.exists():
                tmp.unlink()
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))

    assert last_error is not None
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
        if pick(fields, "country", "fed", "federation") != federation:
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
            return sorted([player for player in players if player and player.federation == federation], key=lambda player: numeric_sort_key(player.fide_id))

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
    return sorted([player for player in players if player and player.federation == federation], key=lambda player: numeric_sort_key(player.fide_id))


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
    if not fide_id or not name or fed != federation:
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


def apply_aliases(players: list[RegistryPlayer], aliases: dict[str, dict[str, Any]]) -> None:
    for player in players:
        entry = aliases.get(player.fide_id)
        if not entry:
            continue
        player.chinese_name = entry.get("chineseName") or player.chinese_name
        player.pinyin = entry.get("pinyin") or player.pinyin
        player.aliases = ordered_unique([*player.aliases, *entry.get("aliases", [])])


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
            "cachedInput": str(source_path),
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
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


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
