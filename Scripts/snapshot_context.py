#!/usr/bin/env python3
"""Shared snapshot identity for derived builds.

Every derived artifact (registry manifest, indexes, API, metrics, search)
must reference the same ``snapshotId`` so users never see totals from one
build mixed with profiles from another.  The sanctioned entry point is
``Scripts/build_release_snapshot.py`` which exports ``SNAPSHOT_ID`` once and
runs every builder under it; individual builders fall back to a fresh id
only for ad-hoc developer runs.
"""

from __future__ import annotations

import datetime as dt
import os
import secrets

_ENV_KEY = "SNAPSHOT_ID"
_cached: str | None = None


def snapshot_id() -> str:
    global _cached
    if _cached:
        return _cached
    value = os.environ.get(_ENV_KEY, "").strip()
    if not value:
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        value = f"{stamp}-{secrets.token_hex(4)}"
        os.environ[_ENV_KEY] = value
    _cached = value
    return value


def stamp(payload: dict) -> dict:
    """Attach snapshot identity fields to a manifest-style payload."""
    payload["snapshotId"] = snapshot_id()
    payload.setdefault(
        "generatedAt",
        dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
    )
    return payload
