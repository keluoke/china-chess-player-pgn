"""Build and certify immutable event PGN objects from published, verified inputs."""
from __future__ import annotations
import argparse
import json
import pathlib
import sys

from r2_object_transaction import content_addressed_key, sha256_file
from snapshot_context import snapshot_id
from stable_json import write_json

ROOT = pathlib.Path(__file__).resolve().parents[1]
PREFIX = "events/chess-results"
SOURCE = ROOT / "data/generated/chess-results-event-pgn"
MANIFEST = ROOT / "docs/data/index/event-pgn-objects.json"
RECEIPT = ROOT / "docs/data/index/event-pgn-r2-receipt.json"


def build():
    old = json.loads((ROOT / "data/generated/r2-object-receipts/events--chess-results.json").read_text())
    published = {p.stem[3:] for p in (ROOT / "docs/data/index/event-details").glob("tnr*.json")}
    entries = {}
    for row in old.get("objects", []):
        name = pathlib.PurePosixPath(row.get("key", "")).name
        if not name.startswith("tnr") or not name.endswith(".pgn") or not name[3:-4].isdigit():
            continue
        tid = name[3:-4]
        if tid not in published:
            continue
        path = SOURCE / name
        if not path.is_file():
            raise ValueError(f"EVENT_PGN_SOURCE_MISSING: {tid}")
        digest = sha256_file(path)
        size = path.stat().st_size
        if digest != row.get("sha256") or size != row.get("bytes"):
            raise ValueError(f"EVENT_PGN_INPUT_RECEIPT_MISMATCH: {tid}")
        key = content_addressed_key(PREFIX, digest)
        entries[tid] = {"path": f"{PREFIX}/{name}", "key": key, "sha256": digest,
                        "bytes": size, "publicURL": f"https://data.chessdb.aigclabs.cc/{key}"}
    write_json(MANIFEST, {"schemaVersion": 1, "snapshotId": snapshot_id(), "events": entries,
                         "totals": {"packages": len(entries), "bytes": sum(r['bytes'] for r in entries.values())}})
    return entries


def validate():
    from validate_player_pgn_r2_receipt import validate as validate_receipt
    manifest = json.loads(MANIFEST.read_text())
    expected = {}
    for tid, row in manifest['events'].items():
        if not tid.isdigit() or row['path'] != f"{PREFIX}/tnr{tid}.pgn":
            raise ValueError("EVENT_PGN_PATH_INVALID")
        if row['key'] != content_addressed_key(PREFIX, row['sha256']):
            raise ValueError("EVENT_PGN_OBJECT_INVALID")
        if row.get('publicURL') != f"https://data.chessdb.aigclabs.cc/{row['key']}":
            raise ValueError('EVENT_PGN_URL_INVALID')
        expected[row['path']] = row
    return validate_receipt(expected_override=expected, prefix=PREFIX, receipt_field="eventObjects",
                            receipt_path=RECEIPT, pgn_root=SOURCE, package_manifest_path=MANIFEST,
                            snapshot_path=ROOT / "docs/data/snapshot.json")


def certify():
    sys.path.insert(0, str(ROOT / "Scripts/local"))
    import upload_bulk_to_r2 as uploader
    import boto3
    from botocore.config import Config
    config = uploader.load_secrets()
    if config.get('R2_BUCKET') != 'chess-data':
        raise ValueError('R2_BUCKET_INVALID')
    client = boto3.client('s3', endpoint_url=config['R2_ENDPOINT'],
                          aws_access_key_id=config['R2_ACCESS_KEY_ID'],
                          aws_secret_access_key=config['R2_SECRET_ACCESS_KEY'],
                          config=Config(signature_version='s3v4', retries={'max_attempts': 3}))
    manifest = json.loads(MANIFEST.read_text())
    files = [SOURCE / pathlib.PurePosixPath(row['path']).name for row in manifest['events'].values()]
    status = uploader.run_content_addressed_upload(client=client, bucket=config['R2_BUCKET'],
              endpoint=config['R2_ENDPOINT'], prefix=PREFIX, source_root=SOURCE, files=files,
              receipt_path=RECEIPT, receipt_field='eventObjects', workers=4, publish_aliases=False,
              verify_only=False, verify_body=True, dry_run=False, audit_sample=384)
    if status:
        raise SystemExit(status)
    return validate()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--certify', action='store_true')
    parser.add_argument('--validate', action='store_true')
    args = parser.parse_args()
    result = certify() if args.certify else validate() if args.validate else build()
    print(json.dumps({'eventPGN': len(result) if not args.certify and not args.validate else result}))
