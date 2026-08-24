#!/usr/bin/env python3
"""Build canonical player-event and player-game facts without derived feedback.

Only registry identities, structured event details, canonical PGN assets and
their verification receipts are inputs.  In particular this builder never
reads ``docs/data/index/players`` or ``docs/data/index/by-player``; cold and
warm builds therefore have the same authority graph.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import pathlib
import re
from collections import defaultdict
from typing import Any, Iterable

import build_static_player_pgn as pgn
from snapshot_context import snapshot_id
from stable_json import write_json


ROOT = pathlib.Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "docs/data/registry/players.json"
EVENT_DETAILS = ROOT / "data/generated/chess-results-event-details"
EVENT_PGN = ROOT / "data/generated/chess-results-event-pgn"
EVENT_PGN_RECEIPT = ROOT / "data/generated/r2-object-receipts/events--chess-results.json"
STATIC_PGN_ROOT = ROOT / "docs/data/pgn"
LICHESS_EVENT_ROOT = ROOT / "docs/data/bulk/lichess-events"
LICHESS_EVENT_MANIFEST = LICHESS_EVENT_ROOT / "manifest.json"
BULK_YOUTH_MANIFEST = ROOT / "docs/data/bulk/youth/manifest.json"
MAPPINGS = ROOT / "data/community/tournament-name-mappings.csv"
EVENT_FACT_ROOT = ROOT / "data/generated/player-event-facts"
GAME_FACT_ROOT = ROOT / "data/generated/player-game-facts"


def clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def read_json(path: pathlib.Path, default: Any = None, *, required: bool = False) -> Any:
    if not path.is_file():
        if required:
            raise RuntimeError(f"CANONICAL_FACT_INPUT_MISSING: {path}")
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"CANONICAL_FACT_INPUT_INVALID: {path}: {error}") from error


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def repo_path(path: pathlib.Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def resolve_repo_path(value: str) -> pathlib.Path:
    path = pathlib.Path(value)
    return path if path.is_absolute() else ROOT / path


def public_data_path(path: pathlib.Path) -> str:
    try:
        relative = path.relative_to(ROOT / "docs")
    except ValueError:
        return ""
    return relative.as_posix()


def registry_rows() -> list[dict[str, Any]]:
    payload = read_json(REGISTRY, required=True)
    rows = payload.get("players") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise RuntimeError(f"CANONICAL_FACT_REGISTRY_INVALID: {REGISTRY}")
    return [row for row in rows if isinstance(row, dict)]


def registry_indexes() -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    registry: dict[str, dict[str, Any]] = {}
    names: dict[str, set[str]] = defaultdict(set)
    for row in registry_rows():
        fide_id = clean(row.get("fideID"))
        if not fide_id.isdigit():
            continue
        registry[fide_id] = row
        for value in (
            row.get("displayName"), row.get("name"), row.get("chineseName"),
            row.get("pinyin"), *(row.get("aliases") or []),
        ):
            key = pgn.normalize_key(value)
            if key:
                names[key].add(fide_id)
    return registry, {
        key: next(iter(values)) for key, values in names.items() if len(values) == 1
    }


def explicit_fide_id(person: dict[str, Any], registry: dict[str, Any]) -> str:
    for key in ("fideID", "fideId", "fide_id", "FIDEID"):
        value = clean(person.get(key))
        if value in registry:
            return value
    return ""


def resolve_person(
    person: dict[str, Any],
    registry: dict[str, Any],
    names: dict[str, str],
) -> tuple[str, str]:
    fide_id = explicit_fide_id(person, registry)
    if fide_id:
        return fide_id, "explicit-fide-id"
    for value in (person.get("name"), person.get("chineseName"), person.get("displayName")):
        fide_id = names.get(pgn.normalize_key(value), "")
        if fide_id:
            return fide_id, "unique-registry-name"
    return "", "unresolved"


def mapping_index() -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    if not MAPPINGS.is_file():
        return result
    with MAPPINGS.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            tid = clean(row.get("tournament_id"))
            if tid:
                result[tid] = {
                    "canonicalEventID": clean(row.get("canonical_event_id")),
                    "chineseName": clean(row.get("chinese_name")),
                }
    return result


def detail_contexts(
    registry: dict[str, dict[str, Any]],
    names: dict[str, str],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    mappings = mapping_index()
    contexts: dict[str, dict[str, Any]] = {}
    facts: dict[tuple[str, str], dict[str, Any]] = {}
    for path in sorted(EVENT_DETAILS.glob("tnr*.json")):
        payload = read_json(path, required=True)
        tid = clean(payload.get("tournamentID"))
        if not tid:
            continue
        roster = {
            clean(row.get("playerNo")): row
            for row in payload.get("players") or []
            if clean(row.get("playerNo"))
        }
        standings = {
            clean(row.get("playerNo")): row
            for row in payload.get("standings") or []
            if clean(row.get("playerNo"))
        }
        by_pairing_key: dict[tuple[str, str], dict[str, Any]] = {}
        for round_row in payload.get("rounds") or []:
            round_id = pgn.clean(round_row.get("round"))
            round_id = re.match(r"(\d+)", round_id).group(1) if re.match(r"(\d+)", round_id) else round_id
            for pairing in round_row.get("pairings") or []:
                board = clean(pairing.get("board"))
                if board:
                    by_pairing_key[(round_id, board)] = pairing
        contexts[tid] = {
            "payload": payload,
            "roster": roster,
            "standings": standings,
            "pairings": by_pairing_key,
        }
        event_name = clean(payload.get("sourceName") or payload.get("displayName"))
        mapping = mappings.get(tid, {})
        participant_count = len(roster)
        for player_no in sorted(set(roster) | set(standings), key=lambda value: (len(value), value)):
            merged = {**standings.get(player_no, {}), **roster.get(player_no, {})}
            # Rank/score are result facts and therefore come from standings.
            merged.update({
                key: value for key, value in standings.get(player_no, {}).items()
                if value not in (None, "")
            })
            fide_id, basis = resolve_person(merged, registry, names)
            if not fide_id:
                continue
            fact = {
                "id": f"fide:{fide_id}|event:{tid}",
                "fideID": fide_id,
                "eventID": f"event:{tid}",
                "tournamentID": tid,
                "event": mapping.get("chineseName") or event_name or f"赛事 {tid}",
                "sourceEventName": event_name or None,
                "canonicalEventID": mapping.get("canonicalEventID") or None,
                "date": clean(payload.get("dateEnd") or payload.get("dateBegin")) or None,
                "playerNo": player_no,
                "rank": clean(standings.get(player_no, {}).get("rank")) or None,
                "score": clean(standings.get(player_no, {}).get("score")) or None,
                "rounds": payload.get("roundCount") or None,
                "participants": participant_count or None,
                "identityBasis": basis,
            }
            facts[(fide_id, tid)] = {key: value for key, value in fact.items() if value is not None}
    return contexts, sorted(
        facts.values(), key=lambda row: (row["fideID"], row.get("date", ""), row["tournamentID"])
    )


def header_fide_id(headers: dict[str, str], side: str, registry: dict[str, Any]) -> str:
    for suffix in ("FideId", "FideID", "FIDEID"):
        value = clean(headers.get(f"{side}{suffix}"))
        if value in registry:
            return value
    return ""


def pairing_fide_ids(
    pairing: dict[str, Any] | None,
    registry: dict[str, Any],
    names: dict[str, str],
) -> tuple[str, str]:
    pairing = pairing or {}
    result = []
    for side in ("white", "black"):
        fide_id, _basis = resolve_person(pairing.get(side) or {}, registry, names)
        result.append(fide_id)
    return result[0], result[1]


def hinted_role(
    fide_id: str,
    headers: dict[str, str],
    registry: dict[str, dict[str, Any]],
) -> str:
    row = registry.get(fide_id) or {}
    identities = {
        pgn.normalize_key(value)
        for value in (
            row.get("displayName"), row.get("name"), row.get("chineseName"),
            row.get("pinyin"), *(row.get("aliases") or []),
        )
        if pgn.normalize_key(value)
    }
    if pgn.normalize_key(headers.get("White")) in identities:
        return "white"
    if pgn.normalize_key(headers.get("Black")) in identities:
        return "black"
    return ""


def source_priority(source: str) -> int:
    if source == "verified-event-archive":
        return 40
    if source == "verified-lichess-event":
        return 30
    if source == "canonical-static-pgn":
        return 20
    return 10


def add_game(
    games: dict[str, dict[str, Any]],
    *,
    game: str,
    asset_path: pathlib.Path,
    game_index: int,
    source_kind: str,
    source_label: str,
    registry: dict[str, dict[str, Any]],
    names: dict[str, str],
    contexts: dict[str, dict[str, Any]],
    tournament_id: str = "",
    player_hint: str = "",
    stage: str = "",
    public_pgn_path: str = "",
    source_index_path: str = "",
    source_shard: str = "",
    verified_by: str = "",
) -> None:
    repaired = pgn.repair_pgn_text(game)
    headers = pgn.pgn_headers(repaired)
    tid = clean(tournament_id)
    if not tid:
        match = re.search(r"(?:^|/)tnr(\d+)(?:/|\.pgn|$)", asset_path.as_posix())
        tid = match.group(1) if match else ""
    round_id = clean(headers.get("Round"))
    match = re.match(r"(\d+)", round_id)
    round_id = match.group(1) if match else round_id
    board = clean(headers.get("Board"))
    context = contexts.get(tid, {})
    pairing = (context.get("pairings") or {}).get((round_id, board)) if board else None
    white_id = header_fide_id(headers, "White", registry)
    black_id = header_fide_id(headers, "Black", registry)
    pairing_white, pairing_black = pairing_fide_ids(pairing, registry, names)
    white_id = white_id or pairing_white or names.get(pgn.normalize_key(headers.get("White")), "")
    black_id = black_id or pairing_black or names.get(pgn.normalize_key(headers.get("Black")), "")
    hinted = clean(player_hint)
    if hinted in registry and hinted not in {white_id, black_id}:
        role = hinted_role(hinted, headers, registry)
        if role == "white" and not white_id:
            white_id = hinted
        elif role == "black" and not black_id:
            black_id = hinted
        else:
            raise RuntimeError(
                "CANONICAL_STATIC_PGN_PLAYER_HINT_MISMATCH: "
                f"{repo_path(asset_path)} game {game_index}: hinted {hinted} "
                f"does not match resolved players {white_id or '-'} / {black_id or '-'}"
            )
    player_ids = sorted({value for value in (white_id, black_id) if value in registry})
    # Verified event-level archives also serve public event playback for two
    # unlinked/no-FIDE players.  Keep those canonical game facts explicitly
    # unlinked; downstream by-player/API counts only registry links.
    if not player_ids and source_kind not in {"verified-event-archive", "verified-lichess-event"}:
        return
    fingerprint = pgn.game_fingerprint(repaired)
    payload = context.get("payload") or {}
    date = pgn.normalize_pgn_date(
        headers.get("EventDate") or headers.get("Date")
        or payload.get("dateEnd") or payload.get("dateBegin")
    )
    fact = {
        "id": fingerprint.removeprefix("fp:"),
        "fingerprint": fingerprint,
        "tournamentID": tid or None,
        "event": clean(headers.get("BroadcastName") or headers.get("Event")) or f"赛事 {tid}",
        "date": date or None,
        "round": round_id or None,
        "board": board or None,
        "white": clean(headers.get("White")) or None,
        "black": clean(headers.get("Black")) or None,
        "whiteFideID": white_id or None,
        "blackFideID": black_id or None,
        "playerFideIDs": player_ids,
        "linkStatus": "registry-linked" if player_ids else "unlinked",
        "result": clean(headers.get("Result")) or None,
        "stage": stage or None,
        "source": source_label,
        "sourceKind": source_kind,
        "assetPath": repo_path(asset_path),
        "gameIndex": game_index,
        "gameSha256": pgn.stable_game_hash(repaired),
        "publicPgnPath": public_pgn_path or public_data_path(asset_path) or None,
        "sourceIndexPath": source_index_path or None,
        "sourceShard": source_shard or None,
        "verifiedBy": verified_by or None,
    }
    fact = {key: value for key, value in fact.items() if value not in (None, "", [], {})}
    previous = games.get(fingerprint)
    if previous is None:
        games[fingerprint] = fact
        return
    merged_ids = sorted(set(previous.get("playerFideIDs") or []) | set(player_ids))
    if source_priority(source_kind) > source_priority(clean(previous.get("sourceKind"))):
        fact["playerFideIDs"] = merged_ids
        games[fingerprint] = fact
    else:
        previous["playerFideIDs"] = merged_ids
        previous["whiteFideID"] = previous.get("whiteFideID") or white_id or None
        previous["blackFideID"] = previous.get("blackFideID") or black_id or None


def split_asset(path: pathlib.Path) -> list[str]:
    return pgn.split_pgn_games(path.read_text(encoding="utf-8", errors="replace"))


def ingest_verified_event_archives(
    games: dict[str, dict[str, Any]],
    registry: dict[str, dict[str, Any]],
    names: dict[str, str],
    contexts: dict[str, dict[str, Any]],
) -> None:
    paths = sorted(EVENT_PGN.glob("tnr*.pgn"))
    if not paths:
        return
    receipt = read_json(EVENT_PGN_RECEIPT, required=True)
    receipts = {
        clean(item.get("key")): item
        for item in receipt.get("objects") or []
        if clean(item.get("key"))
    }
    for path in paths:
        tid = path.stem.removeprefix("tnr")
        key = f"events/chess-results/{path.name}"
        item = receipts.get(key)
        actual = sha256_file(path)
        if not item or clean(item.get("sha256")) != actual:
            raise RuntimeError(
                f"CANONICAL_EVENT_PGN_RECEIPT_MISMATCH: {repo_path(path)}: "
                f"receipt={clean((item or {}).get('sha256')) or '<missing>'}, actual={actual}"
            )
        public = f"api/event-pgn?tnr={tid}&sha={actual[:16]}"
        for index, game in enumerate(split_asset(path)):
            add_game(
                games, game=game, asset_path=path, game_index=index,
                source_kind="verified-event-archive", source_label="Chess-Results",
                registry=registry, names=names, contexts=contexts,
                tournament_id=tid, public_pgn_path=public,
                verified_by=repo_path(EVENT_PGN_RECEIPT),
            )


def ingest_verified_lichess_events(
    games: dict[str, dict[str, Any]],
    registry: dict[str, dict[str, Any]],
    names: dict[str, str],
    contexts: dict[str, dict[str, Any]],
) -> None:
    pgn_root = LICHESS_EVENT_ROOT / "pgn"
    paths = sorted(pgn_root.glob("tnr*.pgn"))
    if not paths:
        return
    manifest = read_json(LICHESS_EVENT_MANIFEST, required=True)
    rows = {
        clean(item.get("tournamentID")): item
        for item in manifest.get("events") or []
        if clean(item.get("tournamentID"))
    }
    for path in paths:
        tid = path.stem.removeprefix("tnr")
        row = rows.get(tid) or {}
        actual = sha256_file(path)
        if clean(row.get("sha256")) != actual:
            raise RuntimeError(
                f"CANONICAL_LICHESS_PGN_MANIFEST_MISMATCH: {repo_path(path)}: "
                f"manifest={clean(row.get('sha256')) or '<missing>'}, actual={actual}"
            )
        for index, game in enumerate(split_asset(path)):
            add_game(
                games, game=game, asset_path=path, game_index=index,
                source_kind="verified-lichess-event", source_label="Lichess Broadcasts",
                registry=registry, names=names, contexts=contexts,
                tournament_id=tid, public_pgn_path=public_data_path(path),
                verified_by=repo_path(LICHESS_EVENT_MANIFEST),
            )


def ingest_static_event_pgns(
    games: dict[str, dict[str, Any]],
    registry: dict[str, dict[str, Any]],
    names: dict[str, str],
    contexts: dict[str, dict[str, Any]],
) -> None:
    if not STATIC_PGN_ROOT.is_dir():
        return
    for path in sorted(STATIC_PGN_ROOT.rglob("*.pgn")):
        relative = path.relative_to(STATIC_PGN_ROOT)
        if relative.parts and relative.parts[0] == "by-player":
            continue
        hint_match = re.search(r"fide-(\d+)", path.name)
        player_hint = hint_match.group(1) if hint_match else ""
        tid_match = re.search(r"tnr(\d+)", path.as_posix())
        tid = tid_match.group(1) if tid_match else ""
        for index, game in enumerate(split_asset(path)):
            add_game(
                games, game=game, asset_path=path, game_index=index,
                source_kind="canonical-static-pgn", source_label="Static PGN",
                registry=registry, names=names, contexts=contexts,
                tournament_id=tid, player_hint=player_hint,
                public_pgn_path=public_data_path(path), verified_by="git-input",
            )


def resolve_docs_data_path(value: Any) -> pathlib.Path:
    text = clean(value).lstrip("/")
    if text.startswith("data/"):
        return ROOT / "docs" / text
    return resolve_repo_path(text)


def ingest_bulk_youth(
    games: dict[str, dict[str, Any]],
    registry: dict[str, dict[str, Any]],
    names: dict[str, str],
    contexts: dict[str, dict[str, Any]],
) -> None:
    if not BULK_YOUTH_MANIFEST.is_file():
        return
    manifest = read_json(BULK_YOUTH_MANIFEST, required=True)
    for stage in manifest.get("stages") or []:
        stage_id = clean(stage.get("id"))
        index_path = resolve_docs_data_path(stage.get("indexPath"))
        pgn_path = resolve_docs_data_path(stage.get("pgnPath"))
        if not stage_id or not index_path.is_file() or not pgn_path.is_file():
            continue
        entries = read_json(index_path, required=True)
        pgn_games = split_asset(pgn_path)
        by_key: dict[str, str] = {}
        for game in pgn_games:
            by_key.setdefault(pgn.game_key_from_headers(pgn.pgn_headers(game)), game)
        positions = {pgn.stable_game_hash(game): index for index, game in enumerate(pgn_games)}
        for entry in entries if isinstance(entries, list) else []:
            fide_id = clean(entry.get("fideID"))
            if fide_id not in registry:
                continue
            game = by_key.get(pgn.game_key_from_entry(entry), "")
            if not game:
                game = next((candidate for candidate in pgn_games if pgn.loose_match(pgn.pgn_headers(candidate), entry)), "")
            if not game:
                continue
            add_game(
                games, game=game, asset_path=pgn_path,
                game_index=positions.get(pgn.stable_game_hash(game), 0),
                source_kind="canonical-bulk-pgn", source_label="Lichess Broadcasts",
                registry=registry, names=names, contexts=contexts,
                player_hint=fide_id, stage=stage_id,
                public_pgn_path=public_data_path(pgn_path),
                source_index_path=public_data_path(index_path),
                source_shard=clean(entry.get("sourceShard")),
                verified_by=repo_path(BULK_YOUTH_MANIFEST),
            )


def tree_fact(label: str, paths: Iterable[pathlib.Path]) -> dict[str, Any]:
    rows = []
    byte_count = 0
    for path in sorted({item for item in paths if item.is_file()}):
        digest = sha256_file(path)
        size = path.stat().st_size
        rows.append(f"{repo_path(path)}\0{digest}\0{size}")
        byte_count += size
    return {
        "label": label,
        "files": len(rows),
        "bytes": byte_count,
        "treeSha256": hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest(),
    }


def input_contract() -> list[dict[str, Any]]:
    bulk_assets: list[pathlib.Path] = []
    bulk_manifest = read_json(BULK_YOUTH_MANIFEST, {}) if BULK_YOUTH_MANIFEST.is_file() else {}
    for entry in bulk_manifest.get("stages") or bulk_manifest.get("groups") or []:
        if not isinstance(entry, dict):
            continue
        for field in ("indexPath", "pgnPath"):
            value = clean(entry.get(field))
            if value:
                path = resolve_docs_data_path(value)
                if not path.is_file():
                    raise RuntimeError(f"CANONICAL_FACT_INPUT_MISSING: {path}")
                bulk_assets.append(path)
    return [
        tree_fact("registry", [REGISTRY]),
        tree_fact("tournament-name-mappings", [MAPPINGS]),
        tree_fact("event-details", EVENT_DETAILS.glob("tnr*.json")),
        tree_fact("verified-event-pgn", EVENT_PGN.glob("tnr*.pgn")),
        tree_fact("event-pgn-receipt", [EVENT_PGN_RECEIPT]),
        tree_fact("static-canonical-pgn", (
            path for path in STATIC_PGN_ROOT.rglob("*.pgn")
            if "by-player" not in path.relative_to(STATIC_PGN_ROOT).parts
        ) if STATIC_PGN_ROOT.is_dir() else []),
        tree_fact("lichess-event", [LICHESS_EVENT_MANIFEST, *sorted((LICHESS_EVENT_ROOT / "pgn").glob("tnr*.pgn"))]),
        tree_fact("bulk-youth", [BULK_YOUTH_MANIFEST, *bulk_assets] if BULK_YOUTH_MANIFEST.is_file() else []),
    ]


def write_dataset(root: pathlib.Path, kind: str, facts: list[dict[str, Any]], inputs: list[dict[str, Any]], totals: dict[str, Any]) -> dict[str, Any]:
    sid = snapshot_id()
    generated_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    data_path = root / "facts.json"
    write_json(data_path, {
        "schemaVersion": 1,
        "snapshotId": sid,
        "generatedAt": generated_at,
        "kind": kind,
        "facts": facts,
    }, ensure_ascii=False, separators=(",", ":"))
    manifest = {
        "schemaVersion": 1,
        "snapshotId": sid,
        "generatedAt": generated_at,
        "kind": kind,
        "dataFile": data_path.name,
        "dataPath": repo_path(data_path),
        "dataSha256": sha256_file(data_path),
        "rows": len(facts),
        "totals": totals,
        "inputs": inputs,
    }
    write_json(root / "manifest.json", manifest, ensure_ascii=False, indent=2)
    return manifest


def build() -> tuple[dict[str, Any], dict[str, Any]]:
    registry, names = registry_indexes()
    contexts, event_facts = detail_contexts(registry, names)
    games: dict[str, dict[str, Any]] = {}
    ingest_verified_event_archives(games, registry, names, contexts)
    ingest_verified_lichess_events(games, registry, names, contexts)
    ingest_static_event_pgns(games, registry, names, contexts)
    ingest_bulk_youth(games, registry, names, contexts)
    game_facts = sorted(games.values(), key=lambda row: (row.get("date", ""), row["id"]))

    games_by_player_event: dict[tuple[str, str], int] = defaultdict(int)
    games_by_player: dict[str, int] = defaultdict(int)
    for fact in game_facts:
        tid = clean(fact.get("tournamentID"))
        for fide_id in fact.get("playerFideIDs") or []:
            games_by_player[fide_id] += 1
            if tid:
                games_by_player_event[(fide_id, tid)] += 1
    for fact in event_facts:
        fact["gameCount"] = games_by_player_event.get((fact["fideID"], fact["tournamentID"]), 0)

    inputs = input_contract()
    event_manifest = write_dataset(EVENT_FACT_ROOT, "player-event-facts", event_facts, inputs, {
        "registryPlayers": len(registry),
        "players": len({fact["fideID"] for fact in event_facts}),
        "events": len({fact["tournamentID"] for fact in event_facts}),
        "participations": len(event_facts),
        "withGames": sum(bool(fact.get("gameCount")) for fact in event_facts),
    })
    game_manifest = write_dataset(GAME_FACT_ROOT, "player-game-facts", game_facts, inputs, {
        "registryPlayers": len(registry),
        "games": len(game_facts),
        "players": len(games_by_player),
        "playerGameLinks": sum(games_by_player.values()),
        "unlinkedGames": sum(not fact.get("playerFideIDs") for fact in game_facts),
        "events": len({clean(fact.get("tournamentID")) for fact in game_facts if clean(fact.get("tournamentID"))}),
    })
    return event_manifest, game_manifest


def main() -> int:
    event_manifest, game_manifest = build()
    print(json.dumps({
        "playerEventFacts": event_manifest["rows"],
        "playerGameFacts": game_manifest["rows"],
        "playerGameLinks": game_manifest["totals"]["playerGameLinks"],
        "snapshotId": event_manifest["snapshotId"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
