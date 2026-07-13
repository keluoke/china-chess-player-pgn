#!/usr/bin/env python3
"""Publish selected local data files to ``local-data`` through GitHub's API.

This is the deterministic fallback when ordinary Git HTTPS cannot reach
github.com. The commit is based on current remote main, changes only explicitly
listed files, and force-moves the single-writer local-data ref. The existing
ingest workflow then mirrors data and rebuilds all derived artifacts.
"""

from __future__ import annotations

import argparse
import base64
import json
import pathlib
import subprocess
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
ALLOWED = ("data/manual/", "data/community/", "data/generated/", "data/incoming/", "docs/data/")


def repository_name() -> str:
    remote = subprocess.run(["git", "remote", "get-url", "origin"], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()
    match = re.search(r"github\.com[/:]([^/]+/[^/]+?)(?:\.git)?$", remote)
    if not match:
        raise SystemExit(f"cannot infer GitHub repository from {remote}")
    return match.group(1)


def api(repository: str, method: str, path: str, payload=None):
    command = ["gh", "api", "--method", method, f"repos/{repository}{path}"]
    encoded = None
    if payload is not None:
        command += ["--input", "-"]
        encoded = json.dumps(payload).encode("utf-8")
    result = subprocess.run(command, input=encoded, cwd=ROOT, check=True, capture_output=True, timeout=360)
    return json.loads(result.stdout or b"{}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", action="append", required=True, help="explicit repository-relative data file")
    parser.add_argument("--message", default="Publish selected local data via API")
    parser.add_argument("--target-branch", default="main")
    parser.add_argument("--data-branch", default="local-data")
    args = parser.parse_args()
    paths = list(dict.fromkeys(args.path))
    for value in paths:
        if not value.startswith(ALLOWED) or not (ROOT / value).is_file():
            raise SystemExit(f"refusing non-data or missing path: {value}")

    repository = repository_name()
    parent = api(repository, "GET", f"/git/ref/heads/{args.target_branch}")["object"]["sha"]
    base_tree = api(repository, "GET", f"/git/commits/{parent}")["tree"]["sha"]
    entries = []
    for value in paths:
        content = (ROOT / value).read_bytes()
        blob = api(repository, "POST", "/git/blobs", {"content": base64.b64encode(content).decode("ascii"), "encoding": "base64"})
        entries.append({"path": value, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    tree = api(repository, "POST", "/git/trees", {"base_tree": base_tree, "tree": entries})
    commit = api(repository, "POST", "/git/commits", {"message": args.message, "tree": tree["sha"], "parents": [parent]})
    ref_path = f"/git/refs/heads/{args.data_branch}"
    existing = subprocess.run(["gh", "api", f"repos/{repository}/git/ref/heads/{args.data_branch}"], cwd=ROOT, capture_output=True)
    if existing.returncode == 0:
        api(repository, "PATCH", ref_path, {"sha": commit["sha"], "force": True})
    else:
        api(repository, "POST", "/git/refs", {"ref": f"refs/heads/{args.data_branch}", "sha": commit["sha"]})
    print({"branch": args.data_branch, "commit": commit["sha"], "paths": paths})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
