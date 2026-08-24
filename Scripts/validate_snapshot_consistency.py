#!/usr/bin/env python3
"""Gate: every public derived manifest must reference ONE snapshotId.

Runs as the final step of ``build_release_snapshot.py`` (which exports
``SNAPSHOT_ID``). If any derived public manifest carries a different id, the
snapshot aborts and nothing is committed — the previous snapshot keeps
serving as a whole; mixed references never publish (review §3.1).

Standalone runs (no SNAPSHOT_ID env) verify mutual consistency between the
manifests themselves.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
from typing import Any

from canonical_player_facts import load_fact_dataset, sha256_file

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

# Derived public manifests that must share one snapshot id. Registry
# manifests are collector inputs, not derived outputs, and are excluded.
MANIFEST_GLOBS = (
    "data/index/manifest.json",
    "data/index/by-player/manifest.json",
    "data/index/event-details/manifest.json",
    "data/index/public-events.json",
    "data/master-series-summary.json",
    "data/index/player-participation/manifest.json",
    "data/index/player-participation/buckets/*.json",
    "api/v1/manifest.json",
    "api/v2/manifest.json",
    "api/v2/rankings/official/current/*/*.json",
    "data/snapshot.json",
    "data/registry/domestic/manifest.json",
    "data/registry/domestic/presentation-groups.json",
    "data/registry/domestic/identity-quality.json",
    "data/identity/presentation-names.json",
    "data/search-bootstrap.json",
    "data/search-bootstrap-domestic.json",
    "data/search/domestic-routing.json",
    "data/search/domestic/*.json",
)

REQUIRED_PATHS = (
    "data/generated/player-event-facts/manifest.json",
    "data/generated/player-game-facts/manifest.json",
    "docs/data/index/by-player/manifest.json",
    "docs/data/index/by-player/players.json",
    "docs/data/index/player-participation/manifest.json",
    "docs/api/v1/manifest.json",
)

FACT_MANIFESTS = (
    ("data/generated/player-event-facts/manifest.json", "player-event-facts"),
    ("data/generated/player-game-facts/manifest.json", "player-game-facts"),
)


def collect() -> dict[str, str]:
    found: dict[str, str] = {}
    for pattern in MANIFEST_GLOBS:
        for path in sorted(DOCS.glob(pattern)):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                found[str(path.relative_to(ROOT))] = f"<unreadable: {error}>"
                continue
            sid = str(payload.get("snapshotId") or "").strip()
            found[str(path.relative_to(ROOT))] = sid or "<missing>"
    for relative, _kind in FACT_MANIFESTS:
        path = ROOT / relative
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            found[relative] = f"<unreadable: {error}>"
            continue
        found[relative] = str(payload.get("snapshotId") or "").strip() or "<missing>"
    return found


def missing_required_paths() -> list[str]:
    return [relative for relative in REQUIRED_PATHS if not (ROOT / relative).is_file()]


def _read(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _fact_reference_errors(
    payload: dict[str, Any],
    key: str,
    fact_path: pathlib.Path,
    fact_manifest: dict[str, Any],
    label: str,
) -> list[str]:
    reference = ((payload.get("factInputs") or {}).get(key) or {})
    errors = []
    if reference.get("sha256") != sha256_file(fact_path):
        errors.append(f"{label} does not reference current {key} manifest hash")
    if reference.get("snapshotId") != fact_manifest.get("snapshotId"):
        errors.append(f"{label} does not reference current {key} snapshot")
    if reference.get("rows") != fact_manifest.get("rows"):
        errors.append(f"{label} does not reference current {key} row count")
    return errors


def player_fact_coverage_errors() -> list[str]:
    """Cross-check canonical facts against registry and every main consumer."""
    errors: list[str] = []
    event_path = ROOT / FACT_MANIFESTS[0][0]
    game_path = ROOT / FACT_MANIFESTS[1][0]
    try:
        event_facts, event_manifest = load_fact_dataset(event_path, "player-event-facts")
        game_facts, game_manifest = load_fact_dataset(game_path, "player-game-facts")
    except RuntimeError as error:
        return [str(error)]

    registry_path = ROOT / "docs/data/registry/players.json"
    if not registry_path.is_file():
        return [f"required registry missing: {registry_path.relative_to(ROOT)}"]
    registry_payload = _read(registry_path)
    registry_rows = registry_payload.get("players") if isinstance(registry_payload, dict) else registry_payload
    registry_ids = {
        str(row.get("fideID") or "").strip()
        for row in registry_rows or []
        if isinstance(row, dict) and str(row.get("fideID") or "").strip()
    }

    event_keys: set[tuple[str, str]] = set()
    for fact in event_facts:
        fide_id = str(fact.get("fideID") or "").strip()
        tid = str(fact.get("tournamentID") or "").strip()
        if not fide_id or fide_id not in registry_ids:
            errors.append(f"player-event fact outside registry: {fide_id or '<missing>'}")
        key = (fide_id, tid)
        if not tid or key in event_keys:
            errors.append(f"duplicate/invalid player-event fact: {key}")
        event_keys.add(key)

    game_ids: set[str] = set()
    game_counts: dict[str, int] = {}
    for fact in game_facts:
        game_id = str(fact.get("fingerprint") or fact.get("id") or "").strip()
        if not game_id or game_id in game_ids:
            errors.append(f"duplicate/invalid player-game fact: {game_id or '<missing>'}")
        game_ids.add(game_id)
        player_ids = {str(value).strip() for value in fact.get("playerFideIDs") or [] if str(value).strip()}
        if not player_ids and fact.get("linkStatus") != "unlinked":
            errors.append(f"player-game fact lacks explicit unlinked status: {game_id}")
        for fide_id in player_ids:
            if fide_id not in registry_ids:
                errors.append(f"player-game fact outside registry: {fide_id}")
            game_counts[fide_id] = game_counts.get(fide_id, 0) + 1

    by_player_manifest_path = ROOT / "docs/data/index/by-player/manifest.json"
    by_player_players_path = ROOT / "docs/data/index/by-player/players.json"
    participation_path = ROOT / "docs/data/index/player-participation/manifest.json"
    api_path = ROOT / "docs/api/v1/manifest.json"
    try:
        by_player = _read(by_player_manifest_path)
        player_rows = _read(by_player_players_path)
        participation = _read(participation_path)
        api = _read(api_path)
    except (OSError, json.JSONDecodeError) as error:
        return [*errors, f"required fact consumer invalid: {error}"]

    errors.extend(_fact_reference_errors(
        by_player, "playerEvents", event_path, event_manifest, "by-player manifest",
    ))
    errors.extend(_fact_reference_errors(
        by_player, "playerGames", game_path, game_manifest, "by-player manifest",
    ))
    errors.extend(_fact_reference_errors(
        participation, "playerEvents", event_path, event_manifest, "participation manifest",
    ))
    errors.extend(_fact_reference_errors(
        api, "playerEvents", event_path, event_manifest, "API manifest",
    ))
    errors.extend(_fact_reference_errors(
        api, "playerGames", game_path, game_manifest, "API manifest",
    ))

    expected_links = sum(game_counts.values())
    expected_players = len(game_counts)
    by_totals = by_player.get("totals") or {}
    if by_totals.get("games") != expected_links or by_totals.get("players") != expected_players:
        errors.append(
            "by-player totals do not cover player-game facts: "
            f"expected players/games {expected_players}/{expected_links}, "
            f"got {by_totals.get('players')}/{by_totals.get('games')}"
        )
    projected = {
        str(row.get("fideID") or "").strip(): int(row.get("gameCount") or 0)
        for row in player_rows or [] if isinstance(row, dict)
    }
    if projected != game_counts:
        errors.append("by-player player counts do not exactly cover canonical game facts")
    api_totals = api.get("totals") or {}
    if api_totals.get("withGameData") != expected_players or api_totals.get("games") != expected_links:
        errors.append(
            "API totals do not cover player-game facts: "
            f"expected {expected_players}/{expected_links}, "
            f"got {api_totals.get('withGameData')}/{api_totals.get('games')}"
        )
    return errors


def event_catalog_detail_issues() -> list[str]:
    """Require a one-to-one published detail contract for every manifest row.

    Merely finding the same TNR somewhere in the catalog is insufficient: the
    old tnr58153 collision passed that weak set-membership check while its only
    public row had ``detailStatus=missing-detail``.  Match the exact path and
    require one manifest row and one published catalog row in both directions.
    """
    detail_manifest = DOCS / "data/index/event-details/manifest.json"
    public_catalog = DOCS / "data/index/public-events.json"
    if not detail_manifest.is_file() or not public_catalog.is_file():
        return []
    try:
        details = json.loads(detail_manifest.read_text(encoding="utf-8"))
        public = json.loads(public_catalog.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return [f"<unreadable event catalog: {error}>"]
    detail_rows: dict[str, list[dict]] = {}
    for item in details.get("events", []):
        tournament_id = str(item.get("tournamentID") or "").strip()
        if tournament_id:
            detail_rows.setdefault(tournament_id, []).append(item)
    public_rows: dict[str, list[dict]] = {}
    for item in public.get("events", []):
        tournament_id = str(item.get("tournamentID") or "").strip()
        if tournament_id:
            public_rows.setdefault(tournament_id, []).append(item)

    issues: list[str] = []
    for tournament_id, manifest_rows in detail_rows.items():
        if len(manifest_rows) != 1:
            issues.append(f"tnr{tournament_id}: detail manifest rows={len(manifest_rows)}, expected=1")
            continue
        expected_path = str(manifest_rows[0].get("path") or "").strip()
        if not expected_path:
            issues.append(f"tnr{tournament_id}: detail manifest path is missing")
            continue
        published_rows = [
            item for item in public_rows.get(tournament_id, [])
            if item.get("detailStatus") == "published" and str(item.get("detailPath") or "").strip()
        ]
        if len(published_rows) != 1:
            issues.append(
                f"tnr{tournament_id}: published detailPath rows={len(published_rows)}, expected=1"
            )
            continue
        actual_path = str(published_rows[0].get("detailPath") or "").strip()
        if actual_path != expected_path:
            issues.append(
                f"tnr{tournament_id}: detailPath={actual_path!r}, expected={expected_path!r}"
            )

    manifest_ids = set(detail_rows)
    for tournament_id, rows in public_rows.items():
        for item in rows:
            if item.get("detailStatus") != "published" and not item.get("detailPath"):
                continue
            if tournament_id not in manifest_ids:
                issues.append(f"tnr{tournament_id}: published detailPath has no manifest row")
    return sorted(set(issues))


def event_catalog_gaps() -> list[str]:
    """Compatibility projection used by older callers and diagnostics."""
    return event_catalog_detail_issues()


def main() -> int:
    expected = os.environ.get("SNAPSHOT_ID", "").strip()
    missing = missing_required_paths()
    if missing:
        print("SNAPSHOT CONSISTENCY FAILED — required manifests/data missing:", file=sys.stderr)
        for path in missing:
            print(f"  - {path}", file=sys.stderr)
        return 1
    found = collect()
    if not found:
        print("no derived manifests found; nothing to validate")
        return 0
    reference = expected or next(
        (sid for sid in found.values() if sid and not sid.startswith("<")), ""
    )
    mismatched = {
        path: sid for path, sid in found.items()
        if sid != reference
    }
    if mismatched:
        print("SNAPSHOT CONSISTENCY FAILED — mixed snapshot references:", file=sys.stderr)
        print(f"  expected: {reference or '<none>'}", file=sys.stderr)
        for path, sid in sorted(mismatched.items()):
            print(f"  - {path}: {sid}", file=sys.stderr)
        return 1
    detail_issues = event_catalog_detail_issues()
    if detail_issues:
        print(
            "SNAPSHOT CONSISTENCY FAILED — event detail/catalog one-to-one contract:",
            file=sys.stderr,
        )
        for issue in detail_issues[:30]:
            print(f"  - {issue}", file=sys.stderr)
        return 1
    coverage_errors = player_fact_coverage_errors()
    if coverage_errors:
        print("SNAPSHOT CONSISTENCY FAILED — canonical player fact coverage:", file=sys.stderr)
        for error in coverage_errors[:50]:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "snapshotId": reference, "manifests": len(found)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
