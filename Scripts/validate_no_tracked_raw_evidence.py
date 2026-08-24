#!/usr/bin/env python3
"""Fail CI if Git tracks raw HTML/WARC capture evidence."""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
from collections.abc import Iterable

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
PUBLIC_SITE_HTML = frozenset({
    "docs/contribute.html",
    "docs/coverage.html",
    "docs/events.html",
    "docs/index.html",
    "docs/leaderboards.html",
    "docs/master-series.html",
})
SYNTHETIC_FIXTURE_HTML = frozenset({
    "Scripts/tests/fixtures/chess_results/empty_event.html",
    "Scripts/tests/fixtures/chess_results/pairings_missing_refs.html",
    "Scripts/tests/fixtures/chess_results/pairings_names_only.html",
    "Scripts/tests/fixtures/chess_results/pairings_out_of_roster.html",
    "Scripts/tests/fixtures/chess_results/pairings_round.html",
    "Scripts/tests/fixtures/chess_results/pairings_shifted.html",
    "Scripts/tests/fixtures/chess_results/standings_individual.html",
    "Scripts/tests/fixtures/chess_results/standings_no_rounds.html",
    "Scripts/tests/fixtures/chess_results/starting_rank_domestic_minimal.html",
    "Scripts/tests/fixtures/chess_results/starting_rank_individual.html",
    "Scripts/tests/fixtures/chess_results/team_player_list.html",
    "Scripts/tests/fixtures/chess_results/team_round.html",
    "Scripts/tests/fixtures/chess_results/tournament_details.html",
})
EVIDENCE_SUFFIXES = (
    ".html", ".htm", ".html.gz", ".htm.gz", ".html.zst", ".htm.zst",
    ".warc", ".warc.gz", ".warc.zst",
)
RAW_FIXTURE_MARKERS = ("chess-results.com", "__viewstate", "cloudflare ray id")
MAX_SYNTHETIC_FIXTURE_BYTES = 16 * 1024


def tracked_evidence_files(repo_root: pathlib.Path = REPO_ROOT) -> tuple[str, ...]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=repo_root, check=True, capture_output=True,
    )
    paths = result.stdout.decode("utf-8", "surrogateescape").split("\0")
    return tuple(sorted(
        path for path in paths
        if path and path.casefold().endswith(EVIDENCE_SUFFIXES)
    ))


def violations(repo_root: pathlib.Path, tracked: Iterable[str]) -> list[str]:
    failures: list[str] = []
    for relative in sorted(set(tracked)):
        lowered = relative.casefold()
        if lowered.endswith((".warc", ".warc.gz", ".warc.zst")):
            failures.append(f"{relative}: tracked WARC capture is forbidden")
            continue
        if relative in PUBLIC_SITE_HTML:
            continue
        if relative not in SYNTHETIC_FIXTURE_HTML:
            failures.append(f"{relative}: HTML is not an approved product page or synthetic fixture")
            continue
        path = repo_root / pathlib.PurePosixPath(relative)
        try:
            body = path.read_bytes()
        except OSError as exc:
            failures.append(f"{relative}: cannot inspect approved fixture ({exc})")
            continue
        if len(body) > MAX_SYNTHETIC_FIXTURE_BYTES:
            failures.append(f"{relative}: synthetic fixture exceeds {MAX_SYNTHETIC_FIXTURE_BYTES} bytes")
            continue
        sample = body.decode("utf-8", "replace").casefold()
        marker = next((item for item in RAW_FIXTURE_MARKERS if item in sample), None)
        if marker:
            failures.append(f"{relative}: fixture contains raw-capture marker {marker!r}")
    return failures


def main() -> int:
    tracked = tracked_evidence_files()
    failures = violations(REPO_ROOT, tracked)
    if failures:
        print("TRACKED_RAW_EVIDENCE_FORBIDDEN")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(json.dumps({
        "trackedRawEvidence": 0,
        "approvedProductHTML": sum(path in PUBLIC_SITE_HTML for path in tracked),
        "syntheticParserFixtures": sum(path in SYNTHETIC_FIXTURE_HTML for path in tracked),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
