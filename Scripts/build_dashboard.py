#!/usr/bin/env python3
"""Build docs/data/dashboard.json — homepage data dashboard payload.

Pure computation from committed artifacts plus git history (available both
locally and on Actions checkouts):

- totals: players / withChineseName / games / events
- community: distinct human contributors + latest human contributor
- recentEvents: newest ingested events (name / date / CHN players / games)
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from public_metrics import canonical_public_metrics  # noqa: E402
from stable_json import write_json  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS_DATA = REPO_ROOT / "docs" / "data"
OUTPUT = DOCS_DATA / "dashboard.json"

BOT_MARKERS = ("github-actions", "[bot]", "actions@github.com", "noreply@github.com")
PLACEHOLDER_AUTHORS = {"test", "unknown", "root", "local"}


def read_json(path: pathlib.Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def git_contributors() -> dict:
    try:
        out = subprocess.run(
            ["git", "log", "--format=%an\t%ae\t%aI", "--no-merges"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=60, check=True,
        ).stdout
    except Exception:
        return {"count": None, "latest": None}
    humans: dict[str, str] = {}
    latest = None
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        name, email, date = parts
        blob = f"{name} {email}".lower()
        if any(marker in blob for marker in BOT_MARKERS):
            continue
        if name.strip().casefold() in PLACEHOLDER_AUTHORS or email.lower().endswith(("@local", "@localhost")):
            continue
        key = email.lower() or name.lower()
        humans.setdefault(key, name)
        if latest is None:
            latest = {"name": name, "date": date[:10]}
    return {"count": len(humans) or None, "latest": latest}


def data_contributors(limit: int = 30) -> list[dict]:
    """鸣谢名录：经审核的人工线索、勘误和质量贡献者。"""
    path = REPO_ROOT / "data" / "community" / "contributors.csv"
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            nickname = (row.get("nickname") or "").strip()
            if not nickname:
                continue
            rows.append({
                "nickname": nickname,
                "github": (row.get("github") or "").strip() or None,
                "submissions": int(row.get("submissions") or 0),
                "players": int(row.get("players") or 0),
                "events": int(row.get("events") or 0),
                "games": int(row.get("games") or 0),
                "since": (row.get("first_contribution") or "").strip() or None,
            })
    rows.sort(key=lambda r: (-r["submissions"], r["since"] or ""))
    return rows[:limit]


def is_event_level(entry: dict) -> bool:
    """Hierarchy: canonical event → section → round → game.

    Rows tagged level="source-item" (e.g. Lichess "Round 6: A - B" broadcast
    fragments) are crawl/evidence units and must not surface as 赛事. Rows
    without a level (older builds) count as events for compatibility.
    """
    return (entry.get("level") or "event") == "event"


def recent_events(limit: int = 8) -> list[dict]:
    events = read_json(DOCS_DATA / "index" / "events.json", []) or []
    # "Latest archived games" is intentionally based on usable PGN coverage,
    # not merely the latest event in a player's prospective tournament list.
    dated = [e for e in events if e.get("date") and e.get("gameCount") and is_event_level(e)]
    dated.sort(key=lambda e: str(e.get("date")), reverse=True)
    return [
        {
            "id": e.get("id"),
            "displayName": e.get("displayName") or e.get("chineseName") or e.get("name"),
            "name": e.get("name"),
            "chineseName": e.get("chineseName"),
            "date": e.get("date"),
            "source": e.get("source"),
            "url": e.get("url"),
            "players": (e.get("players") or [])[:4],
            "playerCount": e.get("playerCount"),
            "gameCount": e.get("gameCount"),
        }
        for e in dated[:limit]
    ]


def main() -> int:
    public_metrics = canonical_public_metrics()
    registry = (read_json(DOCS_DATA / "registry" / "manifest.json", {}) or {}).get("totals", {})
    domestic = (read_json(DOCS_DATA / "registry" / "domestic" / "manifest.json", {}) or {}).get("totals", {})
    by_player = (read_json(DOCS_DATA / "index" / "by-player" / "manifest.json", {}) or {}).get("totals", {})
    index_manifest = (read_json(DOCS_DATA / "index" / "manifest.json", {}) or {}).get("totals", {})
    events = read_json(DOCS_DATA / "index" / "events.json", []) or []
    canonical_events = read_json(DOCS_DATA / "index" / "canonical-events.json", []) or []
    changelog = (read_json(DOCS_DATA / "changelog.json", {}) or {}).get("entries", [])

    payload = {
        "generatedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "totals": {
            "players": registry.get("players"),
            "domesticPlayers": domestic.get("unlinked"),
            "domesticUniqueNames": domestic.get("uniqueNameCount"),
            "domesticSightings": domestic.get("sightings"),
            "searchablePlayers": (registry.get("players") or 0) + (domestic.get("unlinked") or 0),
            "domesticIdentityReview": domestic.get("lowConfidence"),
            "withChineseName": registry.get("withChineseName"),
            "games": public_metrics["totals"]["games"],
            # 赛事数只统计真实赛事层;round/game 级 source item 单独计数,
            # 仅作为证据与抓取单元存在。
            "events": sum(1 for event in events if is_event_level(event)) or index_manifest.get("events"),
            "sourceItems": sum(1 for event in events if not is_event_level(event)),
            "eventsWithChineseName": sum(1 for event in events if event.get("chineseName") and is_event_level(event)),
            "canonicalEvents": len(canonical_events),
            "playersWithGames": public_metrics["totals"]["playersWithGames"],
        },
        "community": git_contributors(),
        "dataContributors": data_contributors(),
        "latestDelta": (changelog[0] if changelog else None),
        "recentEvents": recent_events(),
    }
    write_json(OUTPUT, payload, ensure_ascii=False, indent=1)
    print(json.dumps({"totals": payload["totals"], "community": payload["community"], "recentEvents": len(payload["recentEvents"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
