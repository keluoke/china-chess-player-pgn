#!/usr/bin/env python3
"""Build docs/data/dashboard.json — homepage data dashboard payload.

Pure computation from committed artifacts plus git history (available both
locally and on Actions checkouts):

- totals: players / withChineseName / games / events
- community: distinct human contributors + latest human contributor
- recentEvents: newest ingested events (name / date / CHN players / games)
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import subprocess

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS_DATA = REPO_ROOT / "docs" / "data"
OUTPUT = DOCS_DATA / "dashboard.json"

BOT_MARKERS = ("github-actions", "[bot]", "actions@github.com", "noreply@github.com")


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
        key = email.lower() or name.lower()
        humans.setdefault(key, name)
        if latest is None:
            latest = {"name": name, "date": date[:10]}
    return {"count": len(humans) or None, "latest": latest}


def recent_events(limit: int = 8) -> list[dict]:
    events = read_json(DOCS_DATA / "index" / "events.json", []) or []
    dated = [e for e in events if e.get("date")]
    dated.sort(key=lambda e: str(e.get("date")), reverse=True)
    return [
        {
            "name": e.get("name"),
            "date": e.get("date"),
            "source": e.get("source"),
            "playerCount": e.get("playerCount"),
            "gameCount": e.get("gameCount"),
        }
        for e in dated[:limit]
    ]


def main() -> int:
    registry = (read_json(DOCS_DATA / "registry" / "manifest.json", {}) or {}).get("totals", {})
    by_player = (read_json(DOCS_DATA / "index" / "by-player" / "manifest.json", {}) or {}).get("totals", {})
    index_manifest = (read_json(DOCS_DATA / "index" / "manifest.json", {}) or {}).get("totals", {})
    changelog = (read_json(DOCS_DATA / "changelog.json", {}) or {}).get("entries", [])

    payload = {
        "generatedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "totals": {
            "players": registry.get("players"),
            "withChineseName": registry.get("withChineseName"),
            "games": by_player.get("games"),
            "events": index_manifest.get("events") or len(read_json(DOCS_DATA / "index" / "events.json", []) or []),
            "playersWithGames": by_player.get("players"),
        },
        "community": git_contributors(),
        "latestDelta": (changelog[0] if changelog else None),
        "recentEvents": recent_events(),
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"totals": payload["totals"], "community": payload["community"], "recentEvents": len(payload["recentEvents"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
