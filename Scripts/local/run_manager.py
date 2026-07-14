#!/usr/bin/env python3
"""Persistent local-run state and manifest-driven data delivery.

This module is stdlib-only and intentionally owns no scraper logic.  It gives
the shell entrypoint a cross-process lock, durable per-run state/log paths and
an exact allowlisted release manifest.  CI uses the same validator when it
applies a local-data release to main.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import uuid
from typing import Any

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from source_policy import local_state_root, source_release_metadata  # noqa: E402


MANIFEST_PATH = "data/generated/local-release-manifest.json"
FORBIDDEN_PREFIXES = (
    "data/community",
    "data/manual",
    "data/incoming",
    "data/generated/chess-results-event-snapshots",
)
RAW_SUFFIXES = (".html", ".html.gz", ".warc", ".warc.gz")
PUBLIC_RELEASE_PREFIXES = (
    "docs/data/registry",
    "docs/data/bulk",
    "data/generated/federation-snapshots",
    "data/generated/transfer-candidates.json",
)


class RunManagerError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def atomic_json(path: pathlib.Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def read_json(path: pathlib.Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else (default or {})
    except (OSError, json.JSONDecodeError):
        return default or {}


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def acquire(command: str, pid: int) -> pathlib.Path:
    root = local_state_root()
    runs = root / "runs"
    lock = root / "active.lock"
    runs.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    os.chmod(runs, 0o700)
    try:
        lock.mkdir()
    except FileExistsError:
        owner = read_json(lock / "owner.json")
        owner_pid = int(owner.get("pid") or 0)
        if process_alive(owner_pid):
            raise RunManagerError(
                "RUN_ALREADY_ACTIVE",
                f"已有任务 {owner.get('command') or '?'} 在运行（PID {owner_pid}）。",
            )
        shutil.rmtree(lock, ignore_errors=True)
        lock.mkdir()

    run_id = f"{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    run_dir = runs / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(run_dir, 0o700)
    for name in ("raw", "extracted", "staging", "diagnostics"):
        (run_dir / name).mkdir(parents=True, exist_ok=True)
        os.chmod(run_dir / name, 0o700)
    payload = {
        "schemaVersion": 1,
        "runId": run_id,
        "command": command,
        "pid": pid,
        "status": "running",
        "stage": "preflight",
        "result": None,
        "errorCode": None,
        "message": "运行环境检查中",
        "startedAt": now(),
        "finishedAt": None,
        "runDir": str(run_dir),
        "logPath": str(run_dir / "run.log"),
        "privateRoot": str(run_dir / "raw"),
        "releaseManifest": None,
    }
    atomic_json(run_dir / "run.json", payload)
    atomic_json(root / "current.json", payload)
    atomic_json(lock / "owner.json", {"runId": run_id, "command": command, "pid": pid, "startedAt": payload["startedAt"]})
    return run_dir


def update(run_dir: pathlib.Path, **values: Any) -> dict[str, Any]:
    payload = read_json(run_dir / "run.json")
    if not payload:
        raise RunManagerError("RUN_STATE_MISSING", f"找不到运行状态：{run_dir}")
    payload.update({key: value for key, value in values.items() if value is not None})
    payload["updatedAt"] = now()
    atomic_json(run_dir / "run.json", payload)
    atomic_json(local_state_root() / "current.json", payload)
    return payload


def finish(run_dir: pathlib.Path, code: int, result: str, error_code: str, message: str) -> dict[str, Any]:
    payload = update(
        run_dir,
        status="finished",
        stage="finished",
        result=result,
        returnCode=code,
        errorCode=error_code or None,
        message=message,
        finishedAt=now(),
    )
    lock = local_state_root() / "active.lock"
    owner = read_json(lock / "owner.json")
    if owner.get("runId") == payload.get("runId"):
        shutil.rmtree(lock, ignore_errors=True)
    prune_runs(keep=20)
    return payload


def prune_runs(keep: int) -> None:
    runs = local_state_root() / "runs"
    if not runs.exists():
        return
    entries = sorted((path for path in runs.iterdir() if path.is_dir()), reverse=True)
    cutoff = dt.datetime.now().timestamp() - 90 * 24 * 3600
    for index, path in enumerate(entries):
        if index >= max(keep, 1) or path.stat().st_mtime < cutoff:
            shutil.rmtree(path, ignore_errors=True)


def git(repo: pathlib.Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args], cwd=repo, check=check, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def worktree_status(repo: pathlib.Path) -> dict[str, dict[str, Any]]:
    raw = git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
    fields = raw.split(b"\0")
    result: dict[str, dict[str, Any]] = {}
    index = 0
    while index < len(fields):
        field = fields[index]
        index += 1
        if not field:
            continue
        text = field.decode("utf-8", errors="surrogateescape")
        if len(text) < 4:
            continue
        status, path = text[:2], text[3:]
        paths = [path]
        if "R" in status or "C" in status:
            if index < len(fields) and fields[index]:
                paths.append(fields[index].decode("utf-8", errors="surrogateescape"))
                index += 1
        for relative in paths:
            full = repo / relative
            info: dict[str, Any] = {"status": status}
            if full.is_file():
                info.update(sha256=sha256_file(full), bytes=full.stat().st_size)
            else:
                info.update(sha256=None, bytes=0)
            result[relative] = info
    return result


def within(path: str, roots: list[str]) -> bool:
    return any(path == root.rstrip("/") or path.startswith(root.rstrip("/") + "/") for root in roots)


def validate_release_path(path: str) -> None:
    if path.startswith("/") or ".." in pathlib.PurePosixPath(path).parts:
        raise RunManagerError("RELEASE_PATH_INVALID", f"非法发布路径：{path}")
    if within(path, list(FORBIDDEN_PREFIXES)):
        raise RunManagerError("RELEASE_PATH_FORBIDDEN", f"人工/社区/原始证据路径禁止自动发布：{path}")
    if path.lower().endswith(RAW_SUFFIXES):
        raise RunManagerError("RAW_SOURCE_PUBLICATION_BLOCKED", f"原始网页禁止进入发布包：{path}")


def preflight(repo: pathlib.Path, run_dir: pathlib.Path, allow: list[str]) -> None:
    allowed = [*allow, MANIFEST_PATH]
    staged = git(repo, "diff", "--cached", "--name-only", "-z").stdout
    if staged:
        paths = [p.decode("utf-8", "replace") for p in staged.split(b"\0") if p]
        raise RunManagerError("GIT_INDEX_NOT_CLEAN", f"Git 暂存区已有内容：{', '.join(paths[:5])}")
    baseline = worktree_status(repo)
    dirty_owned = [path for path in baseline if within(path, allowed)]
    if dirty_owned:
        raise RunManagerError(
            "DIRTY_RELEASE_PATH",
            "发布归属路径已有未提交修改，请先处理后再抓取：" + ", ".join(dirty_owned[:8]),
        )
    atomic_json(run_dir / "worktree-baseline.json", baseline)


def source_for_command(command: str) -> str:
    if command in {"registry", "all"}:
        return "fide"
    if command in {"bulk", "bulk-full"}:
        return "lichess"
    return "chess-results"


def prepare_release(repo: pathlib.Path, run_dir: pathlib.Path, command: str, allow: list[str]) -> dict[str, Any]:
    baseline = read_json(run_dir / "worktree-baseline.json")
    current = worktree_status(repo)
    allowed = [*allow, MANIFEST_PATH]
    outside_changes = [
        path for path in set(baseline) | set(current)
        if not within(path, allowed) and baseline.get(path) != current.get(path)
    ]
    if outside_changes:
        raise RunManagerError(
            "WORKTREE_CHANGED_DURING_RUN",
            "运行期间出现非发布路径改动，已停止交付：" + ", ".join(sorted(outside_changes)[:8]),
        )
    changed = sorted(path for path in current if within(path, allow))
    for path in changed:
        validate_release_path(path)
    if not changed:
        return {"changed": 0, "files": [], "manifest": None}

    files: list[dict[str, Any]] = []
    for path in changed:
        full = repo / path
        if full.is_symlink():
            raise RunManagerError("RELEASE_PATH_INVALID", f"发布包禁止符号链接：{path}")
        if full.is_file():
            files.append({"path": path, "operation": "upsert", "sha256": sha256_file(full), "bytes": full.stat().st_size})
        else:
            files.append({"path": path, "operation": "delete", "sha256": None, "bytes": 0})
    source = source_for_command(command)
    manifest = {
        "schemaVersion": 1,
        "runId": read_json(run_dir / "run.json").get("runId"),
        "command": command,
        "createdAt": now(),
        "baseCommit": git(repo, "rev-parse", "HEAD").stdout.decode().strip(),
        "source": source_release_metadata(source),
        "files": files,
    }
    validate_manifest(manifest)
    manifest_path = repo / MANIFEST_PATH
    atomic_json(manifest_path, manifest)
    for item in files:
        git(repo, "add", "-A", "--", item["path"])
    git(repo, "add", "--", MANIFEST_PATH)
    staged = [
        value.decode("utf-8", "surrogateescape")
        for value in git(repo, "diff", "--cached", "--name-only", "-z").stdout.split(b"\0")
        if value
    ]
    expected = sorted([*(item["path"] for item in files), MANIFEST_PATH])
    if sorted(staged) != expected:
        git(repo, "reset", "--quiet")
        raise RunManagerError("RELEASE_STAGE_MISMATCH", f"暂存文件与 manifest 不一致：{staged}")
    target = run_dir / "release-manifest.json"
    atomic_json(target, manifest)
    update(run_dir, releaseManifest=str(target), releaseFiles=len(files))
    return {"changed": len(files), "files": files, "manifest": str(target)}


def _parse_mapping(value: str) -> tuple[pathlib.Path, str]:
    if "::" not in value:
        raise RunManagerError("PROMOTION_MAPPING_INVALID", f"映射必须为 source::destination：{value}")
    source, destination = value.split("::", 1)
    return pathlib.Path(source).resolve(), destination.strip("/")


def promote_staging(
    repo: pathlib.Path,
    run_dir: pathlib.Path,
    trees: list[str],
    overlays: list[str],
    files: list[str],
) -> dict[str, Any]:
    promoted: list[str] = []

    def checked(value: str) -> tuple[pathlib.Path, pathlib.Path, str]:
        source, relative = _parse_mapping(value)
        try:
            source.relative_to(run_dir.resolve())
        except ValueError as error:
            raise RunManagerError("PROMOTION_SOURCE_INVALID", f"暂存源不在本次运行目录：{source}") from error
        validate_release_path(relative)
        return source, repo / relative, relative

    for value in trees:
        source, destination, relative = checked(value)
        if not source.is_dir():
            raise RunManagerError("PROMOTION_SOURCE_MISSING", f"暂存目录不存在：{source}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp = destination.parent / f".{destination.name}.promote-{os.getpid()}"
        backup = destination.parent / f".{destination.name}.backup-{os.getpid()}"
        shutil.rmtree(temp, ignore_errors=True)
        shutil.rmtree(backup, ignore_errors=True)
        shutil.copytree(source, temp)
        try:
            if destination.exists():
                os.replace(destination, backup)
            os.replace(temp, destination)
            shutil.rmtree(backup, ignore_errors=True)
        except Exception:
            if backup.exists() and not destination.exists():
                os.replace(backup, destination)
            shutil.rmtree(temp, ignore_errors=True)
            raise
        promoted.append(relative)

    for value in overlays:
        source, destination, relative = checked(value)
        if not source.is_dir():
            raise RunManagerError("PROMOTION_SOURCE_MISSING", f"暂存目录不存在：{source}")
        for source_file in sorted(path for path in source.rglob("*") if path.is_file()):
            suffix = source_file.relative_to(source)
            target = destination / suffix
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.parent / f".{target.name}.promote-{os.getpid()}"
            shutil.copy2(source_file, temp)
            os.replace(temp, target)
        promoted.append(relative)

    for value in files:
        source, destination, relative = checked(value)
        if not source.is_file():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp = destination.parent / f".{destination.name}.promote-{os.getpid()}"
        shutil.copy2(source, temp)
        os.replace(temp, destination)
        promoted.append(relative)
    return {"promoted": promoted}


def validate_manifest(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if payload.get("schemaVersion") != 1 or not isinstance(payload.get("files"), list):
        raise RunManagerError("RELEASE_MANIFEST_INVALID", "发布 manifest 格式无效。")
    if not payload["files"] or len(payload["files"]) > 50000:
        raise RunManagerError("RELEASE_MANIFEST_INVALID", "发布 manifest 文件数必须为 1-50000。")
    source = payload.get("source") or {}
    source_name = str(source.get("source") or "")
    if source_name == "Chess-Results":
        raise RunManagerError("COMPLIANCE_POLICY_BLOCKED", "Chess-Results 在当前架构中永不进入机器发布包。")
    if source_name not in {"FIDE Rating List", "Lichess Broadcasts"}:
        raise RunManagerError("RELEASE_SOURCE_UNSUPPORTED", f"机器发布不支持来源：{source_name or '?'}")
    if source_name == "FIDE Rating List" and source.get("releasePolicy") != "factual-registry-projection":
        raise RunManagerError("RELEASE_SOURCE_METADATA_INVALID", "FIDE 发布缺少事实注册表投影声明。")
    if source.get("source") == "Lichess Broadcasts" and (
        source.get("releasePolicy") != "cc-by-sa-4.0"
        or source.get("licenseURL") != "https://creativecommons.org/licenses/by-sa/4.0/"
        or not source.get("attributionURL")
    ):
        raise RunManagerError("RELEASE_LICENSE_MISSING", "Lichess 发布缺少 CC BY-SA 4.0 许可或署名信息。")
    files: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in payload["files"]:
        path = str(item.get("path") or "")
        if path in seen:
            raise RunManagerError("RELEASE_MANIFEST_INVALID", f"manifest 重复路径：{path}")
        seen.add(path)
        validate_release_path(path)
        if not within(path, list(PUBLIC_RELEASE_PREFIXES)):
            raise RunManagerError("RELEASE_PATH_FORBIDDEN", f"路径不在机器发布白名单：{path}")
        if source_name == "FIDE Rating List" and not within(
            path,
            ["docs/data/registry", "data/generated/federation-snapshots", "data/generated/transfer-candidates.json"],
        ):
            raise RunManagerError("RELEASE_SOURCE_PATH_MISMATCH", f"FIDE manifest 不能发布：{path}")
        if source_name == "Lichess Broadcasts" and not within(path, ["docs/data/bulk"]):
            raise RunManagerError("RELEASE_SOURCE_PATH_MISMATCH", f"Lichess manifest 不能发布：{path}")
        if item.get("operation") not in {"upsert", "delete"}:
            raise RunManagerError("RELEASE_MANIFEST_INVALID", f"非法文件操作：{path}")
        if item.get("operation") == "upsert":
            if not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256") or "")):
                raise RunManagerError("RELEASE_MANIFEST_INVALID", f"upsert 缺少有效 SHA-256：{path}")
            if not isinstance(item.get("bytes"), int) or int(item["bytes"]) < 0:
                raise RunManagerError("RELEASE_MANIFEST_INVALID", f"upsert 字节数无效：{path}")
        elif item.get("sha256") not in {None, ""}:
            raise RunManagerError("RELEASE_MANIFEST_INVALID", f"delete 不应携带 SHA-256：{path}")
        files.append(item)
    return files


# --- delivery outbox --------------------------------------------------------
# A release bundle is an immutable snapshot of "what must reach local-data":
# the exact manifest, the hashed file contents from the release commit, and a
# delivery state machine. Collection finishes as soon as the bundle exists;
# Git and API transports both consume the same bundle, so a GitHub failure
# never requires re-scraping and can never widen the file list.

OUTBOX_STATUSES = {
    "pending", "pushed", "ingested-to-main", "indexes-rebuilt", "deployed",
    "online-verified", "abandoned",
}


def outbox_root() -> pathlib.Path:
    root = local_state_root() / "outbox"
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    return root


def _outbox_write_bundle(repo: pathlib.Path, commit: str, manifest: dict[str, Any]) -> pathlib.Path:
    files = validate_manifest(manifest)
    run_id = str(manifest.get("runId") or "")
    if not run_id:
        raise RunManagerError("RELEASE_MANIFEST_INVALID", "manifest 缺少 runId，无法建立 outbox 包。")
    entry = outbox_root() / run_id
    files_dir = entry / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(entry, 0o700)
    for item in files:
        if item["operation"] != "upsert":
            continue
        path = item["path"]
        content = git(repo, "show", f"{commit}:{path}").stdout
        digest = hashlib.sha256(content).hexdigest()
        if digest != item.get("sha256"):
            raise RunManagerError("RELEASE_HASH_MISMATCH", f"outbox 内容与 manifest 哈希不符：{path}")
        target = files_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        tmp.write_bytes(content)
        os.replace(tmp, target)
        os.chmod(target, 0o600)
    atomic_json(entry / "manifest.json", manifest)
    atomic_json(entry / "delivery.json", {
        "schemaVersion": 1,
        "runId": run_id,
        "commit": commit,
        "status": "pending",
        "attempts": 0,
        "route": None,
        "remoteSHA": None,
        "lastError": None,
        "createdAt": now(),
        "updatedAt": now(),
    })
    prune_outbox()
    return entry


def outbox_save(repo: pathlib.Path, run_dir: pathlib.Path, commit: str) -> dict[str, Any]:
    manifest = read_json(run_dir / "release-manifest.json")
    if not manifest:
        raise RunManagerError("RELEASE_MANIFEST_MISSING", f"本次运行没有 release manifest：{run_dir}")
    entry = _outbox_write_bundle(repo, commit, manifest)
    return {"outbox": str(entry), "runId": manifest.get("runId"), "commit": commit}


def outbox_import(repo: pathlib.Path, commit: str) -> dict[str, Any]:
    """Import a pre-outbox committed manifest (legacy HEAD) as a bundle."""
    shown = git(repo, "show", f"{commit}:{MANIFEST_PATH}").stdout
    manifest = json.loads(shown.decode("utf-8"))
    entry = _outbox_write_bundle(repo, commit, manifest)
    return {"outbox": str(entry), "runId": manifest.get("runId"), "commit": commit}


def outbox_entries(status: str | None = None) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    root = outbox_root()
    for path in sorted(root.iterdir()):
        if not path.is_dir():
            continue
        delivery = read_json(path / "delivery.json")
        if not delivery:
            continue
        if status and delivery.get("status") != status:
            continue
        delivery["path"] = str(path)
        entries.append(delivery)
    entries.sort(key=lambda item: str(item.get("createdAt") or ""))
    return entries


def outbox_update(run_id: str, status: str | None, remote_sha: str | None,
                  route: str | None, error: str | None) -> dict[str, Any]:
    entry = outbox_root() / run_id
    delivery = read_json(entry / "delivery.json")
    if not delivery:
        raise RunManagerError("OUTBOX_ENTRY_MISSING", f"outbox 中没有 {run_id}")
    if status:
        if status not in OUTBOX_STATUSES:
            raise RunManagerError("OUTBOX_STATUS_INVALID", f"非法投递状态：{status}")
        delivery["status"] = status
    if remote_sha:
        delivery["remoteSHA"] = remote_sha
    if route:
        delivery["route"] = route
    delivery["lastError"] = error or None
    delivery["attempts"] = int(delivery.get("attempts") or 0) + (1 if (error or status == "pushed") else 0)
    delivery["updatedAt"] = now()
    atomic_json(entry / "delivery.json", delivery)
    return delivery


def prune_outbox(keep_delivered: int = 10) -> None:
    delivered = [item for item in outbox_entries() if item.get("status") not in {"pending"}]
    delivered.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
    for item in delivered[keep_delivered:]:
        shutil.rmtree(item["path"], ignore_errors=True)


def apply_release(repo: pathlib.Path, source_ref: str, path_list: pathlib.Path | None) -> dict[str, Any]:
    shown = git(repo, "show", f"{source_ref}:{MANIFEST_PATH}").stdout
    payload = json.loads(shown.decode("utf-8"))
    files = validate_manifest(payload)
    applied: list[str] = []
    for item in files:
        path = item["path"]
        target = repo / path
        if item["operation"] == "delete":
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
        else:
            git(repo, "checkout", source_ref, "--", path)
            if target.is_symlink():
                raise RunManagerError("RELEASE_PATH_INVALID", f"发布包禁止符号链接：{path}")
            if sha256_file(target) != item.get("sha256"):
                raise RunManagerError("RELEASE_HASH_MISMATCH", f"发布文件哈希不匹配：{path}")
        applied.append(path)
    git(repo, "checkout", source_ref, "--", MANIFEST_PATH)
    applied.append(MANIFEST_PATH)
    if path_list:
        path_list.write_text("\n".join(applied) + "\n", encoding="utf-8")
    return {"runId": payload.get("runId"), "applied": len(files), "paths": applied}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="action", required=True)
    acquire_p = sub.add_parser("acquire")
    acquire_p.add_argument("--command", required=True)
    acquire_p.add_argument("--pid", required=True, type=int)
    update_p = sub.add_parser("update")
    update_p.add_argument("--run-dir", required=True, type=pathlib.Path)
    update_p.add_argument("--stage")
    update_p.add_argument("--message")
    update_p.add_argument("--error-code")
    finish_p = sub.add_parser("finish")
    finish_p.add_argument("--run-dir", required=True, type=pathlib.Path)
    finish_p.add_argument("--code", required=True, type=int)
    finish_p.add_argument("--result", required=True)
    finish_p.add_argument("--error-code", default="")
    finish_p.add_argument("--message", default="")
    current_p = sub.add_parser("current")
    current_p.add_argument("--tail", type=int, default=24000)
    preflight_p = sub.add_parser("preflight")
    preflight_p.add_argument("--repo", required=True, type=pathlib.Path)
    preflight_p.add_argument("--run-dir", required=True, type=pathlib.Path)
    preflight_p.add_argument("--allow", action="append", default=[])
    prepare_p = sub.add_parser("prepare")
    prepare_p.add_argument("--repo", required=True, type=pathlib.Path)
    prepare_p.add_argument("--run-dir", required=True, type=pathlib.Path)
    prepare_p.add_argument("--command", required=True)
    prepare_p.add_argument("--allow", action="append", default=[])
    promote_p = sub.add_parser("promote")
    promote_p.add_argument("--repo", required=True, type=pathlib.Path)
    promote_p.add_argument("--run-dir", required=True, type=pathlib.Path)
    promote_p.add_argument("--tree", action="append", default=[])
    promote_p.add_argument("--overlay", action="append", default=[])
    promote_p.add_argument("--file", action="append", default=[])
    apply_p = sub.add_parser("apply")
    apply_p.add_argument("--repo", required=True, type=pathlib.Path)
    apply_p.add_argument("--source-ref", default="origin/local-data")
    apply_p.add_argument("--path-list", type=pathlib.Path)
    outbox_save_p = sub.add_parser("outbox-save")
    outbox_save_p.add_argument("--repo", required=True, type=pathlib.Path)
    outbox_save_p.add_argument("--run-dir", required=True, type=pathlib.Path)
    outbox_save_p.add_argument("--commit", required=True)
    outbox_import_p = sub.add_parser("outbox-import")
    outbox_import_p.add_argument("--repo", required=True, type=pathlib.Path)
    outbox_import_p.add_argument("--commit", required=True)
    outbox_list_p = sub.add_parser("outbox-list")
    outbox_list_p.add_argument("--status")
    outbox_list_p.add_argument("--plain", action="store_true", help="print runId<TAB>commit lines for shell use")
    outbox_update_p = sub.add_parser("outbox-update")
    outbox_update_p.add_argument("--run-id", required=True)
    outbox_update_p.add_argument("--status")
    outbox_update_p.add_argument("--remote-sha")
    outbox_update_p.add_argument("--route")
    outbox_update_p.add_argument("--error")
    return root


def current_payload(tail: int) -> dict[str, Any]:
    payload = read_json(local_state_root() / "current.json")
    log_path = pathlib.Path(str(payload.get("logPath") or ""))
    try:
        data = log_path.read_bytes()
        payload["log"] = data[-max(0, tail) :].decode("utf-8", errors="replace")
    except OSError:
        payload["log"] = ""
    pid = int(payload.get("pid") or 0)
    payload["running"] = payload.get("status") == "running" and process_alive(pid)
    return payload


def main() -> int:
    args = parser().parse_args()
    try:
        if args.action == "acquire":
            print(acquire(args.command, args.pid))
        elif args.action == "update":
            print(json.dumps(update(args.run_dir, stage=args.stage, message=args.message, errorCode=args.error_code), ensure_ascii=False))
        elif args.action == "finish":
            print(json.dumps(finish(args.run_dir, args.code, args.result, args.error_code, args.message), ensure_ascii=False))
        elif args.action == "current":
            print(json.dumps(current_payload(args.tail), ensure_ascii=False))
        elif args.action == "preflight":
            preflight(args.repo, args.run_dir, args.allow)
        elif args.action == "prepare":
            print(json.dumps(prepare_release(args.repo, args.run_dir, args.command, args.allow), ensure_ascii=False))
        elif args.action == "promote":
            print(json.dumps(promote_staging(args.repo, args.run_dir, args.tree, args.overlay, args.file), ensure_ascii=False))
        elif args.action == "apply":
            print(json.dumps(apply_release(args.repo, args.source_ref, args.path_list), ensure_ascii=False))
        elif args.action == "outbox-save":
            print(json.dumps(outbox_save(args.repo, args.run_dir, args.commit), ensure_ascii=False))
        elif args.action == "outbox-import":
            print(json.dumps(outbox_import(args.repo, args.commit), ensure_ascii=False))
        elif args.action == "outbox-list":
            entries = outbox_entries(args.status)
            if args.plain:
                for item in entries:
                    print(f"{item.get('runId')}\t{item.get('commit')}")
            else:
                print(json.dumps(entries, ensure_ascii=False, indent=2))
        elif args.action == "outbox-update":
            print(json.dumps(
                outbox_update(args.run_id, args.status, args.remote_sha, args.route, args.error),
                ensure_ascii=False,
            ))
        return 0
    except RunManagerError as error:
        print(json.dumps({"ok": False, "code": error.code, "message": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
