"""Exact artifact validation and fast-forward publication for monthly datasets."""
import hashlib
import os
from pathlib import Path
import json
import shutil
import subprocess


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def validate_release(release: Path, base: str, *, root: Path, allowed, receipt: str) -> list[dict]:
    manifest = json.loads((release / "release.json").read_text())
    if manifest["inputCommit"] != base:
        raise RuntimeError("MONTHLY_RELEASE_BASE_MISMATCH")
    rows = manifest["files"]
    paths = [row["path"] for row in rows]
    if not rows or len(paths) != len(set(paths)) or receipt not in paths:
        raise RuntimeError("MONTHLY_RELEASE_MANIFEST_INVALID")
    expected_files = {'release.json'} | {row['path'] for row in rows if row.get('operation') == 'upsert'}
    actual_files = {path.relative_to(release).as_posix() for path in release.rglob('*')
                    if path.is_file() or path.is_symlink()}
    if actual_files != expected_files:
        raise RuntimeError('MONTHLY_RELEASE_UNLISTED_FILES')
    for row in rows:
        path = row["path"]
        source, current = release / path, root / path
        if not allowed(path) or row.get("operation") not in {"upsert", "delete"}:
            raise RuntimeError(f"MONTHLY_RELEASE_PATH_INVALID: {path}")
        if row["operation"] == "upsert" and (source.is_symlink() or not source.is_file()):
            raise RuntimeError(f"MONTHLY_RELEASE_PATH_INVALID: {path}")
        if row["operation"] == "upsert" and (digest(source), source.stat().st_size) != (row["sha256"], row["bytes"]):
            raise RuntimeError(f"MONTHLY_RELEASE_HASH_MISMATCH: {path}")
        if (digest(current) if current.is_file() else None) != row["baseSha256"]:
            raise RuntimeError(f"MONTHLY_RELEASE_BASE_CONFLICT: {path}")
    return rows


def publish(release: Path, *, root: Path, allowed, receipt: str, message: str) -> None:
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root, text=True).strip()
    base = git("rev-parse", "HEAD")
    rows = validate_release(release, base, root=root, allowed=allowed, receipt=receipt)
    if git("status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("MONTHLY_RELEASE_DIRTY_WORKTREE")
    git("fetch", "--depth=1", "origin", "main")
    remote = git("rev-parse", "origin/main")
    if remote != base:
        # Rerunning only the failed publish job must not download sources again.
        identical = True
        for row in rows:
            result = subprocess.run(["git", "show", f"{remote}:{row['path']}"], cwd=root, capture_output=True)
            if row["operation"] == "delete":
                identical = identical and result.returncode != 0
            else:
                identical = identical and result.returncode == 0 and hashlib.sha256(result.stdout).hexdigest() == row["sha256"]
        if not identical:
            raise RuntimeError("MONTHLY_RELEASE_REMOTE_MOVED: preserve artifact; rebuild from current main")
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write(f"target_sha={remote}\n")
        return
    for row in rows:
        destination = root / row["path"]
        if row["operation"] == "delete":
            destination.unlink(missing_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(release / row["path"], destination)
    subprocess.run(["bash", "Scripts/ci_commit_push.sh", message,
                    *[row["path"] for row in rows]], cwd=root, check=True,
                   env={**os.environ, "CI_COMMIT_REBASE_ON_CONFLICT": "false", "PUSH_BRANCH": "main"})
    sha = git("rev-parse", "HEAD")
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(f"target_sha={sha}\n")
