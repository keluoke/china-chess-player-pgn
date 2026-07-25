#!/usr/bin/env python3
"""Manifest-driven API fallback for delivering an outbox release bundle.

This is the deterministic fallback when every Git route to github.com fails.
It consumes an existing, immutable outbox bundle (exact release manifest plus
hashed file contents) and force-moves the single-writer ``local-data`` ref via
the GitHub Git Database API. The same ``run_manager.validate_manifest`` policy
gate is applied — there is no second, wider allowlist. Ad-hoc ``--path``
publication is permanently retired: without a validated bundle this tool
fails closed.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import urllib.parse

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent))

import run_manager  # noqa: E402


def repository_name() -> str:
    remote = subprocess.run(
        ["git", "remote", "get-url", "origin"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
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


def load_bundle(run_id: str | None) -> tuple[pathlib.Path, dict, dict]:
    entries = run_manager.outbox_entries("pending")
    if run_id:
        entries = [item for item in entries if item.get("runId") == run_id]
    if not entries:
        raise SystemExit(
            "API_DELIVERY_BLOCKED: outbox 没有待投递发布包。此工具已退役任意 --path 发布；"
            "请先通过 refresh.sh registry/bulk 生成经过校验的 release 包。"
        )
    delivery = entries[0]
    entry = pathlib.Path(delivery["path"])
    manifest = json.loads((entry / "manifest.json").read_text(encoding="utf-8"))
    return entry, manifest, delivery


def git_blob_oid_for_bytes(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content).hexdigest()


def remote_blob_oid(repository: str, commit: str, path: str) -> str | None:
    encoded_path = urllib.parse.quote(path, safe="/")
    encoded_ref = urllib.parse.quote(commit, safe="")
    result = subprocess.run(
        [
            "gh",
            "api",
            f"repos/{repository}/contents/{encoded_path}?ref={encoded_ref}",
            "--jq",
            ".sha",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=360,
    )
    if result.returncode == 0:
        return result.stdout.strip() or None
    detail = f"{result.stdout}\n{result.stderr}"
    if "HTTP 404" in detail or "Not Found" in detail:
        return None
    raise SystemExit(f"API_DELIVERY_BLOCKED: 无法读取远端基线 {commit}:{path}: {detail.strip()}")


def release_conflicts(base_oid: str | None, current_oid: str | None, candidate_oid: str | None) -> bool:
    return current_oid != base_oid and current_oid != candidate_oid


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outbox", metavar="RUN_ID", help="deliver this pending outbox bundle (default: oldest)")
    parser.add_argument("--data-branch", default="local-data")
    parser.add_argument("--base-branch", default="main")
    args = parser.parse_args()

    entry, manifest, _delivery = load_bundle(args.outbox)
    # Fail closed on anything outside the shared machine-release policy.
    files = run_manager.validate_manifest(manifest)
    run_id = str(manifest.get("runId"))

    repository = repository_name()
    parent = api(repository, "GET", f"/git/ref/heads/{args.base_branch}")["object"]["sha"]
    base_tree = api(repository, "GET", f"/git/commits/{parent}")["tree"]["sha"]

    prepared: list[tuple[dict, bytes | None, str | None]] = []
    conflicts: list[str] = []
    for item in files:
        path = item["path"]
        content = None
        candidate_oid = None
        if item["operation"] == "upsert":
            source = entry / "files" / path
            if not source.is_file():
                raise SystemExit(f"API_DELIVERY_BLOCKED: outbox 缺少文件内容：{path}")
            content = source.read_bytes()
            if hashlib.sha256(content).hexdigest() != item.get("sha256"):
                raise SystemExit(f"RELEASE_HASH_MISMATCH: outbox 文件与 manifest 哈希不符：{path}")
            candidate_oid = git_blob_oid_for_bytes(content)

        current_oid = remote_blob_oid(repository, parent, path)
        if "baseBlobOid" not in item:
            # A legacy bundle has no trustworthy collector baseline. It is
            # safe through the API only when the desired state is already
            # present; otherwise retain it in outbox for the Git transport.
            if current_oid != candidate_oid:
                raise SystemExit(
                    f"API_DELIVERY_BASELINE_MISSING: legacy 发布包无法证明远端基线：{path}"
                )
        elif release_conflicts(item.get("baseBlobOid"), current_oid, candidate_oid):
            conflicts.append(path)

        item["deliveryBaseBlobOid"] = current_oid
        if current_oid == item.get("baseBlobOid"):
            item["deliveryBaseSha256"] = item.get("baseSha256")
        elif current_oid == candidate_oid and item["operation"] == "upsert":
            item["deliveryBaseSha256"] = item.get("sha256")
        else:
            item["deliveryBaseSha256"] = None
        prepared.append((item, content, candidate_oid))

    if conflicts:
        raise SystemExit(
            "RELEASE_BASE_CONFLICT: main 已修改以下路径，API 候选已隔离且未创建远端对象："
            + ", ".join(conflicts[:8])
        )

    manifest["deliveryBaseCommit"] = parent
    # Re-run the shared validator after adding the delivery baseline.
    files = run_manager.validate_manifest(manifest)

    tree_entries: list[dict] = []
    for item, content, _candidate_oid in prepared:
        path = item["path"]
        if item["operation"] == "delete":
            tree_entries.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
            continue
        assert content is not None
        blob = api(repository, "POST", "/git/blobs", {
            "content": base64.b64encode(content).decode("ascii"),
            "encoding": "base64",
        })
        tree_entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    # The manifest itself rides along so cloud ingest can validate it.
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    manifest_blob = api(repository, "POST", "/git/blobs", {
        "content": base64.b64encode(manifest_bytes).decode("ascii"),
        "encoding": "base64",
    })
    tree_entries.append({
        "path": run_manager.MANIFEST_PATH, "mode": "100644", "type": "blob", "sha": manifest_blob["sha"],
    })

    tree = api(repository, "POST", "/git/trees", {"base_tree": base_tree, "tree": tree_entries})
    commit = api(repository, "POST", "/git/commits", {
        "message": f"Deliver validated local release {run_id} via API",
        "tree": tree["sha"],
        "parents": [parent],
    })
    ref_path = f"/git/refs/heads/{args.data_branch}"
    existing = subprocess.run(
        ["gh", "api", f"repos/{repository}/git/ref/heads/{args.data_branch}"], cwd=ROOT, capture_output=True
    )
    if existing.returncode == 0:
        api(repository, "PATCH", ref_path, {"sha": commit["sha"], "force": True})
    else:
        api(repository, "POST", "/git/refs", {"ref": f"refs/heads/{args.data_branch}", "sha": commit["sha"]})
    run_manager.outbox_update(run_id, "pushed", commit["sha"], "api", None)
    print(json.dumps({
        "branch": args.data_branch,
        "commit": commit["sha"],
        "runId": run_id,
        "files": len(files),
        "transport": "github-git-database-api",
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
