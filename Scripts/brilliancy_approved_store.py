"""Validate the immutable public-input layer for machine-approved brilliancies.

This loader grants no publication authority: the separate R2 release producer
must authenticate engine evidence before writing these files. The rebuild only
accepts an exact, hash-listed bundle with the pinned quality rule.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re

import brilliancy_publication_gate as gate

DIR = Path("data/generated/brilliancies-approved")
BUCKET = re.compile(r"[0-9a-f]{2}\.json\Z")
BRILLIANCY_ID = re.compile(r"br-[0-9a-f]{64}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def load(root: Path, *, allow_unreleased: bool = False) -> list[dict]:
    folder = root / DIR
    if not folder.exists():
        return []
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError("BRILLIANCY_APPROVED_DIR_INVALID")
    manifest_path = folder / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("BRILLIANCY_APPROVED_MANIFEST_MISSING")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("schemaVersion") != 1
            or manifest.get("qualityRuleVersion") != gate.RULE_VERSION
            or not isinstance(manifest.get("files"), list)
            or not isinstance(manifest.get("totalItems"), int)):
        raise ValueError("BRILLIANCY_APPROVED_MANIFEST_INVALID")
    rows = manifest["files"]
    names = [row.get("path") for row in rows if isinstance(row, dict)]
    actual = {p.name for p in folder.iterdir() if p.name != "manifest.json"}
    if (len(names) != len(rows) or len(names) != len(set(names))
            or actual != set(names) or any(not isinstance(n, str) or not BUCKET.fullmatch(n)
                                                for n in names)):
        raise ValueError("BRILLIANCY_APPROVED_FILE_SET_INVALID")
    result = []
    seen = set()
    for row in rows:
        path = folder / row["path"]
        if path.is_symlink() or not path.is_file():
            raise ValueError("BRILLIANCY_APPROVED_FILE_INVALID")
        raw = path.read_bytes()
        if (row.get("bytes") != len(raw)
                or row.get("sha256") != hashlib.sha256(raw).hexdigest()):
            raise ValueError("BRILLIANCY_APPROVED_HASH_MISMATCH")
        bucket = path.stem
        payload = json.loads(raw)
        if (payload.get("schemaVersion") != 1 or payload.get("bucket") != bucket
                or not isinstance(payload.get("items"), list)):
            raise ValueError("BRILLIANCY_APPROVED_BUCKET_INVALID")
        for original in payload["items"]:
            if not isinstance(original, dict):
                raise ValueError("BRILLIANCY_APPROVED_ITEM_INVALID")
            item = copy.deepcopy(original)
            item_id = item.get("id")
            approval = item.pop("approval", None)
            if (not isinstance(item_id, str) or not BRILLIANCY_ID.fullmatch(item_id)
                    or item_id[3:5] != bucket or item_id in seen
                    or item.get("status") not in ("published", "withdrawn")
                    or not isinstance(approval, dict)
                    or approval.get("tier") not in ("S", "A")
                    or approval.get("ruleVersion") != gate.RULE_VERSION
                    or not isinstance(approval.get("candidateHash"), str)
                    or not SHA256.fullmatch(approval["candidateHash"])
                    or item.get("classification", {}).get("symbol") != "!!"
                    or item.get("classification", {}).get("ruleVersion") != gate.RULE_VERSION):
                raise ValueError("BRILLIANCY_APPROVED_ITEM_INVALID")
            seen.add(item_id)
            item["qualityTier"] = approval["tier"]
            item["qualityRuleVersion"] = gate.RULE_VERSION
            result.append(item)
    if len(result) != manifest["totalItems"]:
        raise ValueError("BRILLIANCY_APPROVED_TOTAL_MISMATCH")
    if result and not allow_unreleased:
        raise ValueError("BRILLIANCY_AUTO_PUBLICATION_CLOSED")
    return result
