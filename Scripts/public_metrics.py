#!/usr/bin/env python3
"""Canonical public metrics shared by every user-facing manifest.

The by-player aggregate is the only public PGN coverage authority because it
deduplicates every usable game after direct, bulk, and promoted sources have
been combined. Source-specific manifests may retain their own diagnostics, but
must not publish those diagnostics as whole-database totals.
"""

from __future__ import annotations

import json
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS_DATA = REPO_ROOT / "docs" / "data"
METRIC_VERSION = 2
SCOPE = "games 保留棋手关联次数；playableUniqueGames 为公共赛事目录可复盘独立棋局；archivedUniqueGames 为全部归档事实"


def read_json(path: pathlib.Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_public_metrics(docs_data: pathlib.Path = DOCS_DATA) -> dict:
    registry = read_json(docs_data / "registry" / "manifest.json", {}) or {}
    aggregate = read_json(docs_data / "index" / "by-player" / "manifest.json", {}) or {}
    facts = read_json(docs_data.parent.parent / "data/generated/player-game-facts/manifest.json", {}) or {}
    fact_totals = facts.get("totals", {})
    catalog = read_json(docs_data / "index/public-events.json", {}) or {}
    registry_totals = registry.get("totals", {})
    aggregate_totals = aggregate.get("totals", {})
    players_with_games = aggregate_totals.get("players")
    games = aggregate_totals.get("games")
    if not isinstance(players_with_games, int) or players_with_games < 0:
        raise ValueError("by-player manifest totals.players is missing or invalid")
    if not isinstance(games, int) or games < 0:
        raise ValueError("by-player manifest totals.games is missing or invalid")
    return {
        "metricVersion": METRIC_VERSION,
        "scope": SCOPE,
        "source": "docs/data/index/by-player/manifest.json",
        "includesBulk": True,
        "totals": {
            "players": registry_totals.get("players"),
            "withChineseName": registry_totals.get("withChineseName"),
            "playersWithGames": players_with_games,
            "games": games,
            "playerGameLinks": games,
            "archivedUniqueGames": fact_totals.get("games"),
            "legalArchivedUniqueGames": fact_totals.get("playableGames"),
            "excludedUniqueGames": fact_totals.get("excludedGames"),
            "playablePlayerGameLinks": fact_totals.get("playablePlayerGameLinks"),
            "playableUniqueGames": catalog.get("totals", {}).get("playableGames"),
        },
    }
