#!/usr/bin/env python3
"""Build the static data API under docs/api/v1/.

Pure computation from committed indexes — no network. Endpoints are plain
files served by GitHub/Cloudflare Pages (CORS: *), see docs/API.md.

Design constraints:
- Cloudflare Pages caps a deployment at 20k files, so per-player endpoint
  files are only generated for players that actually have PGN data
  (by-player index); the full registry is served in one players.json.
- PGN payloads are NOT copied: endpoints reference the existing stable
  paths under /data/pgn/by-player/, which are part of the API contract.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from age_groups import LEADERBOARD_GROUPS  # noqa: E402
from canonical_player_facts import (  # noqa: E402
    PLAYER_EVENT_FACTS,
    PLAYER_GAME_FACTS,
    load_fact_dataset,
    manifest_reference,
)
from public_metrics import canonical_public_metrics  # noqa: E402
from snapshot_context import snapshot_id  # noqa: E402
from stable_json import write_json as write_stable_json  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "docs"
REGISTRY_PLAYERS = DOCS / "data" / "registry" / "players.json"
BY_PLAYER_INDEX = DOCS / "data" / "index" / "by-player"
LEADERBOARDS = DOCS / "data" / "leaderboards.json"
PUBLIC_EVENTS = DOCS / "data" / "index" / "public-events.json"
API_ROOT = DOCS / "api" / "v1"
API_V2_ROOT = DOCS / "api" / "v2"
API_VERSION = "1"

LICENSE_BLOCK = {
    "data": "Source-specific terms; no blanket database relicense",
    "community": "Original reviewed community contributions: CC BY 4.0",
    "lichess": "Lichess Broadcast derivatives: CC BY-SA 4.0 with attribution",
    "fide": "Factual registry projection; source attribution retained",
    "chessResults": "Cleaned structured event data collected and published by the maintainer; raw pages stay private",
    "note": "See LICENSE-DATA.md for the source-level policy.",
}


def read_json(path: pathlib.Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: pathlib.Path, data) -> None:
    write_stable_json(path, data, ensure_ascii=False, separators=(",", ":"))


def prune_stale_api_files(root: pathlib.Path, expected: set[pathlib.Path]) -> None:
    """Remove obsolete endpoints after the replacement set is complete.

    Keeping existing files until they are rewritten lets ``stable_json``
    retain timestamps and bytes for semantically unchanged payloads.  The old
    implementation removed the whole API tree first, which turned every
    no-op rebuild into thousands of timestamp-only changes.
    """
    if not root.exists():
        return
    for path in root.rglob("*"):
        if path.is_file() and path not in expected:
            path.unlink()
    for directory in sorted((path for path in root.rglob("*") if path.is_dir()), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass


def compact_player(p: dict) -> dict:
    out = {
        "fideID": p.get("fideID"),
        "displayName": p.get("displayName"),
        "chineseName": p.get("chineseName") or None,
        "pinyin": p.get("pinyin") or None,
        "name": p.get("name"),
        "federation": p.get("federation"),
        "formerFederation": p.get("formerFederation") or None,
        "transfer": p.get("transfer") or None,
        "sex": p.get("sex") or None,
        "title": p.get("title") or None,
        "birthYear": p.get("birthYear"),
        "standard": p.get("standard"),
        "rapid": p.get("rapid"),
        "blitz": p.get("blitz"),
        "inactive": bool(p.get("inactive")),
    }
    return {k: v for k, v in out.items() if v is not None}


def player_bucket(fide_id: str) -> str:
    return f"{int(fide_id) % 256:02x}"


def main() -> int:
    generated_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    sid = snapshot_id()
    players = read_json(REGISTRY_PLAYERS)
    event_facts, event_fact_manifest = load_fact_dataset(PLAYER_EVENT_FACTS, "player-event-facts")
    game_facts, game_fact_manifest = load_fact_dataset(PLAYER_GAME_FACTS, "player-game-facts")
    fact_inputs = {
        "playerEvents": manifest_reference(PLAYER_EVENT_FACTS, event_fact_manifest),
        "playerGames": manifest_reference(PLAYER_GAME_FACTS, game_fact_manifest),
    }
    public_metrics = canonical_public_metrics()
    API_ROOT.mkdir(parents=True, exist_ok=True)
    player_api_root = API_ROOT / "players"
    player_bucket_root = API_ROOT / "player-buckets"
    player_api_root.mkdir(parents=True, exist_ok=True)
    expected_files: set[pathlib.Path] = set()
    player_buckets: dict[str, dict[str, dict]] = {}

    def emit(path: pathlib.Path, data) -> None:
        write_json(path, data)
        expected_files.add(path)

    # --- players.json: full registry, compact -------------------------------
    emit(API_ROOT / "players.json", {
        "snapshotId": sid,
        "generatedAt": generated_at,
        "count": len(players),
        "players": [compact_player(p) for p in players],
    })

    # --- search.json: alias -> fideID rows ----------------------------------
    emit(API_ROOT / "search.json", {
        "snapshotId": sid,
        "generatedAt": generated_at,
        "rows": [[p.get("fideID"), "|".join(p.get("aliases", []))] for p in players],
    })

    # --- leaderboards --------------------------------------------------------
    if LEADERBOARDS.exists():
        emit(API_ROOT / "leaderboards.json", read_json(LEADERBOARDS))

    # --- per-player endpoints (only players with PGN data) ------------------
    detailed = 0
    registry_by_id = {str(p.get("fideID")): p for p in players}
    public_catalog = {
        str(row.get("tournamentID") or "").strip(): row
        for row in (read_json(PUBLIC_EVENTS).get("events") if PUBLIC_EVENTS.is_file() else []) or []
        if str(row.get("tournamentID") or "").strip()
    }
    event_facts_by_player: dict[str, list[dict]] = {}
    for fact in event_facts:
        fide_id = str(fact.get("fideID") or "").strip()
        tid = str(fact.get("tournamentID") or "").strip()
        catalog_event = public_catalog.get(tid)
        if not fide_id or not catalog_event:
            continue
        event_facts_by_player.setdefault(fide_id, []).append({
            "tournamentID": tid,
            "name": catalog_event.get("chineseName") or catalog_event.get("displayName") or catalog_event.get("name"),
            "date": catalog_event.get("date") or fact.get("date"),
            "rank": fact.get("rank"),
        })
    game_counts: dict[str, int] = {}
    for fact in game_facts:
        for fide_id in fact.get("playerFideIDs") or []:
            fide_id = str(fide_id)
            game_counts[fide_id] = game_counts.get(fide_id, 0) + 1

    if (
        int(public_metrics["totals"].get("playersWithGames") or 0) != len(game_counts)
        or int(public_metrics["totals"].get("games") or 0) != sum(game_counts.values())
    ):
        raise RuntimeError(
            "PUBLIC_METRICS_FACT_COVERAGE_MISMATCH: public metrics totals do not "
            "equal canonical player-game links"
        )

    for fide_id in sorted(game_counts):
        detail_file = BY_PLAYER_INDEX / f"fide-{fide_id}.json"
        if not detail_file.is_file():
            raise RuntimeError(f"PLAYER_GAME_FACT_PROJECTION_MISSING: {fide_id}: {detail_file}")
        detail = read_json(detail_file)
        detail_fide_id = str(detail.get("player", {}).get("fideID") or detail_file.stem.replace("fide-", ""))
        if detail_fide_id != fide_id:
            raise RuntimeError(f"PLAYER_GAME_FACT_PROJECTION_ID_MISMATCH: {fide_id} != {detail_fide_id}")
        if detail.get("snapshotId") != sid:
            raise RuntimeError(
                f"PLAYER_GAME_FACT_PROJECTION_SNAPSHOT_MISMATCH: {fide_id}: "
                f"{detail.get('snapshotId')} != {sid}"
            )
        reg = registry_by_id.get(fide_id, {})
        if not reg:
            raise RuntimeError(f"REGISTRY_AUTHORITY_MISMATCH: by-player identity {fide_id} is absent from registry")
        packages = []
        for pkg in detail.get("packages", []):
            packages.append({
                "id": pkg.get("id"),
                "gameCount": pkg.get("gameCount"),
                "pgnPath": "/" + str(pkg.get("pgnPath", "")).lstrip("/"),
                "publicURL": pkg.get("publicURL"),
                "sha256": pkg.get("sha256"),
                "stages": pkg.get("stages"),
            })
        events = sorted(
            event_facts_by_player.get(fide_id, []),
            key=lambda row: (row.get("date") or "", row.get("tournamentID") or ""),
            reverse=True,
        )
        if int(detail.get("totals", {}).get("games") or 0) != game_counts[fide_id]:
            raise RuntimeError(
                f"PLAYER_GAME_FACT_COVERAGE_MISMATCH: {fide_id}: "
                f"by-player={detail.get('totals', {}).get('games')}, facts={game_counts[fide_id]}"
            )
        # The registry is the only identity/rating authority. Never merge an
        # old by-player identity underneath a sparse registry row: missing
        # registry values must clear stale derivatives instead of reviving
        # them.
        identity = reg
        payload = {
            "snapshotId": sid,
            "generatedAt": generated_at,
            **compact_player(identity),
            "gameCount": game_counts[fide_id],
            "eventCount": len(events),
            "packages": packages,
            "events": events,
        }
        player_path = player_api_root / f"fide-{fide_id}.json"
        emit(player_path, payload)
        # Read the stable projection back after emit. Its generatedAt may have
        # been retained from an earlier byte-identical build; embedding the
        # pre-write payload would make bucket bytes depend on whether a build
        # crossed a wall-clock second.
        player_buckets.setdefault(player_bucket(fide_id), {})[fide_id] = read_json(player_path)
        detailed += 1

    for bucket, bucket_players in sorted(player_buckets.items()):
        emit(player_bucket_root / f"{bucket}.json", {
            "schemaVersion": 1,
            "snapshotId": snapshot_id(),
            "players": bucket_players,
        })

    expected_detailed = public_metrics["totals"]["playersWithGames"]
    if detailed != expected_detailed:
        raise RuntimeError(f"API player endpoints ({detailed}) != canonical playersWithGames ({expected_detailed})")

    # --- API v2 (resource-sharded; plan §8.4) -------------------------------
    # File-count budget: Cloudflare Pages caps a deployment at 20k files and
    # the tree already sits near it, so v2 currently ships the manifest and
    # official-ranking shards only. Per-player/event v2 shards and search
    # shards follow once bulk/PGN assets move to object storage (P2-2).
    v2_root = API_V2_ROOT
    if LEADERBOARDS.exists():
        boards = read_json(LEADERBOARDS)
        basis_year = boards.get("basisYear")
        for group in boards.get("groups", []):
            cohort = str(group.get("id") or "").strip() or "unknown"
            rankings = group.get("rankings") or {"standard": {"all": {
                "totalEligible": group.get("totalEligible"),
                "players": group.get("players"),
                "birthYears": {},
            }}}
            for control, scopes in rankings.items():
                emit(v2_root / "rankings" / "official" / "current" / control / f"{cohort}.json", {
                    "schemaVersion": 2,
                    "snapshotId": sid,
                    "generatedAt": generated_at,
                    "track": "official",
                    "control": control,
                    "cohort": cohort,
                    "label": group.get("label"),
                    "basisYear": basis_year,
                    "cohortRule": {"minAge": group.get("minAge"), "maxAge": group.get("maxAge")},
                    "rankings": scopes,
                    "license": LICENSE_BLOCK,
                })
    emit(v2_root / "manifest.json", {
        "apiVersion": "2",
        "schemaVersion": 2,
        "snapshotId": sid,
        "generatedAt": generated_at,
        "status": "preview",
        "endpoints": {
            "rankings": "/api/v2/rankings/official/current/{control}/{cohort}.json",
            "playersCompat": "/api/v1/players/fide-{fideID}.json（v2 分片端点将随对象存储迁移上线）",
            "eventsCompat": "/data/index/event-details/tnr{tournamentID}.json",
        },
        "notes": "官方榜与未来参考榜永久分轨；所有响应引用同一 snapshotId。",
        "license": LICENSE_BLOCK,
    })

    # --- manifest ------------------------------------------------------------
    emit(API_ROOT / "manifest.json", {
        "apiVersion": API_VERSION,
        "snapshotId": sid,
        "generatedAt": generated_at,
        "totals": {
            "players": len(players),
            "withChineseName": sum(1 for p in players if p.get("chineseName")),
            "withGameData": public_metrics["totals"]["playersWithGames"],
            "games": public_metrics["totals"]["games"],
        },
        "metricContract": {
            "version": public_metrics["metricVersion"],
            "scope": public_metrics["scope"],
            "source": "/data/public-metrics.json",
        },
        "factInputs": fact_inputs,
        "ageGroups": [g["id"] for g in LEADERBOARD_GROUPS] + ["adult"],
        "endpoints": {
            "players": "/api/v1/players.json",
            "search": "/api/v1/search.json",
            "leaderboards": "/api/v1/leaderboards.json",
            "player": "/api/v1/players/fide-{fideID}.json (only players with game data)",
            "playerBucket": "/api/v1/player-buckets/{bucket}.json",
            "playerBucketRule": "integer FIDE ID modulo 256, lower-case hex",
            "pgn": "/data/pgn/by-player/fide-{fideID}/{all|U8..U18|adult}.pgn (paths listed per player in packages[])",
        },
        "license": LICENSE_BLOCK,
        "docs": "https://github.com/keluoke/china-chess-player-pgn/blob/main/docs/API.md",
    })

    prune_stale_api_files(API_ROOT, expected_files)
    prune_stale_api_files(v2_root, expected_files)

    print(json.dumps({"players": len(players), "playerEndpoints": detailed, "apiRoot": str(API_ROOT.relative_to(REPO_ROOT))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
