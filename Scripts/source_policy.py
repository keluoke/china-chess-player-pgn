#!/usr/bin/env python3
"""Shared source and local-storage policy for every network collector.

Network collection is a maintainer-only local operation.  Chess-Results is
full-data by default: cleaned, structured event data (players, pairings,
results, standings, PGN) is published through the manifest pipeline for data
completeness.  Raw HTML responses always stay in the private run area outside
the Git worktree.  The legacy ``link-only`` mode can still be forced via the
``CHESS_RESULTS_RELEASE_POLICY`` environment variable.
"""

from __future__ import annotations

import os
import pathlib
import platform


LOCAL_ACK_ENV = "CHINA_CHESS_MAINTAINER_LOCAL"
CHESS_RESULTS_POLICY_ENV = "CHESS_RESULTS_RELEASE_POLICY"
FULL_DATA_VALUE = "full-data"
# Legacy alias kept for compatibility with old environments/manifests.
AUTHORIZED_VALUE = "authorized"


class SourcePolicyError(RuntimeError):
    """A collection or publication operation violates the configured policy."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


def local_state_root() -> pathlib.Path:
    override = os.environ.get("CHINA_CHESS_LOCAL_ROOT", "").strip()
    if override:
        return pathlib.Path(override).expanduser().resolve()
    if platform.system() == "Darwin":
        return pathlib.Path.home() / "Library" / "Application Support" / "ChinaChessPlayerPGN"
    xdg = os.environ.get("XDG_STATE_HOME", "").strip()
    if xdg:
        return pathlib.Path(xdg).expanduser() / "china-chess-player-pgn"
    return pathlib.Path.home() / ".local" / "state" / "china-chess-player-pgn"


def require_local_collector(provider: str) -> None:
    """Require an explicit acknowledgement before any source is contacted."""
    if os.environ.get(LOCAL_ACK_ENV) == "1":
        return
    raise SourcePolicyError(
        "LOCAL_MAINTAINER_ACK_REQUIRED",
        f"{provider} 采集只允许维护者本地运行；请使用 Scripts/local/refresh.sh，"
        f"或显式设置 {LOCAL_ACK_ENV}=1。",
    )


def chess_results_release_policy() -> str:
    value = os.environ.get(CHESS_RESULTS_POLICY_ENV, FULL_DATA_VALUE).strip().lower()
    if value == AUTHORIZED_VALUE:
        return FULL_DATA_VALUE
    return value if value in {"link-only", FULL_DATA_VALUE} else FULL_DATA_VALUE


def require_chess_results_publication() -> None:
    """Full-data publication is the default; only explicit link-only blocks it."""
    if chess_results_release_policy() == FULL_DATA_VALUE:
        return
    raise SourcePolicyError(
        "COMPLIANCE_POLICY_BLOCKED",
        "Chess-Results 已被环境显式设为 link-only：结构化赛事数据本次不发布，"
        "原始页面仍只保存在本地私有运行区。",
    )


def source_release_metadata(source: str) -> dict[str, str]:
    normalized = source.strip().lower()
    if normalized == "lichess":
        return {
            "source": "Lichess Broadcasts",
            "releasePolicy": "cc-by-sa-4.0",
            "license": "Creative Commons Attribution-ShareAlike 4.0",
            "licenseURL": "https://creativecommons.org/licenses/by-sa/4.0/",
            "attributionURL": "https://database.lichess.org/",
        }
    if normalized == "fide":
        return {
            "source": "FIDE Rating List",
            "releasePolicy": "factual-registry-projection",
            "sourceURL": "https://ratings.fide.com/download_lists.phtml",
        }
    if normalized == "chess-results":
        return {
            "source": "Chess-Results",
            "releasePolicy": chess_results_release_policy(),
            "sourceURL": "https://chess-results.com/",
        }
    return {"source": source, "releasePolicy": "review-required"}
