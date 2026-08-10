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
import time
import uuid
from typing import Any

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from source_policy import local_state_root, source_release_metadata  # noqa: E402


MANIFEST_PATH = "data/generated/local-release-manifest.json"
CHECKOUT_BATCH_SIZE = 256
PREFETCH_ATTEMPTS = 3
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
    "data/generated/chess-results-event-details",
    "data/generated/chess-results-event-pgn",
    "data/generated/pgn-source-attempts",
    "docs/data/pgn/chess-results",
    "data/generated/person-observations.csv",
    "data/generated/person-observations.meta.json",
    "data/generated/pgn-collection-status.json",
    "data/generated/event-completeness-report.json",
    "data/generated/pgn-supplement-queue.json",
    "data/generated/r2-object-receipts/events--chess-results.json",
)
CHESS_RESULTS_RELEASE_PREFIXES = (
    "data/generated/chess-results-event-details",
    "data/generated/chess-results-event-pgn",
    "data/generated/pgn-source-attempts",
    "docs/data/pgn/chess-results",
    "data/generated/person-observations.csv",
    "data/generated/person-observations.meta.json",
    "data/generated/pgn-collection-status.json",
    "data/generated/event-completeness-report.json",
    "data/generated/pgn-supplement-queue.json",
    "data/generated/r2-object-receipts/events--chess-results.json",
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


FINAL_ERROR_CODE_RE = re.compile(r"(?:❌\s*)?([A-Z][A-Z0-9_]{2,})(?=\s*[:：])")


def inferred_final_error_code(log: str) -> str:
    """Return the last structured error code emitted before an unclean exit."""
    matches = FINAL_ERROR_CODE_RE.findall(log)
    return matches[-1] if matches else ""


def requested_event_targets(arguments: list[str] | None) -> list[str]:
    """Extract explicit TNRs for durable preflight/error reporting.

    Queue selectors such as ``--from-queue 10`` deliberately do not become a
    fake tournament ID. The collector's result.json remains authoritative
    once target selection has actually run.
    """
    requested: list[str] = []
    for token in arguments or []:
        value = str(token).strip()
        direct = re.fullmatch(r"(?:tnr)?(\d{5,10})", value, re.IGNORECASE)
        linked = re.search(r"(?:^|/)tnr(\d{5,10})\.aspx(?:[?#]|$)", value, re.IGNORECASE)
        matched = direct or linked
        if matched and matched.group(1) not in requested:
            requested.append(matched.group(1))
    return requested


def acquire(
    command: str,
    pid: int,
    request_arguments: list[str] | None = None,
) -> pathlib.Path:
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
        "requestArguments": list(request_arguments or []) if command == "event-queue" else [],
        "requested": requested_event_targets(request_arguments) if command == "event-queue" else [],
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
    prune_runs()
    return payload


def prune_runs(keep_per_command: int = 5, keep_recent: int = 30) -> None:
    """Prune diagnostics without erasing a whole command's recent history."""
    runs = local_state_root() / "runs"
    if not runs.exists():
        return
    entries = sorted((path for path in runs.iterdir() if path.is_dir()), reverse=True)
    retained = set(entries[:max(keep_recent, 1)])
    buckets: dict[str, int] = {}
    for path in entries:
        command = str(read_json(path / "run.json").get("command") or "unknown")
        if buckets.get(command, 0) < max(keep_per_command, 1):
            retained.add(path)
            buckets[command] = buckets.get(command, 0) + 1
    for path in entries:
        if path not in retained:
            shutil.rmtree(path, ignore_errors=True)


def record_error(
    run_dir: pathlib.Path,
    stage: str,
    code: str,
    message: str,
    hint: str = "",
    evidence: str = "",
) -> dict[str, Any]:
    payload = {
        "schemaVersion": 1,
        "stage": stage or "unknown",
        "code": code or "UNEXPECTED_FAILURE",
        "message": message,
        "hint": hint or None,
        "evidence": evidence or None,
        "recordedAt": now(),
    }
    atomic_json(run_dir / "error.json", payload)
    return payload


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


def git_blob_oid(repo: pathlib.Path, ref: str, path: str) -> str | None:
    try:
        raw = git(repo, "ls-tree", "-z", ref, "--", path).stdout
    except subprocess.CalledProcessError as error:
        raise RunManagerError(
            "RELEASE_BASE_UNAVAILABLE",
            f"无法读取 Git 树 {ref}:{path}: {error.stderr.decode('utf-8', errors='replace').strip()}",
        ) from error
    records = [record for record in raw.split(b"\0") if record]
    if not records:
        return None
    if len(records) != 1:
        raise RunManagerError("RELEASE_PATH_INVALID", f"{ref}:{path} 解析为多个 Git 对象。")
    metadata, separator, recorded_path = records[0].partition(b"\t")
    fields = metadata.split()
    if (
        not separator
        or recorded_path.decode("utf-8", errors="surrogateescape") != path
        or len(fields) != 3
        or fields[1] != b"blob"
        or fields[0] not in {b"100644", b"100755"}
    ):
        raise RunManagerError("RELEASE_PATH_INVALID", f"{ref}:{path} 不是普通 Git blob。")
    return fields[2].decode("ascii", errors="strict")


def git_blob_sha256(repo: pathlib.Path, oid: str) -> str:
    digest = hashlib.sha256()
    process = subprocess.Popen(
        ["git", "cat-file", "blob", oid],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdout is not None
    for chunk in iter(lambda: process.stdout.read(1024 * 1024), b""):
        digest.update(chunk)
    process.stdout.close()
    stderr = process.stderr.read() if process.stderr is not None else b""
    if process.stderr is not None:
        process.stderr.close()
    if process.wait() != 0:
        raise RunManagerError(
            "RELEASE_BASE_UNAVAILABLE",
            f"无法读取基线 blob {oid}: {stderr.decode('utf-8', errors='replace').strip()}",
        )
    return digest.hexdigest()


def git_blob_facts(repo: pathlib.Path, ref: str, path: str) -> tuple[str | None, str | None]:
    oid = git_blob_oid(repo, ref, path)
    return (oid, git_blob_sha256(repo, oid)) if oid else (None, None)


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


def ignored_machine_files(repo: pathlib.Path, allowed: list[str]) -> dict[str, dict[str, Any]]:
    """Enumerate ignored files only inside validated machine-release roots.

    Event details/archives are intentionally ignored to keep ordinary Git
    status usable on the collector.  They must nevertheless enter the exact
    release manifest; otherwise a workstation can hold 933 complete events
    while cloud ingest receives only the small historically tracked subset.
    """
    roots = [root for root in allowed if (repo / root).exists()]
    if not roots:
        return {}
    raw = git(
        repo, "ls-files", "--others", "--ignored", "--exclude-standard", "-z", "--", *roots
    ).stdout
    result: dict[str, dict[str, Any]] = {}
    for value in raw.split(b"\0"):
        if not value:
            continue
        relative = value.decode("utf-8", errors="surrogateescape")
        validate_release_path(relative)
        full = repo / relative
        if full.is_file():
            result[relative] = {"status": "??", "sha256": sha256_file(full), "bytes": full.stat().st_size}
    return result


def machine_release_status(repo: pathlib.Path, allowed: list[str]) -> dict[str, dict[str, Any]]:
    """One status projection for preflight and release preparation."""
    result = worktree_status(repo)
    result.update(ignored_machine_files(repo, allowed))
    return result


def validate_recovery_candidate(repo: pathlib.Path, path: str) -> None:
    """Cheap fail-closed format checks before adopting an orphaned output."""
    validate_release_path(path)
    target = repo / path
    if target.is_symlink() or not target.is_file():
        raise RunManagerError("RECOVERY_PATH_INVALID", f"待接管路径不是普通文件：{path}")
    if path.endswith(".json"):
        try:
            json.loads(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RunManagerError("RECOVERY_CONTENT_INVALID", f"待接管 JSON 无效：{path}") from error
    if path.endswith(".pgn"):
        sample = target.read_bytes()[:4096]
        if not sample.strip() or b"[" not in sample:
            raise RunManagerError("RECOVERY_CONTENT_INVALID", f"待接管 PGN 无效：{path}")


def recovery_candidates(repo: pathlib.Path, allow: list[str]) -> list[str]:
    allowed = [*allow, MANIFEST_PATH]
    snapshot = machine_release_status(repo, allowed)
    return sorted(path for path in snapshot if within(path, allowed))


def within(path: str, roots: list[str]) -> bool:
    return any(path == root.rstrip("/") or path.startswith(root.rstrip("/") + "/") for root in roots)


def validate_release_path(path: str) -> None:
    if path.startswith("/") or ".." in pathlib.PurePosixPath(path).parts:
        raise RunManagerError("RELEASE_PATH_INVALID", f"非法发布路径：{path}")
    if within(path, list(FORBIDDEN_PREFIXES)):
        raise RunManagerError("RELEASE_PATH_FORBIDDEN", f"人工/社区/原始证据路径禁止自动发布：{path}")
    if path.lower().endswith(RAW_SUFFIXES):
        raise RunManagerError("RAW_SOURCE_PUBLICATION_BLOCKED", f"原始网页禁止进入发布包：{path}")


def preflight(repo: pathlib.Path, run_dir: pathlib.Path, allow: list[str], *, adopt: list[str] | None = None) -> None:
    """Record a release baseline, optionally adopting exact orphaned machine outputs.

    ``adopt`` is deliberately narrow: it is only for a verified generated
    output written by an interrupted local run before its manifest was made.
    The caller must list every path exactly; all other dirty release files
    still fail preflight as usual.
    """
    allowed = [*allow, MANIFEST_PATH]
    adopt = sorted(set(adopt or []))
    for path in adopt:
        validate_release_path(path)
        if not within(path, allowed):
            raise RunManagerError("RECOVERY_PATH_FORBIDDEN", f"恢复路径不在本次发布白名单：{path}")
    staged = git(repo, "diff", "--cached", "--name-only", "-z").stdout
    if staged:
        paths = [p.decode("utf-8", "replace") for p in staged.split(b"\0") if p]
        raise RunManagerError("GIT_INDEX_NOT_CLEAN", f"Git 暂存区已有内容：{', '.join(paths[:5])}")
    baseline = machine_release_status(repo, allowed)
    dirty_owned = [path for path in baseline if within(path, allowed)]
    unknown_adopted = [path for path in adopt if path not in dirty_owned]
    if unknown_adopted:
        raise RunManagerError("RECOVERY_PATH_NOT_DIRTY", f"恢复路径不是待接管的机器输出：{', '.join(unknown_adopted[:8])}")
    dirty_not_adopted = [path for path in dirty_owned if path not in adopt]
    if dirty_not_adopted:
        diagnostics = run_dir / "diagnostics"
        diagnostics.mkdir(parents=True, exist_ok=True)
        atomic_json(diagnostics / "recovery-candidates.json", {
            "schemaVersion": 1,
            "paths": sorted(dirty_not_adopted),
            "message": "这些机器发布路径未进入 manifest；只能显式接管，不会自动丢弃。",
            "recordedAt": now(),
        })
        raise RunManagerError(
            "DIRTY_RELEASE_PATH",
            "发布归属路径已有未提交修改，请先处理后再抓取：" + ", ".join(dirty_not_adopted[:8]),
        )
    # Leave verified adopted outputs out of the baseline so prepare_release
    # creates a normal manifest for them; all unrelated user/code changes stay
    # in the baseline and therefore cannot leak into the release.
    for path in adopt:
        validate_recovery_candidate(repo, path)
        baseline.pop(path, None)
    atomic_json(run_dir / "worktree-baseline.json", baseline)


def source_for_command(command: str) -> str:
    if command in {"registry", "all"}:
        return "fide"
    if command in {"bulk", "bulk-full"}:
        return "lichess"
    if command == "storage-migrate":
        return "object-storage"
    return "chess-results"


def prepare_release(repo: pathlib.Path, run_dir: pathlib.Path, command: str, allow: list[str]) -> dict[str, Any]:
    baseline_path = run_dir / "worktree-baseline.json"
    if not baseline_path.is_file():
        raise RunManagerError(
            "RELEASE_BASELINE_MISSING",
            "本次运行没有通过发布预检，禁止把现有工作树改动包装为发布包。",
        )
    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RunManagerError(
            "RELEASE_BASELINE_INVALID",
            "本次运行的发布基线损坏，禁止继续生成发布包。",
        ) from error
    if not isinstance(baseline, dict):
        raise RunManagerError(
            "RELEASE_BASELINE_INVALID",
            "本次运行的发布基线格式无效，禁止继续生成发布包。",
        )
    current = machine_release_status(repo, [*allow, MANIFEST_PATH])
    allowed = [*allow, MANIFEST_PATH]
    outside_changes = [
        path for path in set(baseline) | set(current)
        if not within(path, allowed) and baseline.get(path) != current.get(path)
    ]
    if outside_changes:
        diagnostics = run_dir / "diagnostics"
        diagnostics.mkdir(parents=True, exist_ok=True)
        atomic_json(diagnostics / "outside-worktree-changes.json", {
            "schemaVersion": 1,
            "paths": sorted(outside_changes),
            "message": "运行期间的非发布路径改动已记录，但不进入 manifest，也不阻断机器数据交付。",
            "recordedAt": now(),
        })
    changed = sorted(
        path for path in set(baseline) | set(current)
        if within(path, allow) and baseline.get(path) != current.get(path)
    )
    for path in changed:
        validate_release_path(path)
    if not changed:
        return {"changed": 0, "files": [], "manifest": None}

    base_commit = git(repo, "rev-parse", "HEAD").stdout.decode().strip()
    files: list[dict[str, Any]] = []
    for path in changed:
        full = repo / path
        if full.is_symlink():
            raise RunManagerError("RELEASE_PATH_INVALID", f"发布包禁止符号链接：{path}")
        base_oid, base_sha256 = git_blob_facts(repo, base_commit, path)
        if full.is_file():
            files.append({
                "path": path,
                "operation": "upsert",
                "sha256": sha256_file(full),
                "bytes": full.stat().st_size,
                "baseBlobOid": base_oid,
                "baseSha256": base_sha256,
            })
        else:
            files.append({
                "path": path,
                "operation": "delete",
                "sha256": None,
                "bytes": 0,
                "baseBlobOid": base_oid,
                "baseSha256": base_sha256,
            })
    source = source_for_command(command)
    manifest = {
        "schemaVersion": 1,
        "runId": read_json(run_dir / "run.json").get("runId"),
        "command": command,
        "createdAt": now(),
        "baseCommit": base_commit,
        "source": source_release_metadata(source),
        "files": files,
    }
    validate_manifest(manifest)
    manifest_path = repo / MANIFEST_PATH
    atomic_json(manifest_path, manifest)
    for item in files:
        # Event detail archives live beneath an ignored generated-data tree.
        # A path that is already tracked but happens to be clean in the index
        # can still make `git add -A -- <path>` exit non-zero as ignored.
        # Release manifests have already validated the exact path, so force-add
        # only this explicit manifest entry rather than broadening the stage.
        git(repo, "add", "-f", "--sparse", "-A", "--", item["path"])
    # A dedicated collector worktree may sparsely include only the receipt
    # subtree. The tracked release manifest still has to enter the exact stage
    # even when its generated-data parent is sparse-excluded.
    git(repo, "add", "-f", "--sparse", "--", MANIFEST_PATH)
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
    if source_name == "Chess-Results" and source.get("releasePolicy") not in {"full-data", "authorized"}:
        raise RunManagerError(
            "COMPLIANCE_POLICY_BLOCKED",
            "Chess-Results link-only 发布包不合法：清洗后的结构化数据才允许进入 manifest。",
        )
    if source_name not in {
        "FIDE Rating List", "Lichess Broadcasts", "Chess-Results", "R2 Object Storage",
    }:
        raise RunManagerError("RELEASE_SOURCE_UNSUPPORTED", f"机器发布不支持来源：{source_name or '?'}")
    if source_name == "FIDE Rating List" and source.get("releasePolicy") != "factual-registry-projection":
        raise RunManagerError("RELEASE_SOURCE_METADATA_INVALID", "FIDE 发布缺少事实注册表投影声明。")
    if source.get("source") == "Lichess Broadcasts" and (
        source.get("releasePolicy") != "cc-by-sa-4.0"
        or source.get("licenseURL") != "https://creativecommons.org/licenses/by-sa/4.0/"
        or not source.get("attributionURL")
    ):
        raise RunManagerError("RELEASE_LICENSE_MISSING", "Lichess 发布缺少 CC BY-SA 4.0 许可或署名信息。")
    if source_name == "R2 Object Storage" and (
        source.get("releasePolicy") != "verified-public-object-replication"
    ):
        raise RunManagerError(
            "RELEASE_SOURCE_METADATA_INVALID",
            "R2 发布回执缺少已验证公开对象复制声明。",
        )
    files: list[dict[str, Any]] = []
    seen: set[str] = set()
    base_commit = payload.get("baseCommit")
    delivery_base_commit = payload.get("deliveryBaseCommit")
    for label, value in (
        ("baseCommit", base_commit),
        ("deliveryBaseCommit", delivery_base_commit),
    ):
        if value is not None and not re.fullmatch(r"[0-9a-f]{40,64}", str(value)):
            raise RunManagerError("RELEASE_MANIFEST_INVALID", f"{label} 不是有效 Git commit id。")
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
        if source_name == "Chess-Results" and not within(path, list(CHESS_RESULTS_RELEASE_PREFIXES)):
            raise RunManagerError("RELEASE_SOURCE_PATH_MISMATCH", f"Chess-Results manifest 不能发布：{path}")
        if item.get("operation") not in {"upsert", "delete"}:
            raise RunManagerError("RELEASE_MANIFEST_INVALID", f"非法文件操作：{path}")
        if item.get("operation") == "upsert":
            if not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256") or "")):
                raise RunManagerError("RELEASE_MANIFEST_INVALID", f"upsert 缺少有效 SHA-256：{path}")
            if not isinstance(item.get("bytes"), int) or int(item["bytes"]) < 0:
                raise RunManagerError("RELEASE_MANIFEST_INVALID", f"upsert 字节数无效：{path}")
        elif item.get("sha256") not in {None, ""}:
            raise RunManagerError("RELEASE_MANIFEST_INVALID", f"delete 不应携带 SHA-256：{path}")
        for prefix, commit in (
            ("base", base_commit),
            ("deliveryBase", delivery_base_commit),
        ):
            oid_key = f"{prefix}BlobOid"
            sha_key = f"{prefix}Sha256"
            if oid_key not in item and sha_key not in item:
                continue
            oid = item.get(oid_key)
            sha = item.get(sha_key)
            if oid is None and sha is None:
                pass
            elif (
                not re.fullmatch(r"[0-9a-f]{40,64}", str(oid or ""))
                or not re.fullmatch(r"[0-9a-f]{64}", str(sha or ""))
            ):
                raise RunManagerError(
                    "RELEASE_MANIFEST_INVALID",
                    f"{path} 的 {prefix} blob/hash 不完整。",
                )
            if not commit:
                raise RunManagerError(
                    "RELEASE_MANIFEST_INVALID",
                    f"{path} 携带 {prefix} blob/hash 但 manifest 缺少对应 commit。",
                )
        files.append(item)
    return files


def validate_release_baseline(
    repo: pathlib.Path,
    source_ref: str,
    payload: dict[str, Any],
    files: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate every baseline/current/candidate tuple before worktree writes."""
    try:
        ancestry = git(repo, "rev-list", "--parents", "-n", "1", source_ref).stdout.decode().split()
    except subprocess.CalledProcessError as error:
        raise RunManagerError(
            "RELEASE_BASE_UNAVAILABLE",
            f"无法读取发布提交 {source_ref} 的父节点。",
        ) from error
    if len(ancestry) != 2:
        raise RunManagerError(
            "RELEASE_BASE_UNAVAILABLE",
            f"发布提交 {source_ref} 必须有且仅有一个可用父节点；请至少 fetch depth 2。",
        )
    parent = ancestry[1]
    delivery_mode = bool(payload.get("deliveryBaseCommit"))
    baseline_ref = str(payload.get("deliveryBaseCommit") or payload.get("baseCommit") or parent)
    if baseline_ref != parent:
        raise RunManagerError(
            "RELEASE_BASE_COMMIT_MISMATCH",
            f"发布提交父节点 {parent} 与 manifest 基线 {baseline_ref} 不一致。",
        )

    prefix = "deliveryBase" if delivery_mode else "base"
    conflicts: list[str] = []
    idempotent = 0
    verified = 0
    for item in files:
        path = item["path"]
        oid_key = f"{prefix}BlobOid"
        sha_key = f"{prefix}Sha256"
        expected_oid = item.get(oid_key) if oid_key in item else None
        expected_sha = item.get(sha_key) if sha_key in item else None
        has_embedded_baseline = oid_key in item or sha_key in item
        actual_base_oid, actual_base_sha = git_blob_facts(repo, baseline_ref, path)
        if has_embedded_baseline and (
            expected_oid != actual_base_oid or expected_sha != actual_base_sha
        ):
            raise RunManagerError(
                "RELEASE_BASE_HASH_MISMATCH",
                f"manifest 基线与 {baseline_ref}:{path} 不一致。",
            )

        baseline_oid = actual_base_oid
        current_oid = git_blob_oid(repo, "HEAD", path)
        candidate_oid = (
            git_blob_oid(repo, source_ref, path)
            if item["operation"] == "upsert"
            else None
        )
        if item["operation"] == "upsert" and candidate_oid is None:
            raise RunManagerError("RELEASE_PATH_MISSING", f"发布提交缺少候选文件：{path}")
        if current_oid == candidate_oid:
            idempotent += 1
        elif current_oid != baseline_oid:
            conflicts.append(path)
        verified += 1

    if conflicts:
        raise RunManagerError(
            "RELEASE_BASE_CONFLICT",
            "main 已在发布基线之后修改以下路径，候选已隔离且未写入工作树："
            + ", ".join(conflicts[:8]),
        )
    return {
        "baselineCommit": baseline_ref,
        "baselineMode": "delivery" if delivery_mode else ("manifest" if payload.get("baseCommit") else "legacy-parent"),
        "verified": verified,
        "idempotent": idempotent,
    }


def prefetch_partial_clone_blobs(
    repo: pathlib.Path, source_ref: str, upserts: list[dict[str, Any]]
) -> None:
    """Fetch manifest-listed blobs in one promisor request when repo is partial."""
    if not upserts:
        return
    configured = git(
        repo,
        "config",
        "--get-regexp",
        r"^remote\..*\.promisor$",
        check=False,
    )
    promisor_remote = ""
    for line in configured.stdout.decode("utf-8", errors="replace").splitlines():
        key, _, value = line.partition(" ")
        if value.strip().lower() == "true" and key.startswith("remote.") and key.endswith(".promisor"):
            promisor_remote = key[len("remote.") : -len(".promisor")]
            break
    if not promisor_remote:
        return

    object_ids: list[bytes] = []
    for start in range(0, len(upserts), CHECKOUT_BATCH_SIZE):
        batch = upserts[start : start + CHECKOUT_BATCH_SIZE]
        tree = git(
            repo,
            "ls-tree",
            "-z",
            source_ref,
            "--",
            *(item["path"] for item in batch),
        ).stdout
        records = [record for record in tree.split(b"\0") if record]
        if len(records) != len(batch):
            raise RunManagerError(
                "RELEASE_PATH_MISSING",
                "manifest 中的 upsert 路径未全部出现在发布提交。",
            )
        for record in records:
            metadata, separator, _ = record.partition(b"\t")
            fields = metadata.split()
            if not separator or len(fields) != 3 or fields[1] != b"blob":
                raise RunManagerError("RELEASE_PATH_INVALID", "manifest upsert 必须指向普通 Git blob。")
            object_ids.append(fields[2])

    # This matches Git's own partial-clone lazy-fetch command, but supplies all
    # exact manifest blob OIDs at once. The noop negotiation setting is
    # required for direct object wants; without it GitHub may reject the batch.
    unique_ids = list(dict.fromkeys(object_ids))
    command = [
        "git",
        "-c",
        "fetch.negotiationAlgorithm=noop",
        "fetch",
        promisor_remote,
        "--no-tags",
        "--no-write-fetch-head",
        "--recurse-submodules=no",
        "--filter=blob:none",
        "--stdin",
    ]
    object_input = b"\n".join(unique_ids) + b"\n"
    fetched: subprocess.CompletedProcess[bytes] | None = None
    for attempt in range(1, PREFETCH_ATTEMPTS + 1):
        fetched = subprocess.run(
            command,
            cwd=repo,
            input=object_input,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if fetched.returncode == 0:
            return
        if attempt < PREFETCH_ATTEMPTS:
            time.sleep(attempt)

    # Prefetch is a performance optimization, not a correctness gate. Let the
    # following checkout use Git's normal lazy-fetch path if direct object
    # wants remain unavailable after bounded retries.
    assert fetched is not None
    detail = fetched.stderr.decode("utf-8", errors="replace").strip()
    print(
        f"warning: manifest blob batch prefetch failed after {PREFETCH_ATTEMPTS} attempts; "
        f"falling back to checkout lazy-fetch: {detail or 'git fetch failed'}",
        file=sys.stderr,
    )


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


def _outbox_write_bundle(
    repo: pathlib.Path,
    commit: str,
    manifest: dict[str, Any],
    result_path: pathlib.Path | None = None,
) -> pathlib.Path:
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
    if result_path and result_path.is_file():
        result = read_json(result_path)
        if result:
            atomic_json(entry / "result.json", result)
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
    entry = _outbox_write_bundle(repo, commit, manifest, run_dir / "result.json")
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
    baseline = validate_release_baseline(repo, source_ref, payload, files)
    upserts = [item for item in files if item["operation"] == "upsert"]
    prefetch_partial_clone_blobs(repo, source_ref, upserts)

    # The ingest checkout is blobless and excludes machine-data roots from its
    # sparse worktree. Materialize only manifest-listed upserts; bounded batches
    # keep command lines and index updates manageable after the one-shot fetch.
    for start in range(0, len(upserts), CHECKOUT_BATCH_SIZE):
        batch = upserts[start : start + CHECKOUT_BATCH_SIZE]
        git(
            repo,
            "checkout",
            "--ignore-skip-worktree-bits",
            source_ref,
            "--",
            *(item["path"] for item in batch),
        )

    applied: list[str] = []
    for item in files:
        path = item["path"]
        target = repo / path
        if item["operation"] == "delete":
            # Sparse-excluded files are absent on disk and retain an index
            # entry with skip-worktree set. Remove that entry directly; this
            # also makes an already-applied deletion idempotent.
            git(repo, "update-index", "--force-remove", "--", path)
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
        else:
            if target.is_symlink():
                raise RunManagerError("RELEASE_PATH_INVALID", f"发布包禁止符号链接：{path}")
            if sha256_file(target) != item.get("sha256"):
                raise RunManagerError("RELEASE_HASH_MISMATCH", f"发布文件哈希不匹配：{path}")
        applied.append(path)
    git(repo, "checkout", "--ignore-skip-worktree-bits", source_ref, "--", MANIFEST_PATH)
    applied.append(MANIFEST_PATH)
    if path_list:
        path_list.write_text("\n".join(applied) + "\n", encoding="utf-8")
    return {
        "runId": payload.get("runId"),
        "applied": len(files),
        "paths": applied,
        "baseline": baseline,
    }


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="action", required=True)
    acquire_p = sub.add_parser("acquire")
    acquire_p.add_argument("--command", required=True)
    acquire_p.add_argument("--pid", required=True, type=int)
    acquire_p.add_argument("--request-argument", action="append", default=[])
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
    error_p = sub.add_parser("error")
    error_p.add_argument("--run-dir", required=True, type=pathlib.Path)
    error_p.add_argument("--stage", default="unknown")
    error_p.add_argument("--code", required=True)
    error_p.add_argument("--message", required=True)
    error_p.add_argument("--hint", default="")
    error_p.add_argument("--evidence", default="")
    error_get_p = sub.add_parser("error-get")
    error_get_p.add_argument("--run-dir", required=True, type=pathlib.Path)
    error_get_p.add_argument("--plain", action="store_true")
    preflight_p = sub.add_parser("preflight")
    preflight_p.add_argument("--repo", required=True, type=pathlib.Path)
    preflight_p.add_argument("--run-dir", required=True, type=pathlib.Path)
    preflight_p.add_argument("--allow", action="append", default=[])
    preflight_p.add_argument("--adopt", action="append", default=[])
    recovery_p = sub.add_parser("recovery-list")
    recovery_p.add_argument("--repo", required=True, type=pathlib.Path)
    recovery_p.add_argument("--allow", action="append", default=[])
    recovery_p.add_argument("--plain", action="store_true")
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
    marked_running = payload.get("status") == "running"
    payload["running"] = marked_running and process_alive(pid)
    if marked_running and not payload["running"]:
        error_code = str(payload.get("errorCode") or inferred_final_error_code(payload["log"]))
        payload["staleState"] = True
        payload["status"] = "finished"
        discovery_result = {}
        if payload.get("command") == "discover-events":
            run_dir = pathlib.Path(str(payload.get("runDir") or ""))
            discovery_result = read_json(run_dir / "result.json") if str(run_dir) not in {"", "."} else {}
            if (
                discovery_result.get("command") != "discover-events"
                or discovery_result.get("status") not in {"ok", "partial", "failed"}
            ):
                discovery_result = {}
        if discovery_result.get("status") == "ok":
            players = int(discovery_result.get("playersChecked") or 0)
            candidates = int(discovery_result.get("candidatesFound") or 0)
            payload["result"] = "result-preserved"
            payload["errorCode"] = "FINAL_STATE_WRITE_FAILED"
            payload["resultPreserved"] = True
            payload["discoveryResult"] = discovery_result
            payload["message"] = (
                f"赛事发现结果已保留：检查 {players} 名棋手，发现 {candidates} 个候选赛事；"
                "仅运行状态收尾异常，无需重新查询来源。"
            )
        elif discovery_result.get("status") == "partial":
            players = int(discovery_result.get("playersChecked") or 0)
            candidates = int(discovery_result.get("candidatesFound") or 0)
            failures = len(discovery_result.get("failures") or [])
            payload["result"] = "partial"
            payload["errorCode"] = "PARTIAL_FAILURE"
            payload["resultPreserved"] = True
            payload["stateFinalizationFailed"] = True
            payload["discoveryResult"] = discovery_result
            payload["message"] = (
                f"赛事发现部分完成：检查 {players} 名棋手，发现 {candidates} 个候选赛事，"
                f"{failures} 名查询失败；结果已保留，且运行状态收尾异常。"
            )
        elif discovery_result.get("status") == "failed":
            players = int(discovery_result.get("playersChecked") or 0)
            failures = len(discovery_result.get("failures") or [])
            payload["result"] = "failed"
            payload["errorCode"] = "EVENT_DISCOVERY_FAILED"
            payload["resultPreserved"] = True
            payload["stateFinalizationFailed"] = True
            payload["discoveryResult"] = discovery_result
            payload["message"] = (
                f"赛事发现未形成候选：检查 {players} 名棋手，{failures} 名查询失败；"
                "失败明细已保留，且运行状态收尾异常。"
            )
        else:
            payload["result"] = payload.get("result") or "failed"
            payload["errorCode"] = error_code or "PROCESS_EXITED_WITHOUT_FINAL_STATE"
            if payload.get("command") == "deliver" and error_code == "GIT_PUSH_FAILED":
                payload["message"] = (
                    "投递进程已结束；发布包仍在 outbox，网络恢复后重试 deliver。"
                )
            else:
                payload["message"] = (
                    payload.get("message")
                    or "任务进程已结束但未写入最终状态；请查看本次日志。"
                )
    return payload


def main() -> int:
    args = parser().parse_args()
    try:
        if args.action == "acquire":
            print(acquire(args.command, args.pid, args.request_argument))
        elif args.action == "update":
            print(json.dumps(update(args.run_dir, stage=args.stage, message=args.message, errorCode=args.error_code), ensure_ascii=False))
        elif args.action == "finish":
            print(json.dumps(finish(args.run_dir, args.code, args.result, args.error_code, args.message), ensure_ascii=False))
        elif args.action == "current":
            print(json.dumps(current_payload(args.tail), ensure_ascii=False))
        elif args.action == "error":
            print(json.dumps(record_error(
                args.run_dir, args.stage, args.code, args.message, args.hint, args.evidence,
            ), ensure_ascii=False))
        elif args.action == "error-get":
            payload = read_json(args.run_dir / "error.json")
            if args.plain:
                print(f"{payload.get('code') or ''}\t{payload.get('message') or ''}")
            else:
                print(json.dumps(payload, ensure_ascii=False))
        elif args.action == "preflight":
            preflight(args.repo, args.run_dir, args.allow, adopt=args.adopt)
        elif args.action == "recovery-list":
            paths = recovery_candidates(args.repo, args.allow)
            if args.plain:
                print("\n".join(paths))
            else:
                print(json.dumps({"paths": paths, "count": len(paths)}, ensure_ascii=False))
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
        run_dir = getattr(args, "run_dir", None)
        if isinstance(run_dir, pathlib.Path):
            record_error(
                run_dir,
                str(read_json(run_dir / "run.json").get("stage") or args.action),
                error.code,
                str(error),
            )
        print(json.dumps({"ok": False, "code": error.code, "message": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
