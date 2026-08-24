#!/usr/bin/env python3
"""Install and verify an exact collector runtime/control-input overlay.

The collector checkout is intentionally append-only and never pulls main, so
its executable scripts cannot be trusted merely because its branch is named
``main``.  A clean code-workspace commit is the source of truth.  ``install``
copies only the reviewed allowlist from that immutable commit and writes the
installed manifest last; ``verify`` runs locally with no network access and
fails before a run is acquired or a source is contacted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
from typing import Any


SPEC_RELATIVE_PATH = pathlib.PurePosixPath("Scripts/local/collector-runtime-files.json")
INSTALLED_MANIFEST_NAME = "collector-runtime-manifest.json"
ALLOWED_INSTALL_PREFIXES = (
    "Scripts/",
    "data/community/",
    "data/manual/",
    "data/generated/audit/",
    "docs/data/index/",
)
PROFILE_SCOPES = {
    "core": {"core"},
    "event": {"core", "event"},
    "panel": {"core", "event", "panel"},
}


class CollectorRuntimeError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


def sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def safe_relative_path(value: Any) -> pathlib.PurePosixPath:
    text = str(value or "").strip()
    path = pathlib.PurePosixPath(text)
    if (
        not text
        or "\\" in text
        or "\x00" in text
        or path.is_absolute()
        or ".." in path.parts
        or "." in path.parts
        or any(part == ".git" for part in path.parts)
    ):
        raise CollectorRuntimeError("COLLECTOR_RUNTIME_SPEC_INVALID", f"unsafe path: {text!r}")
    if path.as_posix() != text:
        raise CollectorRuntimeError("COLLECTOR_RUNTIME_SPEC_INVALID", f"non-canonical path: {text!r}")
    if not text.startswith(ALLOWED_INSTALL_PREFIXES):
        raise CollectorRuntimeError("COLLECTOR_RUNTIME_SPEC_INVALID", f"path outside runtime roots: {text!r}")
    return path


def parse_spec_bytes(body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CollectorRuntimeError("COLLECTOR_RUNTIME_SPEC_INVALID", str(error)) from error
    if payload.get("schemaVersion") != 1 or not isinstance(payload.get("files"), list):
        raise CollectorRuntimeError("COLLECTOR_RUNTIME_SPEC_INVALID", "schemaVersion/files invalid")
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for item in payload["files"]:
        if not isinstance(item, dict):
            raise CollectorRuntimeError("COLLECTOR_RUNTIME_SPEC_INVALID", "file row is not an object")
        path = safe_relative_path(item.get("path")).as_posix()
        kind = str(item.get("kind") or "").strip()
        profiles = sorted({str(value).strip() for value in item.get("profiles") or []})
        if path in seen or kind not in {"runtime", "runtime-spec", "control-input"}:
            raise CollectorRuntimeError("COLLECTOR_RUNTIME_SPEC_INVALID", f"duplicate/invalid row: {path}")
        if not profiles or any(profile not in {"core", "event", "panel"} for profile in profiles):
            raise CollectorRuntimeError("COLLECTOR_RUNTIME_SPEC_INVALID", f"invalid profiles: {path}")
        if kind in {"runtime", "runtime-spec"} and not path.startswith("Scripts/"):
            raise CollectorRuntimeError("COLLECTOR_RUNTIME_SPEC_INVALID", f"runtime outside Scripts/: {path}")
        if kind == "control-input" and path.startswith("Scripts/"):
            raise CollectorRuntimeError("COLLECTOR_RUNTIME_SPEC_INVALID", f"control input under Scripts/: {path}")
        seen.add(path)
        normalized.append({"path": path, "kind": kind, "profiles": profiles})
    spec_row = next(
        (item for item in normalized if item["path"] == SPEC_RELATIVE_PATH.as_posix()),
        None,
    )
    if not spec_row or spec_row["kind"] != "runtime-spec" or "core" not in spec_row["profiles"]:
        raise CollectorRuntimeError("COLLECTOR_RUNTIME_SPEC_INVALID", "spec must include itself")
    return {"schemaVersion": 1, "files": normalized}


def read_spec(repo_root: pathlib.Path, spec_path: pathlib.Path | None = None) -> tuple[dict[str, Any], bytes]:
    path = spec_path or repo_root / SPEC_RELATIVE_PATH
    try:
        body = path.read_bytes()
    except OSError as error:
        raise CollectorRuntimeError("COLLECTOR_RUNTIME_SPEC_MISSING", str(path)) from error
    return parse_spec_bytes(body), body


def git(repo_root: pathlib.Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            ["git", "-C", str(repo_root), *args],
            check=check,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        stderr = getattr(error, "stderr", b"") or b""
        detail = stderr.decode("utf-8", errors="replace").strip() or str(error)
        raise CollectorRuntimeError("COLLECTOR_RUNTIME_GIT_FAILED", detail) from error


def workspace_role(repo_root: pathlib.Path) -> str:
    completed = git(repo_root, "config", "--get", "chessdb.workspaceRole", check=False)
    return completed.stdout.decode("utf-8", errors="replace").strip()


def installed_manifest_path(repo_root: pathlib.Path) -> pathlib.Path:
    completed = git(repo_root, "rev-parse", "--git-path", INSTALLED_MANIFEST_NAME)
    raw = pathlib.Path(completed.stdout.decode("utf-8", errors="replace").strip())
    return raw if raw.is_absolute() else repo_root / raw


def active_spec_rows(spec: dict[str, Any], profile: str) -> list[dict[str, Any]]:
    scopes = PROFILE_SCOPES.get(profile)
    if not scopes:
        raise CollectorRuntimeError("COLLECTOR_RUNTIME_PROFILE_INVALID", profile)
    return [item for item in spec["files"] if scopes.intersection(item["profiles"])]


def load_installed_manifest(path: pathlib.Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise CollectorRuntimeError("COLLECTOR_RUNTIME_MANIFEST_MISSING", str(path)) from error
    except (OSError, json.JSONDecodeError) as error:
        raise CollectorRuntimeError("COLLECTOR_RUNTIME_MANIFEST_INVALID", str(error)) from error
    if payload.get("schemaVersion") != 1 or not re.fullmatch(r"[0-9a-f]{40,64}", str(payload.get("sourceCommit") or "")):
        raise CollectorRuntimeError("COLLECTOR_RUNTIME_MANIFEST_INVALID", "schema/sourceCommit invalid")
    if not isinstance(payload.get("files"), list):
        raise CollectorRuntimeError("COLLECTOR_RUNTIME_MANIFEST_INVALID", "files missing")
    return payload


def verify(
    repo_root: pathlib.Path,
    *,
    profile: str,
    manifest_path: pathlib.Path | None = None,
    spec_path: pathlib.Path | None = None,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    spec, spec_body = read_spec(repo_root, spec_path)
    manifest = load_installed_manifest(manifest_path or installed_manifest_path(repo_root))
    if manifest.get("specSha256") != sha256_bytes(spec_body):
        raise CollectorRuntimeError(
            "COLLECTOR_RUNTIME_MANIFEST_STALE",
            "installed manifest does not match collector-runtime-files.json",
        )
    expected_spec = {item["path"]: item for item in spec["files"]}
    installed: dict[str, dict[str, Any]] = {}
    for item in manifest["files"]:
        if not isinstance(item, dict):
            raise CollectorRuntimeError("COLLECTOR_RUNTIME_MANIFEST_INVALID", "file row is not an object")
        path = safe_relative_path(item.get("path")).as_posix()
        if path in installed:
            raise CollectorRuntimeError("COLLECTOR_RUNTIME_MANIFEST_INVALID", f"duplicate row: {path}")
        if (
            item.get("mode") not in {"100644", "100755"}
            or not isinstance(item.get("bytes"), int)
            or item["bytes"] < 0
            or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256") or ""))
        ):
            raise CollectorRuntimeError("COLLECTOR_RUNTIME_MANIFEST_INVALID", f"invalid digest row: {path}")
        installed[path] = item
    if set(installed) != set(expected_spec):
        missing = sorted(set(expected_spec) - set(installed))
        extra = sorted(set(installed) - set(expected_spec))
        raise CollectorRuntimeError(
            "COLLECTOR_RUNTIME_MANIFEST_STALE",
            f"entry set differs; missing={missing[:5]} extra={extra[:5]}",
        )
    for path, expected in expected_spec.items():
        item = installed[path]
        if item.get("kind") != expected["kind"] or sorted(item.get("profiles") or []) != expected["profiles"]:
            raise CollectorRuntimeError("COLLECTOR_RUNTIME_MANIFEST_STALE", f"metadata differs: {path}")

    checked = 0
    for expected in active_spec_rows(spec, profile):
        path = expected["path"]
        item = installed[path]
        candidate = repo_root / safe_relative_path(path)
        if (
            candidate.parent.resolve() != candidate.parent
            or not candidate.is_file()
            or candidate.is_symlink()
        ):
            raise CollectorRuntimeError("COLLECTOR_RUNTIME_DRIFT", f"missing/non-regular: {path}")
        body = candidate.read_bytes()
        executable = bool(candidate.stat().st_mode & 0o111)
        expected_executable = item["mode"] == "100755"
        if (
            len(body) != item["bytes"]
            or sha256_bytes(body) != item["sha256"]
            or executable != expected_executable
        ):
            raise CollectorRuntimeError("COLLECTOR_RUNTIME_DRIFT", f"hash/size mismatch: {path}")
        checked += 1
    return {
        "ok": True,
        "profile": profile,
        "sourceCommit": manifest["sourceCommit"],
        "checked": checked,
    }


def git_object(repo_root: pathlib.Path, commit: str, relative: str) -> tuple[bytes, str]:
    body = git(repo_root, "show", f"{commit}:{relative}").stdout
    tree = git(repo_root, "ls-tree", commit, "--", relative).stdout.decode("utf-8", errors="replace").strip()
    match = re.match(r"^(100644|100755)\s+blob\s+[0-9a-f]{40,64}\t", tree)
    if not match:
        raise CollectorRuntimeError("COLLECTOR_RUNTIME_SOURCE_INVALID", f"not a regular Git blob: {relative}")
    return body, match.group(1)


def build_manifest(source_root: pathlib.Path, commit: str) -> tuple[dict[str, Any], dict[str, bytes]]:
    spec_body, _ = git_object(source_root, commit, SPEC_RELATIVE_PATH.as_posix())
    spec = parse_spec_bytes(spec_body)
    bodies: dict[str, bytes] = {}
    rows: list[dict[str, Any]] = []
    for item in spec["files"]:
        body, mode = git_object(source_root, commit, item["path"])
        bodies[item["path"]] = body
        rows.append({
            **item,
            "mode": mode,
            "bytes": len(body),
            "sha256": sha256_bytes(body),
        })
    return {
        "schemaVersion": 1,
        "sourceCommit": commit,
        "specSha256": sha256_bytes(spec_body),
        "files": rows,
    }, bodies


def atomic_write(path: pathlib.Path, body: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or path.parent.resolve() != path.parent:
        raise CollectorRuntimeError("COLLECTOR_RUNTIME_TARGET_INVALID", f"symlinked target: {path}")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def install(source_root: pathlib.Path, collector_root: pathlib.Path, *, apply: bool) -> dict[str, Any]:
    source_root = source_root.resolve()
    collector_root = collector_root.resolve()
    if source_root == collector_root or workspace_role(source_root) != "code":
        raise CollectorRuntimeError("COLLECTOR_RUNTIME_SOURCE_INVALID", "source must be the code workspace")
    if workspace_role(collector_root) != "collector":
        raise CollectorRuntimeError("COLLECTOR_RUNTIME_TARGET_INVALID", "target must be the collector workspace")
    if git(source_root, "branch", "--show-current").stdout.decode().strip() != "main":
        raise CollectorRuntimeError("COLLECTOR_RUNTIME_SOURCE_INVALID", "code workspace must be on main")
    if git(source_root, "diff", "--quiet", check=False).returncode or git(
        source_root, "diff", "--cached", "--quiet", check=False
    ).returncode:
        raise CollectorRuntimeError("COLLECTOR_RUNTIME_SOURCE_DIRTY", "commit tracked changes before install")
    commit = git(source_root, "rev-parse", "HEAD").stdout.decode().strip()
    manifest, bodies = build_manifest(source_root, commit)
    changed = []
    for item in manifest["files"]:
        target = collector_root / safe_relative_path(item["path"])
        current = target.read_bytes() if target.is_file() and not target.is_symlink() else None
        executable = bool(target.stat().st_mode & 0o111) if target.is_file() else False
        expected_executable = item["mode"] == "100755"
        if current != bodies[item["path"]] or executable != expected_executable:
            changed.append(item["path"])
    if apply:
        for item in manifest["files"]:
            target = collector_root / safe_relative_path(item["path"])
            atomic_write(target, bodies[item["path"]], 0o755 if item["mode"] == "100755" else 0o644)
        manifest_body = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        target_manifest = installed_manifest_path(collector_root)
        atomic_write(target_manifest, manifest_body, 0o600)
        verify(collector_root, profile="panel", manifest_path=target_manifest)
    return {
        "ok": True,
        "apply": apply,
        "sourceCommit": commit,
        "files": len(manifest["files"]),
        "changed": changed,
        "manifestPath": str(installed_manifest_path(collector_root)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--repo-root", type=pathlib.Path, required=True)
    verify_parser.add_argument("--profile", choices=sorted(PROFILE_SCOPES), default="core")
    verify_parser.add_argument("--manifest", type=pathlib.Path)
    install_parser = subparsers.add_parser("install")
    install_parser.add_argument("--source-root", type=pathlib.Path, required=True)
    install_parser.add_argument("--collector-root", type=pathlib.Path, required=True)
    install_parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "verify":
            result = verify(args.repo_root, profile=args.profile, manifest_path=args.manifest)
        else:
            result = install(args.source_root, args.collector_root, apply=args.apply)
    except CollectorRuntimeError as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
