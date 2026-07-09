#!/usr/bin/env python3
"""Unified age-group rules for the whole pipeline.

Single source of truth consumed by leaderboards, by-player PGN
segmentation and the static API. Age basis is natural age:
``reference year - birth year`` (李成智杯口径 for youth groups).

Youth groups (U8-U18) keep the historical two-year brackets. Adult
dimensions extend the same axis:

  U20    19-20
  OPEN   >=19  (成年公开组; every adult, also the S50/S65 players)
  S50    >=50  (FIDE senior 口径)
  S65    >=65

Game-level PGN segmentation uses ``stage_for_age`` which returns one
exclusive stage per game: U8..U18 for youth ages, ``adult`` for 19+.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

YOUTH_STAGES: list[tuple[str, int, int]] = [
    ("U8", 7, 8),
    ("U10", 9, 10),
    ("U12", 11, 12),
    ("U14", 13, 14),
    ("U16", 15, 16),
    ("U18", 17, 18),
]

ADULT_STAGE = "adult"

LEADERBOARD_GROUPS: list[dict[str, Any]] = [
    *[{"id": s, "label": s, "minAge": lo, "maxAge": hi} for s, lo, hi in YOUTH_STAGES],
    {"id": "U20", "label": "U20", "minAge": 19, "maxAge": 20},
    {"id": "OPEN", "label": "成年公开", "minAge": 19, "maxAge": None},
    {"id": "S50", "label": "元老 S50", "minAge": 50, "maxAge": None},
    {"id": "S65", "label": "元老 S65", "minAge": 65, "maxAge": None},
]


def reference_year(today: dt.date | None = None) -> int:
    return (today or dt.date.today()).year


def age_of(birth_year: int | None, ref_year: int | None = None) -> int | None:
    if not birth_year:
        return None
    return (ref_year or reference_year()) - int(birth_year)


def stage_for_age(age: int | None) -> str:
    """Exclusive PGN-segmentation stage for a game played at this age."""
    if age is None:
        return ""
    for stage, lower, upper in YOUTH_STAGES:
        if lower <= age <= upper:
            return stage
    if age > YOUTH_STAGES[-1][2]:
        return ADULT_STAGE
    return ""


def groups_for_age(age: int | None) -> list[str]:
    """All leaderboard groups this age belongs to (groups may overlap)."""
    if age is None:
        return []
    out = []
    for group in LEADERBOARD_GROUPS:
        lo = group["minAge"]
        hi = group["maxAge"]
        if age >= lo and (hi is None or age <= hi):
            out.append(group["id"])
    return out
