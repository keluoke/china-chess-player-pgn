#!/usr/bin/env python3
"""Reconcile dirty per-player PGNs against complete local event archives.

This is a local, offline safety tool.  It never contacts a source.  It compares
each changed ``docs/data/pgn/chess-results`` file against both Git ``HEAD`` and
the already-stored complete tournament archive.  ``--apply`` writes an audit
receipt and preserves both sides of a real conflict in the private quarantine
area before restoring a clean repository baseline for a policy-enforced replay.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DOC_PGN_ROOT = ROOT / "docs" / "data" / "pgn" / "chess-results"
ARCHIVE_ROOT = ROOT / "data" / "generated" / "chess-results-event-pgn"
sys.path.insert(0, str(ROOT / "Scripts"))

from fetch_event_pgn import fide_id_for, load_china_fide_ids, load_name_index, parse_headers, split_games  # noqa: E402
from source_policy import local_state_root  # noqa: E402


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=ROOT)


def changed_paths() -> list[str]:
    """Return both modified tracked and untracked per-player PGN files."""
    output = git("status", "--porcelain=v1", "--", "docs/data/pgn/chess-results").decode("utf-8")
    paths = [line[3:] for line in output.splitlines() if len(line) > 3 and line[3:].endswith(".pgn")]
    return sorted(set(paths))


_WS_RE = re.compile(r"\s+")


def game_fingerprint(game: str) -> str:
    headers = parse_headers(game)
    tags = "|".join(
        f"{key.casefold()}={_WS_RE.sub(' ', value).strip().casefold()}"
        for key, value in sorted(headers.items())
    )
    moves = re.sub(r"^\[[^\n]*\]\s*$", "", game, flags=re.MULTILINE)
    return tags + "\n" + re.sub(r"\s+", " ", moves).strip()


def fingerprints(text: str) -> set[str]:
    return {game_fingerprint(game) for game in split_games(text)}


def key_from_path(path: str) -> tuple[str, str] | None:
    match = re.search(r"/tnr(\d+)/fide-(\d+)-\d+\.pgn$", path)
    return (match.group(1), match.group(2)) if match else None


def expected_games(tournament_id: str, fide_id: str, china_ids: set[str], names: dict[str, str]) -> set[str] | None:
    archive = ARCHIVE_ROOT / f"tnr{tournament_id}.pgn"
    if not archive.is_file():
        return None
    selected: list[str] = []
    for game in split_games(archive.read_text(encoding="utf-8", errors="replace")):
        headers = parse_headers(game)
        assigned = [fide_id_for(headers, side, china_ids, names) for side in ("White", "Black")]
        if fide_id in assigned:
            selected.append(game)
    return {game_fingerprint(game) for game in selected}


def classify(path: str, china_ids: set[str], names: dict[str, str]) -> dict[str, Any]:
    working_path = ROOT / path
    working = working_path.read_bytes()
    show = subprocess.run(["git", "show", f"HEAD:{path}"], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    head_exists = show.returncode == 0
    head = show.stdout if head_exists else b""
    work_games = fingerprints(working.decode("utf-8", errors="replace"))
    head_games = fingerprints(head.decode("utf-8", errors="replace"))
    key = key_from_path(path)
    expected = expected_games(*key, china_ids, names) if key else None
    if head_exists and work_games == head_games:
        verdict = "semantic-identical"
    elif expected is None:
        verdict = "unresolved-no-local-archive"
    elif work_games == expected and head_games != expected:
        verdict = "working-matches-local-archive"
    elif head_games == expected and work_games != expected:
        verdict = "head-matches-local-archive"
    else:
        verdict = "unresolved-conflict"
    return {
        "path": path,
        "tournamentID": key[0] if key else "",
        "fideID": key[1] if key else "",
        "verdict": verdict,
        "workingSha256": digest(working),
        "headSha256": digest(head) if head_exists else None,
        "headExists": head_exists,
        "workingGames": len(work_games),
        "headGames": len(head_games),
        "archiveGames": None if expected is None else len(expected),
        "workingOnlyGames": len(work_games - head_games),
        "headOnlyGames": len(head_games - work_games),
        "_working": working,
        "_head": head,
    }


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def public_record(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if not key.startswith("_")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write a private receipt and restore clean baseline files")
    args = parser.parse_args()
    china_ids, names = load_china_fide_ids(), load_name_index()
    items = [classify(path, china_ids, names) for path in changed_paths()]
    counts = Counter(item["verdict"] for item in items)
    replay_tournaments = sorted({item["tournamentID"] for item in items if item["verdict"] == "working-matches-local-archive"})
    result: dict[str, Any] = {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "apply": args.apply,
        "summary": dict(counts),
        "replayTournaments": replay_tournaments,
        "files": [public_record(item) for item in items],
    }
    unresolved = [item for item in items if item["verdict"].startswith("unresolved-")]
    if not args.apply:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if unresolved else 0
    if unresolved:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit("存在无法由本地完整归档判定的 PGN 冲突；未修改任何文件。")

    quarantine = local_state_root() / "pgn-conflicts" / datetime.now().strftime("%Y%m%d-%H%M%S")
    result["quarantineRoot"] = str(quarantine)
    for item in items:
        path = Path(item["path"])
        verdict = item["verdict"]
        # The good working copy and the superseded baseline are both retained
        # privately for genuine conflicts.  Restoring HEAD keeps the release
        # path clean; a later policy-enforced replay regenerates the validated
        # archive-derived version through a normal manifest.
        if verdict == "working-matches-local-archive":
            atomic_write(quarantine / "validated-working" / path, item["_working"])
            if item["headExists"]:
                atomic_write(quarantine / "superseded-baseline" / path, item["_head"])
        elif verdict == "head-matches-local-archive":
            atomic_write(quarantine / "rejected-working" / path, item["_working"])
            atomic_write(quarantine / "validated-baseline" / path, item["_head"])
        if item["headExists"]:
            atomic_write(ROOT / path, item["_head"])
        else:
            (ROOT / path).unlink()
    atomic_write(quarantine / "audit.json", (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
