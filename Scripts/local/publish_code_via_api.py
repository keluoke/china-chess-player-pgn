#!/usr/bin/env python3
"""Publish local code changes on top of remote main without fetching/cloning.

The data pipeline intentionally force-pushes ``local-data`` and lets Actions
mirror raw data into main. Code changes need a different path: this tool reads
a local commit range, performs a three-way merge against the current GitHub
target branch through the GitHub API, and creates a clean PR branch whose
single parent is the current target head.

Derived artifacts are excluded by default. ``rebuild-indexes.yml`` regenerates
them from the latest main data after the code PR is merged.
"""

from __future__ import annotations

import argparse
import base64
import http.client
import json
import os
import pathlib
import re
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

try:
    import certifi
except ImportError:  # pragma: no cover - system CA remains the fallback
    certifi = None


ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_EXCLUDES = ("docs/data/", "data/generated/", "data/incoming/")


@dataclass
class Change:
    status: str
    path: str


def run(*args: str, input_data: bytes | None = None, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        args,
        cwd=ROOT,
        input=input_data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


def git_text(*args: str) -> str:
    return run("git", *args).stdout.decode("utf-8", errors="replace").strip()


def git_blob(ref: str, path: str) -> bytes | None:
    result = run("git", "show", f"{ref}:{path}", check=False)
    return result.stdout if result.returncode == 0 else None


def git_mode(ref: str, path: str) -> str:
    line = git_text("ls-tree", ref, "--", path)
    return line.split()[0] if line else "100644"


def changed_paths(base: str, head: str) -> list[Change]:
    payload = run("git", "diff", "--no-renames", "--name-status", "-z", base, head).stdout
    fields = payload.decode("utf-8", errors="surrogateescape").split("\0")
    result: list[Change] = []
    for index in range(0, len(fields) - 1, 2):
        status, path = fields[index:index + 2]
        if status and path:
            result.append(Change(status=status[0], path=path))
    return result


def repository_name() -> str:
    remote = git_text("remote", "get-url", "origin")
    match = re.search(r"github\.com[/:]([^/]+/[^/]+?)(?:\.git)?$", remote)
    if not match:
        raise SystemExit(f"无法从 origin 推断 GitHub 仓库: {remote}")
    return match.group(1)


def github_token() -> str:
    request = b"protocol=https\nhost=github.com\n\n"
    response = run("git", "credential", "fill", input_data=request).stdout.decode("utf-8")
    values = dict(line.split("=", 1) for line in response.splitlines() if "=" in line)
    token = values.get("password", "")
    if not token:
        raise SystemExit("macOS Git 凭据链中没有 github.com token")
    return token


class GitHub:
    def __init__(self, repository: str, token: str, timeout: float = 180.0):
        self.repository = repository
        self.token = token
        self.timeout = timeout
        self.root = f"https://api.github.com/repos/{repository}"
        self.ssl_context = ssl.create_default_context(cafile=certifi.where() if certifi else None)

    def request(self, method: str, path: str, payload: Any | None = None, missing_ok: bool = False) -> Any:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        for attempt in range(1, 5):
            request = urllib.request.Request(
                self.root + path,
                data=data,
                method=method,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "Content-Type": "application/json",
                    "User-Agent": "china-chess-player-pgn-code-publisher",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout, context=self.ssl_context) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                if missing_ok and error.code == 404:
                    return None
                detail = error.read().decode("utf-8", errors="replace")
                if error.code not in {429, 500, 502, 503, 504} or attempt == 4:
                    raise SystemExit(f"GitHub API {method} {path} failed: HTTP {error.code} {detail[:500]}") from error
            except (
                urllib.error.URLError,
                http.client.IncompleteRead,
                http.client.RemoteDisconnected,
                TimeoutError,
            ) as error:
                if attempt == 4:
                    reason = getattr(error, "reason", error)
                    raise SystemExit(f"GitHub API 网络失败（重试 4 次）: {reason}") from error
            time.sleep(attempt * 2)
        raise AssertionError("unreachable")

    def ref_sha(self, branch: str) -> str:
        data = self.request("GET", f"/git/ref/heads/{urllib.parse.quote(branch, safe='/')}")
        return str(data["object"]["sha"])

    def commit_tree(self, commit_sha: str) -> str:
        data = self.request("GET", f"/git/commits/{commit_sha}")
        return str(data["tree"]["sha"])

    def content(self, path: str, ref: str) -> tuple[bytes | None, str | None]:
        encoded = urllib.parse.quote(path, safe="/")
        data = self.request("GET", f"/contents/{encoded}?ref={ref}", missing_ok=True)
        if data is None:
            return None, None
        blob_sha = str(data.get("sha") or "")
        if data.get("encoding") == "base64":
            return base64.b64decode(data["content"]), blob_sha
        # The Contents API omits inline data for large files. Fetch the same
        # immutable object through the Git Blobs API so large CSV/JSON sources
        # can use the exact same merge and conflict checks as small files.
        if blob_sha and data.get("type") == "file":
            blob = self.request("GET", f"/git/blobs/{blob_sha}")
            if blob.get("encoding") == "base64":
                return base64.b64decode(blob["content"]), blob_sha
        raise SystemExit(f"无法读取 {path}: GitHub API 未返回可解码的文件内容")

    def blob(self, content: bytes) -> str:
        data = self.request("POST", "/git/blobs", {
            "content": base64.b64encode(content).decode("ascii"),
            "encoding": "base64",
        })
        return str(data["sha"])


def is_binary(content: bytes | None) -> bool:
    return bool(content and b"\0" in content[:8192])


def merge_text(path: str, ours: bytes, base: bytes, target: bytes) -> tuple[bytes, bool]:
    with tempfile.TemporaryDirectory(prefix="code-publish-") as temp:
        temp_path = pathlib.Path(temp)
        ours_path = temp_path / "ours"
        base_path = temp_path / "base"
        target_path = temp_path / "target"
        ours_path.write_bytes(ours)
        base_path.write_bytes(base)
        target_path.write_bytes(target)
        result = run(
            "git", "merge-file", "--stdout", "--diff3",
            "-L", f"local:{path}", "-L", f"base:{path}", "-L", f"main:{path}",
            str(ours_path), str(base_path), str(target_path),
            check=False,
        )
        return result.stdout, result.returncode == 0


def conflict_output(path: str, base: bytes | None, ours: bytes | None, target: bytes | None, merged: bytes | None) -> None:
    root = ROOT / ".git" / "code-publish-conflicts"
    for label, content in (("base", base), ("ours", ours), ("main", target), ("merged", merged)):
        if content is None:
            continue
        output = root / label / path
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(content)


def resolution(path: str) -> bytes | None:
    candidate = ROOT / ".git" / "code-publish-conflicts" / "merged" / path
    if not candidate.exists():
        return None
    content = candidate.read_bytes()
    if b"<<<<<<<" in content or b">>>>>>>" in content:
        return None
    return content


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish a clean GitHub code branch without fetching main.")
    parser.add_argument("--source-base", required=True, help="local commit before the intended code changes")
    parser.add_argument("--source-head", default="HEAD")
    parser.add_argument("--target-branch", default="main")
    parser.add_argument("--branch", required=True, help="new GitHub branch to create")
    parser.add_argument("--message", required=True)
    parser.add_argument("--publish", action="store_true", help="create blobs/commit/ref after a clean dry run")
    parser.add_argument("--include-derived", action="store_true")
    parser.add_argument("--update-existing", action="store_true", help="fast-forward an existing API-published branch")
    parser.add_argument("--timeout", type=float, default=180.0, help="GitHub API request timeout in seconds")
    args = parser.parse_args()

    repository = repository_name()
    github = GitHub(repository, github_token(), timeout=args.timeout)
    target_sha = github.ref_sha(args.target_branch)
    target_tree = github.commit_tree(target_sha)
    changes = changed_paths(args.source_base, args.source_head)
    if not args.include_derived:
        changes = [change for change in changes if not change.path.startswith(DEFAULT_EXCLUDES)]

    entries: list[dict[str, Any]] = []
    conflicts: list[str] = []
    for change in changes:
        path = change.path
        base = git_blob(args.source_base, path)
        ours = git_blob(args.source_head, path)
        target, _target_blob = github.content(path, target_sha)
        merged: bytes | None
        clean = True

        resolved = resolution(path)
        if resolved is not None:
            merged = resolved
        elif ours is None:
            merged = None
            clean = target == base
        elif target == base or target == ours:
            merged = ours
        elif ours == base:
            merged = target
        elif base is None and target is None:
            merged = ours
        elif base is None or target is None or is_binary(base) or is_binary(ours) or is_binary(target):
            merged = ours
            clean = False
        else:
            merged, clean = merge_text(path, ours, base, target)

        if not clean:
            conflicts.append(path)
            conflict_output(path, base, ours, target, merged)
            continue
        if merged == target:
            continue
        if merged is None:
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
        else:
            entries.append({"path": path, "mode": git_mode(args.source_head, path), "type": "blob", "content": merged})

    print(json.dumps({
        "repository": repository,
        "targetBranch": args.target_branch,
        "targetSHA": target_sha,
        "sourceBase": git_text("rev-parse", args.source_base),
        "sourceHead": git_text("rev-parse", args.source_head),
        "changedPaths": len(changes),
        "publishPaths": len(entries),
        "conflicts": conflicts,
        "derivedExcluded": not args.include_derived,
    }, ensure_ascii=False, indent=2))

    if conflicts:
        print("冲突副本已写入 .git/code-publish-conflicts/{base,ours,main,merged}/", flush=True)
        return 2
    if not args.publish:
        print("Dry run complete; add --publish to create the remote branch.")
        return 0

    for entry in entries:
        content = entry.pop("content", None)
        if content is not None:
            entry["sha"] = github.blob(content)
    existing = github.request("GET", f"/git/ref/heads/{urllib.parse.quote(args.branch, safe='/')}", missing_ok=True)
    if existing is not None and not args.update_existing:
        raise SystemExit(f"远端分支已存在，拒绝覆盖: {args.branch}")
    parent_sha = str(existing["object"]["sha"]) if existing is not None else target_sha
    publish_base_tree = github.commit_tree(parent_sha) if existing is not None else target_tree

    tree = github.request("POST", "/git/trees", {"base_tree": publish_base_tree, "tree": entries})
    commit = github.request("POST", "/git/commits", {
        "message": args.message,
        "tree": tree["sha"],
        "parents": [parent_sha],
    })
    if existing is None:
        github.request("POST", "/git/refs", {"ref": f"refs/heads/{args.branch}", "sha": commit["sha"]})
    else:
        github.request("PATCH", f"/git/refs/heads/{urllib.parse.quote(args.branch, safe='/')}", {
            "sha": commit["sha"],
            "force": False,
        })
    print(json.dumps({
        "branch": args.branch,
        "commit": commit["sha"],
        "compareURL": f"https://github.com/{repository}/compare/{args.target_branch}...{args.branch}",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
