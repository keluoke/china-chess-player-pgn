#!/usr/bin/env python3
"""Deterministic JSON writer for derived artifacts.

When only a top-level ``generatedAt`` value changed, retain the previous value
and bytes. This prevents no-op offline rebuilds from creating thousands of
timestamp-only diffs while still advancing freshness whenever payload content
actually changes.
"""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any


def preserve_generated_at(path: pathlib.Path, data: Any) -> Any:
    if not path.exists() or not isinstance(data, dict) or "generatedAt" not in data:
        return data
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return data
    if not isinstance(previous, dict) or not previous.get("generatedAt"):
        return data
    old_body = {key: value for key, value in previous.items() if key != "generatedAt"}
    new_body = {key: value for key, value in data.items() if key != "generatedAt"}
    if old_body != new_body:
        return data
    stable = dict(data)
    stable["generatedAt"] = previous["generatedAt"]
    return stable


def write_json(
    path: pathlib.Path,
    data: Any,
    *,
    ensure_ascii: bool = False,
    indent: int | None = None,
    separators: tuple[str, str] | None = None,
    sort_keys: bool = False,
) -> bool:
    payload = preserve_generated_at(path, data)
    text = json.dumps(
        payload,
        ensure_ascii=ensure_ascii,
        indent=indent,
        separators=separators,
        sort_keys=sort_keys,
    ) + "\n"
    try:
        if path.read_text(encoding="utf-8") == text:
            return False
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)
    return True
