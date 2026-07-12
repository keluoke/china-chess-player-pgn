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
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from age_groups import LEADERBOARD_GROUPS  # noqa: E402
from public_metrics import canonical_public_metrics  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "docs"
REGISTRY_PLAYERS = DOCS / "data" / "registry" / "players.json"
BY_PLAYER_INDEX = DOCS / "data" / "index" / "by-player"
LEADERBOARDS = DOCS / "data" / "leaderboards.json"
API_ROOT = DOCS / "api" / "v1"
API_VERSION = "1"

LICENSE_BLOCK = {
    "data": "CC BY 4.0 — attribution: china-chess-player-pgn contributors",
    "note": "PGN game scores originate from public Chess-Results/Lichess broadcast pages; see LICENSE-DATA.md in the repository for source attribution requirements.",
}


def read_json(path: pathlib.Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: pathlib.Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")


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


def main() -> int:
    generated_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    players = read_json(REGISTRY_PLAYERS)
    public_metrics = canonical_public_metrics()

    if API_ROOT.exists():
        shutil.rmtree(API_ROOT)

    # --- players.json: full registry, compact -------------------------------
    write_json(API_ROOT / "players.json", {
        "generatedAt": generated_at,
        "count": len(players),
        "players": [compact_player(p) for p in players],
    })

    # --- search.json: alias -> fideID rows ----------------------------------
    write_json(API_ROOT / "search.json", {
        "generatedAt": generated_at,
        "rows": [[p.get("fideID"), "|".join(p.get("aliases", []))] for p in players],
    })

    # --- leaderboards --------------------------------------------------------
    if LEADERBOARDS.exists():
        write_json(API_ROOT / "leaderboards.json", read_json(LEADERBOARDS))

    # --- per-player endpoints (only players with PGN data) ------------------
    detailed = 0
    registry_by_id = {str(p.get("fideID")): p for p in players}
    for detail_file in sorted(BY_PLAYER_INDEX.glob("fide-*.json")):
        detail = read_json(detail_file)
        fide_id = str(detail.get("player", {}).get("fideID") or detail_file.stem.replace("fide-", ""))
        reg = registry_by_id.get(fide_id, {})
        packages = []
        for pkg in detail.get("packages", []):
            packages.append({
                "id": pkg.get("id"),
                "gameCount": pkg.get("gameCount"),
                "pgnPath": "/" + str(pkg.get("pgnPath", "")).lstrip("/"),
                "sha256": pkg.get("sha256"),
                "stages": pkg.get("stages"),
            })
        events = [
            {
                "tournamentID": e.get("tournamentID"),
                "name": e.get("name"),
                "date": e.get("date"),
                "rank": e.get("rank"),
                "source": e.get("source"),
            }
            for e in detail.get("events", [])
        ]
        payload = {
            "generatedAt": generated_at,
            **compact_player({**detail.get("player", {}), **reg}),
            "gameCount": detail.get("totals", {}).get("games"),
            "eventCount": len(events),
            "packages": packages,
            "events": events,
        }
        write_json(API_ROOT / "players" / f"fide-{fide_id}.json", payload)
        detailed += 1

    expected_detailed = public_metrics["totals"]["playersWithGames"]
    if detailed != expected_detailed:
        raise RuntimeError(f"API player endpoints ({detailed}) != canonical playersWithGames ({expected_detailed})")

    # --- manifest ------------------------------------------------------------
    write_json(API_ROOT / "manifest.json", {
        "apiVersion": API_VERSION,
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
        "ageGroups": [g["id"] for g in LEADERBOARD_GROUPS] + ["adult"],
        "endpoints": {
            "players": "/api/v1/players.json",
            "search": "/api/v1/search.json",
            "leaderboards": "/api/v1/leaderboards.json",
            "player": "/api/v1/players/fide-{fideID}.json (only players with game data)",
            "pgn": "/data/pgn/by-player/fide-{fideID}/{all|U8..U18|adult}.pgn (paths listed per player in packages[])",
        },
        "license": LICENSE_BLOCK,
        "docs": "https://github.com/keluoke/china-chess-player-pgn/blob/main/docs/API.md",
    })

    print(json.dumps({"players": len(players), "playerEndpoints": detailed, "apiRoot": str(API_ROOT.relative_to(REPO_ROOT))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
