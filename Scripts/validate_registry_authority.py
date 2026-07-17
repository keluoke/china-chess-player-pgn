#!/usr/bin/env python3
"""Reject derived player identities that diverge from the registry.

This is a post-rebuild deployment gate.  Derived products may add event/game
metrics, but every player identity and current rating field must be copied from
``docs/data/registry/players.json``.  Missing registry values are authoritative
too, so an old non-empty output cannot survive by fallback merging.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any, Iterator


ROOT = pathlib.Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "docs" / "data" / "registry" / "players.json"
TARGETS = (
    ROOT / "docs" / "data" / "index" / "players.json",
    ROOT / "docs" / "data" / "index" / "by-player",
    ROOT / "docs" / "data" / "search-bootstrap.json",
    ROOT / "docs" / "data" / "leaderboards.json",
    ROOT / "data" / "generated" / "youth-leaderboards.json",
    ROOT / "docs" / "api" / "v1",
)
AUTHORITY_FIELDS = (
    "displayName", "name", "chineseName", "pinyin", "federation", "sex",
    "title", "womenTitle", "birthYear", "standard", "rapid", "blitz",
    "inactive", "formerFederation", "transfer",
)


def normalized(value: Any) -> Any:
    return None if value in (None, "", [], {}) else value


def walk(node: Any, location: str = "$") -> Iterator[tuple[str, dict[str, Any]]]:
    if isinstance(node, dict):
        yield location, node
        for key, value in node.items():
            yield from walk(value, f"{location}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from walk(value, f"{location}[{index}]")


def validate_document(
    payload: Any,
    registry: dict[str, dict[str, Any]],
    label: str,
) -> list[str]:
    errors: list[str] = []
    for location, row in walk(payload):
        fide_id = str(row.get("fideID") or "").strip()
        if not fide_id:
            continue
        authority = registry.get(fide_id)
        if authority is None:
            errors.append(f"{label}:{location}: FIDE {fide_id} absent from registry")
            continue
        for field in AUTHORITY_FIELDS:
            if field not in row:
                continue
            if normalized(row.get(field)) != normalized(authority.get(field)):
                errors.append(
                    f"{label}:{location}: FIDE {fide_id} {field} "
                    f"derived={row.get(field)!r} registry={authority.get(field)!r}"
                )
        if "aliases" in row:
            derived_aliases = {str(value).casefold() for value in row.get("aliases") or []}
            registry_aliases = {str(value).casefold() for value in authority.get("aliases") or []}
            extras = sorted(derived_aliases - registry_aliases)
            if extras:
                errors.append(
                    f"{label}:{location}: FIDE {fide_id} aliases contain non-registry values {extras[:5]!r}"
                )
    return errors


def json_targets() -> Iterator[pathlib.Path]:
    seen: set[pathlib.Path] = set()
    for target in TARGETS:
        paths = target.rglob("*.json") if target.is_dir() else [target]
        for path in paths:
            if path.is_file() and path not in seen:
                seen.add(path)
                yield path


def main() -> int:
    players = json.loads(REGISTRY.read_text(encoding="utf-8"))
    registry = {str(player.get("fideID") or ""): player for player in players}
    errors: list[str] = []
    checked = 0
    for path in json_targets():
        checked += 1
        payload = json.loads(path.read_text(encoding="utf-8"))
        errors.extend(validate_document(payload, registry, str(path.relative_to(ROOT))))
        if len(errors) >= 100:
            break
    if errors:
        for error in errors[:100]:
            print(f"REGISTRY_AUTHORITY_MISMATCH: {error}")
        return 1
    print(json.dumps({"ok": True, "registryPlayers": len(registry), "documents": checked}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
