#!/usr/bin/env python3
"""Validate target-only community submissions under ``data/incoming``.

Community submissions may contain URLs, tournament/FIDE identifiers, reasons
and priority hints.  They must never contain downloaded HTML/PGN, parsed rows,
cookies, response headers or any other product of automated collection.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
import urllib.parse
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
INCOMING = ROOT / "data" / "incoming"
SUB_ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")
FIDE_ID_RE = re.compile(r"^\d{4,10}$")
TNR_RE = re.compile(r"^\d{5,9}$")
ALLOWED_TYPES = {"event-target", "player-target", "source-clue", "quality-report"}
TOP_LEVEL_KEYS = {"schema", "createdAt", "contributor", "targets"}
CONTRIBUTOR_KEYS = {"nickname"}
TARGET_KEYS = {
    "type", "tournamentID", "fideID", "sourceURL", "evidenceURL",
    "reason", "priority", "eventName", "playerName", "notes",
}
FORBIDDEN_KEYS = {
    "files", "html", "raw", "pgn", "rows", "parsed", "response", "headers",
    "cookies", "snapshot", "payload", "games", "standings", "pairings",
}
ERRORS: list[str] = []


def error(submission: str, message: str) -> None:
    ERRORS.append(f"{submission}: {message}")


def clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def valid_url(value: str) -> bool:
    if not value:
        return True
    parsed = urllib.parse.urlparse(value)
    return parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password


def forbidden_content(value: Any) -> bool:
    if isinstance(value, dict):
        if any(clean(key).casefold() in FORBIDDEN_KEYS for key in value):
            return True
        return any(forbidden_content(item) for item in value.values())
    if isinstance(value, list):
        return any(forbidden_content(item) for item in value)
    if isinstance(value, str):
        sample = value.casefold()
        return "<html" in sample or '[event "' in sample or "__viewstate" in sample
    return False


def validate_target(submission: str, index: int, target: Any) -> None:
    prefix = f"target[{index}]"
    if not isinstance(target, dict):
        error(submission, f"{prefix} 必须是对象")
        return
    unknown = sorted(set(target) - TARGET_KEYS)
    if unknown:
        error(submission, f"{prefix} 包含非目标字段：{', '.join(unknown)}")
    kind = clean(target.get("type"))
    if kind not in ALLOWED_TYPES:
        error(submission, f"{prefix}.type 不支持：{kind}")
    tournament_id = clean(target.get("tournamentID"))
    fide_id = clean(target.get("fideID"))
    source_url = clean(target.get("sourceURL") or target.get("evidenceURL"))
    if tournament_id and not TNR_RE.fullmatch(tournament_id):
        error(submission, f"{prefix}.tournamentID 不合规")
    if fide_id and not FIDE_ID_RE.fullmatch(fide_id):
        error(submission, f"{prefix}.fideID 不合规")
    if kind == "event-target" and not tournament_id:
        error(submission, f"{prefix} 缺少 tournamentID")
    if kind == "player-target" and not fide_id:
        error(submission, f"{prefix} 缺少 fideID")
    if not valid_url(source_url):
        error(submission, f"{prefix} URL 必须为不带凭据的 https 地址")
    reason = clean(target.get("reason"))
    if len(reason) > 500:
        error(submission, f"{prefix}.reason 超过 500 字")
    try:
        priority = int(target.get("priority") or 0)
    except (TypeError, ValueError):
        priority = -1
    if priority not in range(0, 101):
        error(submission, f"{prefix}.priority 必须为 0-100")
    for key in ("eventName", "playerName", "notes"):
        if len(clean(target.get(key))) > 500:
            error(submission, f"{prefix}.{key} 超过 500 字")


def validate_submission(path: pathlib.Path) -> None:
    submission = path.name
    if not SUB_ID_RE.fullmatch(submission):
        error(submission, "目录名必须为 YYYYMMDD-HHMMSS-hex6")
        return
    manifest_path = path / "manifest.json"
    files = [item for item in path.rglob("*") if item.is_file() or item.is_symlink()]
    if files != [manifest_path] or manifest_path.is_symlink():
        error(submission, "目标提交只允许一个 manifest.json；禁止 HTML/PGN/解析结果附件")
        return
    if manifest_path.stat().st_size > 64 * 1024:
        error(submission, "manifest 超过 64 KiB")
        return
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        error(submission, f"manifest JSON 无效：{exc}")
        return
    if not isinstance(payload, dict):
        error(submission, "manifest 必须是 JSON 对象")
        return
    if payload.get("schema") != "china-chess-target-submission/v2":
        error(submission, "schema 必须为 china-chess-target-submission/v2")
    unknown = sorted(set(payload) - TOP_LEVEL_KEYS)
    if unknown:
        error(submission, f"manifest 包含非目标字段：{', '.join(unknown)}")
    if forbidden_content(payload):
        error(submission, "检测到抓取产物字段或 HTML/PGN 内容")
    contributor = payload.get("contributor") or {}
    if not isinstance(contributor, dict):
        error(submission, "contributor 必须是对象")
        contributor = {}
    contributor_unknown = sorted(set(contributor) - CONTRIBUTOR_KEYS)
    if contributor_unknown:
        error(submission, f"contributor 禁止字段：{', '.join(contributor_unknown)}")
    nickname = clean(contributor.get("nickname"))
    if nickname and (len(nickname) > 20 or "http" in nickname.casefold()):
        error(submission, "贡献者昵称不合规")
    if any(key in contributor for key in ("email", "phone", "wechat", "contact")):
        error(submission, "公开目标提交禁止联系方式；敏感线索请私下联系维护者")
    targets = payload.get("targets")
    if not isinstance(targets, list) or not 1 <= len(targets) <= 20:
        error(submission, "targets 必须包含 1-20 条线索")
        return
    for index, target in enumerate(targets):
        validate_target(submission, index, target)


def main() -> int:
    if not INCOMING.exists():
        print(json.dumps({"submissions": 0, "errors": 0}))
        return 0
    wanted = set(sys.argv[1:])
    directories = [
        path for path in sorted(INCOMING.iterdir())
        if path.is_dir() and (not wanted or path.name in wanted)
    ]
    for path in directories:
        validate_submission(path)
    for message in ERRORS:
        print(f"ERROR {message}", file=sys.stderr)
    print(json.dumps({"submissions": len(directories), "errors": len(ERRORS)}))
    return 1 if ERRORS else 0


if __name__ == "__main__":
    raise SystemExit(main())
