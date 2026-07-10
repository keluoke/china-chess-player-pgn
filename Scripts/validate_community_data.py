#!/usr/bin/env python3
"""Offline validation of community-editable data files. Runs in CI on PRs.

NO network access: chess-results blocks GitHub datacenter IPs, so source
cross-verification happens locally (refresh.sh verify) — this script only
enforces structure and plausibility rules.
"""

from __future__ import annotations

import csv
import json
import pathlib
import re
import sys
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from apply_aliases_to_registry import sanitize_person_name  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

URL_DOMAIN_ALLOWLIST = {
    "chess-results.com",
    "s1.chess-results.com", "s2.chess-results.com", "s3.chess-results.com",
    "ratings.fide.com", "fide.com", "www.fide.com",
    "lichess.org",
    "cca.imsa.cn", "www.imsa.cn",
    "yunbisai.com", "www.yunbisai.com",
    "zh.wikipedia.org", "en.wikipedia.org",
}

FIDE_ID_RE = re.compile(r"^\d{5,10}$")
MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
FED_RE = re.compile(r"^[A-Z]{3}$")

errors: list[str] = []
warnings: list[str] = []


def err(path: pathlib.Path, line: int, msg: str) -> None:
    errors.append(f"{path.relative_to(REPO_ROOT)}:{line}: {msg}")


def warn(path: pathlib.Path, line: int, msg: str) -> None:
    warnings.append(f"{path.relative_to(REPO_ROOT)}:{line}: {msg}")


def url_ok(url: str) -> bool:
    if not url:
        return False
    try:
        host = urllib.parse.urlparse(url).hostname or ""
    except ValueError:
        return False
    return host in URL_DOMAIN_ALLOWLIST


def check_federation_overrides() -> None:
    path = REPO_ROOT / "data" / "community" / "federation-overrides.csv"
    if not path.exists():
        return
    seen: set[str] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        for i, row in enumerate(csv.DictReader(fh), start=2):
            fid = (row.get("fide_id") or "").strip()
            kind = (row.get("type") or "").strip().lower()
            if not fid and not kind:
                continue
            if not FIDE_ID_RE.match(fid):
                err(path, i, f"invalid fide_id {fid!r}")
            if fid in seen:
                err(path, i, f"duplicate fide_id {fid}")
            seen.add(fid)
            if kind not in {"transferred_out", "transferred_in"}:
                err(path, i, f"type must be transferred_out|transferred_in, got {kind!r}")
            for col in ("former_federation", "current_federation"):
                val = (row.get(col) or "").strip()
                if val and not FED_RE.match(val.upper()):
                    err(path, i, f"{col} must be a 3-letter code, got {val!r}")
            eff = (row.get("effective") or "").strip()
            if eff and not MONTH_RE.match(eff):
                err(path, i, f"effective must be YYYY-MM, got {eff!r}")
            ev = (row.get("evidence_url") or "").strip()
            if not url_ok(ev):
                err(path, i, f"evidence_url missing or domain not allow-listed: {ev!r}")


def check_player_aliases() -> None:
    path = REPO_ROOT / "data" / "manual" / "player-aliases.csv"
    if not path.exists():
        return
    seen: set[str] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        for i, row in enumerate(csv.DictReader(fh), start=2):
            fid = (row.get("fide_id") or "").strip()
            if not fid:
                continue
            if not FIDE_ID_RE.match(fid):
                err(path, i, f"invalid fide_id {fid!r}")
            if fid in seen:
                err(path, i, f"duplicate fide_id {fid}")
            seen.add(fid)
            cn = (row.get("chinese_name") or "").strip()
            if cn and not sanitize_person_name(cn):
                warn(path, i, f"chinese_name {cn!r} does not look like a person name (2-6 CJK chars)")


def check_sightings() -> None:
    path = REPO_ROOT / "data" / "manual" / "domestic-player-sightings.csv"
    if not path.exists():
        return
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        for i, row in enumerate(csv.DictReader(fh), start=2):
            if not any((v or "").strip() for v in row.values()):
                continue
            src = (row.get("source_url") or "").strip()
            if src and not url_ok(src):
                err(path, i, f"source_url domain not allow-listed: {src!r}")


