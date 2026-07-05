#!/usr/bin/env python3
"""Build browser mimic profiles for youth players with updated PGNs.

The raw polyglot/style profile is generated in .build by default, then exported
as docs/mimic/profiles/fide-*/profile.js for the static web simulator.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
BUILD_PROFILE = ROOT / "mimic-engine" / "build_player_profile.py"
EXPORT_PROFILE = ROOT / "mimic-engine" / "export_web_profile.py"
SCHEMA_VERSION = 1
BUILD_VERSION = 2


def read_json(path: pathlib.Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_if_changed(path: pathlib.Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return
    path.write_text(text, encoding="utf-8")


def repo_relative(path: pathlib.Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        return str(path)


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compact_stage_label(stages: dict[str, Any]) -> str:
    return ",".join(sorted(str(key) for key in stages.keys())) if stages else ""


def is_youth_player(player: dict[str, Any], current_year: int) -> bool:
    if player.get("stages"):
        return True
    birth_year = player.get("birthYear")
    return isinstance(birth_year, int) and birth_year >= current_year - 18


def unique_aliases(player: dict[str, Any]) -> list[str]:
    values = [
        player.get("displayName"),
        player.get("name"),
        player.get("chineseName"),
        *(player.get("aliases") or []),
    ]
    aliases = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        aliases.append(text)
    return aliases or [str(player.get("fideID") or player.get("id") or "")]


def player_id(player: dict[str, Any]) -> str:
    fide_id = str(player.get("fideID") or "").strip()
    if fide_id:
        return f"fide-{fide_id}"
    return str(player.get("id") or "").strip()


def profile_input_hash(player: dict[str, Any], pgn_sha: str, book_max_ply: int) -> str:
    return sha256_json({
        "pgnSha256": pgn_sha,
        "bookMaxPly": book_max_ply,
        "fideID": str(player.get("fideID") or ""),
        "displayName": player.get("displayName") or "",
        "name": player.get("name") or "",
        "chineseName": player.get("chineseName") or "",
        "aliases": unique_aliases(player),
    })


def build_one(
    player: dict[str, Any],
    pgn_path: pathlib.Path,
    raw_profile_dir: pathlib.Path,
    web_profile_path: pathlib.Path,
    book_max_ply: int,
) -> None:
    if raw_profile_dir.exists():
        shutil.rmtree(raw_profile_dir)
    raw_profile_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(BUILD_PROFILE),
        "--pgn",
        str(pgn_path),
        "--fide-id",
        str(player.get("fideID") or ""),
        "--out-dir",
        str(raw_profile_dir),
        "--book-max-ply",
        str(book_max_ply),
    ]
    for alias in unique_aliases(player):
        command.extend(["--player", alias])
    run_checked(command)

    web_profile_path.parent.mkdir(parents=True, exist_ok=True)
    run_checked(
        [
            sys.executable,
            str(EXPORT_PROFILE),
            "--profile",
            str(raw_profile_dir),
            "--out",
            str(web_profile_path),
        ],
    )


def run_checked(command: list[str]) -> None:
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    if result.returncode == 0:
        return
    if result.stdout:
        print(result.stdout, file=sys.stderr)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    raise subprocess.CalledProcessError(result.returncode, command)


def select_players(players: list[dict[str, Any]], requested: set[str], min_games: int) -> list[dict[str, Any]]:
    current_year = dt.date.today().year
    selected = []
    for player in players:
        pid = player_id(player)
        fide_id = str(player.get("fideID") or "")
        if requested and pid not in requested and fide_id not in requested:
            continue
        if not requested and not is_youth_player(player, current_year):
            continue
        if int(player.get("playerPgnGameCount") or player.get("gameCount") or 0) < min_games:
            continue
        selected.append(player)
    selected.sort(key=lambda item: (str(item.get("displayName") or ""), str(item.get("fideID") or "")))
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description="Build static web mimic profiles for youth players.")
    parser.add_argument("--players", type=pathlib.Path, default=DOCS / "data/index/by-player/players.json")
    parser.add_argument("--raw-profile-root", type=pathlib.Path, default=ROOT / ".build/mimic-profiles")
    parser.add_argument("--web-profile-root", type=pathlib.Path, default=DOCS / "mimic/profiles")
    parser.add_argument("--data-manifest", type=pathlib.Path, default=DOCS / "data/mimic/profiles/manifest.json")
    parser.add_argument("--min-games", type=int, default=1)
    parser.add_argument("--book-max-ply", type=int, default=30)
    parser.add_argument("--max-players", type=int, default=0, help="0 means no limit")
    parser.add_argument("--player", action="append", default=[], help="FIDE ID or fide-* id; repeatable")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    players = read_json(args.players, [])
    if not isinstance(players, list):
        raise SystemExit(f"{args.players} must contain a JSON list")

    requested = set()
    for value in args.player:
        text = value.strip()
        requested.add(text)
        requested.add(text.removeprefix("fide-"))

    selected = select_players(players, requested, args.min_games)
    if args.max_players:
        selected = selected[: args.max_players]

    manifest_path = args.web_profile_root / "manifest.json"
    existing_manifest = read_json(manifest_path, {})
    existing_by_id = {item.get("id"): item for item in existing_manifest.get("players", []) if item.get("id")}
    manifest_players = []
    skipped = []
    built = 0
    unchanged = 0

    for player in selected:
        pid = player_id(player)
        if not pid:
            skipped.append({"reason": "missing id", "player": player.get("displayName")})
            continue
        rel_pgn = player.get("playerPgnPath")
        pgn_path = DOCS / rel_pgn if rel_pgn else DOCS / "data/pgn/by-player" / pid / "all.pgn"
        if not pgn_path.exists():
            skipped.append({"id": pid, "reason": "missing pgn", "pgn": str(pgn_path.relative_to(ROOT))})
            continue

        pgn_sha = sha256_file(pgn_path)
        input_hash = profile_input_hash(player, pgn_sha, args.book_max_ply)
        web_profile_path = args.web_profile_root / pid / "profile.js"
        raw_profile_dir = args.raw_profile_root / pid
        existing = existing_by_id.get(pid) or {}
        needs_build = (
            args.force
            or not web_profile_path.exists()
            or existing.get("pgnSha256") != pgn_sha
            or existing.get("profileInputHash") != input_hash
            or existing.get("buildVersion") != BUILD_VERSION
        )
        if needs_build:
            if args.dry_run:
                print(f"would build {pid} {player.get('displayName')}")
            else:
                build_one(player, pgn_path, raw_profile_dir, web_profile_path, args.book_max_ply)
                print(f"built {pid} {player.get('displayName')}")
            built += 1
        else:
            unchanged += 1

        manifest_players.append({
            "id": pid,
            "fideID": str(player.get("fideID") or ""),
            "displayName": player.get("displayName") or player.get("name") or pid,
            "chineseName": player.get("chineseName") or "",
            "birthYear": player.get("birthYear"),
            "standard": player.get("standard"),
            "rapid": player.get("rapid"),
            "stages": player.get("stages") or {},
            "stageLabel": compact_stage_label(player.get("stages") or {}),
            "games": int(player.get("playerPgnGameCount") or player.get("gameCount") or 0),
            "pgnPath": str(pgn_path.relative_to(DOCS)),
            "pgnSha256": pgn_sha,
            "profileInputHash": input_hash,
            "profilePath": f"profiles/{pid}/profile.js",
            "buildVersion": BUILD_VERSION,
        })

    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "buildVersion": BUILD_VERSION,
        "generatedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "source": repo_relative(args.players),
        "totalProfiles": len(manifest_players),
        "built": built,
        "unchanged": unchanged,
        "skipped": skipped,
        "players": manifest_players,
    }

    if not args.dry_run:
        write_json_if_changed(manifest_path, manifest)
        write_json_if_changed(args.data_manifest, manifest)

    print(json.dumps({
        "selected": len(selected),
        "profiles": len(manifest_players),
        "built": built,
        "unchanged": unchanged,
        "skipped": len(skipped),
        "manifest": repo_relative(manifest_path),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