def check_tournament_name_mappings() -> None:
    """Validate the reviewed event-name layer without touching crawler data."""
    path = REPO_ROOT / "data" / "community" / "tournament-name-mappings.csv"
    if not path.exists():
        return
    required = {"source", "tournament_id", "chinese_name", "evidence_url", "notes"}
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        actual = set(reader.fieldnames or [])
        missing = required - actual
        if missing:
            err(path, 1, f"missing required columns: {', '.join(sorted(missing))}")
            return
        seen: set[tuple[str, str]] = set()
        for i, row in enumerate(reader, start=2):
            if not any((value or "").strip() for value in row.values()):
                continue
            source = (row.get("source") or "").strip()
            tournament_id = (row.get("tournament_id") or "").strip()
            chinese_name = (row.get("chinese_name") or "").strip()
            if not source:
                err(path, i, "source required")
            if not tournament_id or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", tournament_id):
                err(path, i, f"invalid tournament_id {tournament_id!r}")
            if not (2 <= len(chinese_name) <= 100):
                err(path, i, "chinese_name must be 2-100 characters")
            key = (source.lower(), tournament_id)
            if key in seen:
                err(path, i, f"duplicate source+tournament_id {source!r}/{tournament_id!r}")
            seen.add(key)
            evidence = (row.get("evidence_url") or "").strip()
            if not url_ok(evidence):
                err(path, i, f"evidence_url missing or domain not allow-listed: {evidence!r}")


def check_contributors() -> None:
    """鸣谢名录:昵称合规、唯一,统计字段为非负整数,日期格式正确。"""
    path = REPO_ROOT / "data" / "community" / "contributors.csv"
    if not path.exists():
        return
    required = {"nickname", "github", "first_contribution", "last_contribution",
                "submissions", "players", "events", "games", "notes"}
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        missing = required - set(reader.fieldnames or [])
        if missing:
            err(path, 1, f"missing required columns: {', '.join(sorted(missing))}")
            return
        seen: set[str] = set()
        for i, row in enumerate(reader, start=2):
            if not any((value or "").strip() for value in row.values()):
                continue
            nickname = (row.get("nickname") or "").strip()
            if not (1 <= len(nickname) <= 20) or re.search(r"https?://", nickname):
                err(path, i, f"invalid nickname {nickname!r} (1-20 chars, no URL)")
            if nickname in seen:
                err(path, i, f"duplicate nickname {nickname!r}")
            seen.add(nickname)
            github = (row.get("github") or "").strip()
            if github and not re.fullmatch(r"[A-Za-z0-9-]{1,39}", github):
                err(path, i, f"invalid github login {github!r}")
            for col in ("first_contribution", "last_contribution"):
                value = (row.get(col) or "").strip()
                if value and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    err(path, i, f"invalid date in {col}: {value!r}")
            for col in ("submissions", "players", "events", "games"):
                value = (row.get(col) or "").strip()
                if value and not value.isdigit():
                    err(path, i, f"{col} must be a non-negative integer, got {value!r}")


def check_name_corrections_pinned() -> None:
    """Pinned identity assertions: once a name mistake is corrected via
    data/community/name-corrections.csv, no committed artifact may ever carry
    the wrong value again (regression guard for e.g. 8602980 居文君→侯逸凡)."""
    path = REPO_ROOT / "data" / "community" / "name-corrections.csv"
    if not path.exists():
        return
    corrections: dict[str, tuple[str, str]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        for i, row in enumerate(csv.DictReader(fh), start=2):
            fid = (row.get("fide_id") or "").strip()
            wrong = (row.get("wrong_chinese_name") or "").strip()
            correct = (row.get("correct_chinese_name") or "").strip()
            if not fid:
                continue
            if not FIDE_ID_RE.match(fid):
                err(path, i, f"invalid fide_id {fid!r}")
                continue
            if not correct:
                err(path, i, "correct_chinese_name required")
                continue
            if not url_ok((row.get("evidence_url") or "").strip()):
                err(path, i, "evidence_url missing or domain not allow-listed")
            corrections[fid] = (wrong, correct)

    for artifact in [
        REPO_ROOT / "docs" / "data" / "registry" / "players.json",
        REPO_ROOT / "docs" / "data" / "index" / "players.json",
    ]:
        if not artifact.exists():
            continue
        try:
            players = json.loads(artifact.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{artifact.relative_to(REPO_ROOT)}: unreadable ({exc})")
            continue
        for player in players:
            fid = str(player.get("fideID") or "").strip()
            pin = corrections.get(fid)
            if not pin:
                continue
            wrong, correct = pin
            cn = str(player.get("chineseName") or "").strip()
            if wrong and cn == wrong:
                errors.append(
                    f"{artifact.relative_to(REPO_ROOT)}: fide {fid} still carries corrected-away name {wrong!r} (must be {correct!r})"
                )


def check_generated_untouched_note() -> None:
    # Structural sanity of machine files (they can be regenerated, so only warn).
    path = REPO_ROOT / "data" / "generated" / "chess-results-player-name-map.csv"
    if path.exists():
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            header = fh.readline().strip()
        if not header.startswith("fide_id,"):
            warnings.append(f"{path.relative_to(REPO_ROOT)}: unexpected header {header!r}")


def main() -> int:
    check_federation_overrides()
    check_player_aliases()
    check_sightings()
    check_tournament_name_mappings()
    check_contributors()
    check_name_corrections_pinned()
    check_generated_untouched_note()

    print(json.dumps({"errors": len(errors), "warnings": len(warnings)}, ensure_ascii=False))
    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
