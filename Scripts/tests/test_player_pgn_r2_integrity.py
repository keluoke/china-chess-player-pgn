from __future__ import annotations

import hashlib
import io
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Scripts"))
sys.path.insert(0, str(ROOT / "Scripts" / "local"))

import build_static_player_pgn as builder  # noqa: E402
import upload_bulk_to_r2 as uploader  # noqa: E402
import validate_player_pgn_r2_receipt as validator  # noqa: E402


class PlayerPgnR2IntegrityTests(unittest.TestCase):
    def test_content_addressed_key_contains_full_hash(self) -> None:
        sha = "ab" * 32
        self.assertEqual(
            uploader.content_addressed_key("data/pgn", sha, ".pgn"),
            f"data/pgn/objects/sha256/ab/{sha}.pgn",
        )
        self.assertEqual(builder.content_addressed_pgn_path(sha), f"data/pgn/objects/sha256/ab/{sha}.pgn")
        with self.assertRaises(ValueError):
            uploader.content_addressed_key("data/pgn", "short", ".pgn")

    def test_aggregate_buckets_are_written_after_stale_details_are_pruned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            index_root = root / "index/by-player"
            bucket_root = root / "index/by-player-buckets"
            pgn_root = root / "pgn/by-player"
            index_root.mkdir(parents=True)
            stale = index_root / "fide-2.json"
            stale.write_text(json.dumps({
                "player": {"fideID": "2"},
                "packages": [{"pgnPath": "data/pgn/by-player/fide-2/all.pgn"}],
            }), encoding="utf-8")
            current = builder.PlayerBucket(builder.PlayerProfile("1", display_name="One"))
            current.add(builder.PlayerGame(
                pgn='[Event "fixture"]\n[White "One"]\n[Black "Two"]\n[Result "*"]\n\n*\n',
                event="fixture", date="2026-08-24", white="One", black="Two",
                result="*", source="fixture", sha256="game-1",
            ))
            with mock.patch.object(builder, "OUTPUT_INDEX_ROOT", index_root), \
                 mock.patch.object(builder, "OUTPUT_BUCKET_ROOT", bucket_root), \
                 mock.patch.object(builder, "OUTPUT_PGN_ROOT", pgn_root), \
                 mock.patch.object(builder, "DOCS_DATA", root), \
                 mock.patch.object(builder, "snapshot_id", return_value="snapshot-fixture"):
                builder.write_outputs(
                    {"1": current}, dry_run=False, existing_packages={}, write_aggregates=True
                )
            self.assertFalse(stale.exists())
            bucket_players = {}
            for path in bucket_root.glob("*.json"):
                bucket_players.update(json.loads(path.read_text())["players"])
            self.assertEqual(set(bucket_players), {"1"})
            package = bucket_players["1"]["packages"][0]
            self.assertRegex(package["objectPath"], r"^data/pgn/objects/sha256/[0-9a-f]{2}/")

    def test_receipt_requires_exact_package_coverage_and_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            buckets = root / "buckets"
            pgn_root = root / "pgn"
            buckets.mkdir()
            target = pgn_root / "by-player" / "fide-1" / "all.pgn"
            target.parent.mkdir(parents=True)
            target.write_text("[Event \"fixture\"]\n\n*\n", encoding="utf-8")
            body = target.read_bytes()
            sha = hashlib.sha256(body).hexdigest()
            object_path = f"data/pgn/objects/sha256/{sha[:2]}/{sha}.pgn"
            input_commit = "a" * 40
            snapshot = {"snapshotId": "snapshot-fixture", "inputCommit": input_commit}
            package = {
                "id": "all",
                "pgnPath": "data/pgn/by-player/fide-1/all.pgn",
                "objectPath": object_path,
                "publicURL": f"https://data.chessdb.aigclabs.cc/{object_path}",
                "sha256": sha,
                "pgnBytes": len(body),
            }
            (buckets / "01.json").write_text(json.dumps({
                "snapshotId": "snapshot-fixture",
                "players": {"1": {"packages": [package]}},
            }), encoding="utf-8")
            receipt_path = root / "receipt.json"
            receipt = {
                "schemaVersion": 3,
                "bucket": "chess-data",
                "endpoint": "https://fixture.r2.cloudflarestorage.com",
                "contentAddressed": True,
                "bodyVerified": True,
                "bodyCertified": True,
                "objectPattern": "data/pgn/objects/sha256/<first-two>/<sha256>.pgn",
                "snapshotId": "snapshot-fixture",
                "inputCommit": input_commit,
                "inventory": {
                    "prefix": "data/pgn/objects/sha256/",
                    "expectedKeys": 1,
                    "presentKeys": 1,
                    "pages": 1,
                    "missingKeys": 0,
                    "sizeMismatches": 0,
                },
                "audit": {
                    "startCursor": 0,
                    "nextCursor": 0,
                    "sampleSize": 1,
                    "auditedKeys": [object_path],
                },
                "quota": {
                    "classARequests": 1,
                    "classBRequests": 1,
                    "maxClassA": 10000,
                    "maxClassB": 10000,
                },
                "playerObjects": [{
                    "path": package["pgnPath"],
                    "key": object_path,
                    "sha256": sha,
                    "bytes": len(body),
                    "publicURL": package["publicURL"],
                    "verified": "body-sha256",
                    "bodyVerifiedAtSnapshot": "snapshot-fixture",
                }],
            }
            valid_receipt = json.loads(json.dumps(receipt))
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            snapshot_path = root / "snapshot.json"
            snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
            package_manifest = root / "manifest.json"
            package_manifest.write_text(json.dumps({
                "snapshotId": "snapshot-fixture",
                "totals": {"packages": 1, "bytes": len(body)},
            }), encoding="utf-8")
            self.assertEqual(
                validator.validate(
                    bucket_root=buckets,
                    receipt_path=receipt_path,
                    snapshot_path=snapshot_path,
                    pgn_root=pgn_root,
                    package_manifest_path=package_manifest,
                ),
                {"packages": 1, "bytes": len(body)},
            )
            receipt["playerObjects"][0]["sha256"] = "00" * 32
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "receipt row mismatch"):
                validator.validate(
                    bucket_root=buckets,
                    receipt_path=receipt_path,
                    snapshot_path=snapshot_path,
                    pgn_root=pgn_root,
                    package_manifest_path=package_manifest,
                )

            receipt = json.loads(json.dumps(valid_receipt))
            receipt["playerObjects"][0]["verified"] = "immutable-key-metadata"
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "receipt row mismatch"):
                validator.validate(
                    bucket_root=buckets,
                    receipt_path=receipt_path,
                    snapshot_path=snapshot_path,
                    pgn_root=pgn_root,
                    package_manifest_path=package_manifest,
                )

            receipt = json.loads(json.dumps(valid_receipt))
            receipt["inputCommit"] = "b" * 40
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "input commit mismatch"):
                validator.validate(
                    bucket_root=buckets,
                    receipt_path=receipt_path,
                    snapshot_path=snapshot_path,
                    pgn_root=pgn_root,
                    package_manifest_path=package_manifest,
                )

            escaped = json.loads((buckets / "01.json").read_text(encoding="utf-8"))
            escaped["players"]["1"]["packages"][0]["pgnPath"] = (
                "data/pgn/by-player/../escape.pgn"
            )
            (buckets / "01.json").write_text(json.dumps(escaped), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid or duplicate package path"):
                validator.expected_packages(buckets)


class _Paginator:
    def __init__(self, client):
        self.client = client

    def paginate(self, **_kwargs):
        yield {"Contents": [
            {"Key": key, "Size": len(value["body"])}
            for key, value in sorted(self.client.objects.items())
        ]}


class _FakeS3:
    def __init__(self, objects=None):
        self.objects = objects or {}
        self.gets = []
        self.uploads = []

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return _Paginator(self)

    def upload_file(self, path, _bucket, key, ExtraArgs):
        body = pathlib.Path(path).read_bytes()
        self.objects[key] = {"body": body, "metadata": dict(ExtraArgs["Metadata"])}
        self.uploads.append(key)

    def get_object(self, Bucket, Key):  # noqa: ARG002
        self.gets.append(Key)
        if Key not in self.objects:
            raise RuntimeError("NoSuchKey 404")
        value = self.objects[Key]
        return {
            "Body": io.BytesIO(value["body"]),
            "ContentLength": len(value["body"]),
            "Metadata": value.get("metadata") or {},
        }


class ContentAddressedUploadTests(unittest.TestCase):
    def fixture(self, root: pathlib.Path):
        source = root / "docs/data/pgn/by-player/fide-1"
        source.mkdir(parents=True)
        package = source / "all.pgn"
        package.write_text('[Event "fixture"]\n\n*\n', encoding="utf-8")
        snapshot = root / "docs/data/snapshot.json"
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_text(json.dumps({
            "snapshotId": "snapshot-upload",
            "inputCommit": "a" * 40,
        }), encoding="utf-8")
        receipt = root / "receipt.json"
        sha = hashlib.sha256(package.read_bytes()).hexdigest()
        key = f"data/pgn/objects/sha256/{sha[:2]}/{sha}.pgn"
        return source.parents[1], package, receipt, sha, key

    def run_upload(self, root, client, source_root, package, receipt, *, audit=0):
        with mock.patch.object(uploader, "ROOT", root):
            return uploader.run_content_addressed_upload(
                client=client,
                bucket="chess-data",
                prefix="data/pgn",
                source_root=source_root,
                files=[package],
                receipt_path=receipt,
                receipt_field="playerObjects",
                workers=1,
                publish_aliases=False,
                verify_only=False,
                verify_body=False,
                dry_run=False,
                audit_sample=audit,
                endpoint="https://fixture.r2.cloudflarestorage.com",
            )

    def test_new_object_is_uploaded_and_body_read_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            source_root, package, receipt, _sha, key = self.fixture(root)
            client = _FakeS3()
            self.assertEqual(self.run_upload(root, client, source_root, package, receipt), 0)
            self.assertEqual(client.uploads, [key])
            self.assertEqual(client.gets, [key])
            payload = json.loads(receipt.read_text())
            self.assertTrue(payload["bodyCertified"])
            self.assertEqual(payload["schemaVersion"], 3)

    def test_valid_prior_receipt_uses_inventory_and_rotating_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            source_root, package, receipt, sha, key = self.fixture(root)
            client = _FakeS3({key: {"body": package.read_bytes(), "metadata": {"sha256": sha}}})
            self.assertEqual(self.run_upload(root, client, source_root, package, receipt, audit=1), 0)
            client.gets.clear()
            self.assertEqual(self.run_upload(root, client, source_root, package, receipt, audit=0), 0)
            self.assertEqual(client.gets, [])
            self.assertEqual(self.run_upload(root, client, source_root, package, receipt, audit=1), 0)
            self.assertEqual(client.gets, [key])

    def test_corrupt_existing_body_fails_without_overwrite_or_receipt_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            source_root, package, receipt, sha, key = self.fixture(root)
            client = _FakeS3({key: {"body": b"x" * package.stat().st_size, "metadata": {"sha256": sha}}})
            receipt.write_text('{"sentinel":true}\n', encoding="utf-8")
            before = receipt.read_bytes()
            self.assertEqual(self.run_upload(root, client, source_root, package, receipt), 1)
            self.assertEqual(client.uploads, [])
            self.assertEqual(receipt.read_bytes(), before)

    def test_wrong_bucket_receipt_is_not_reused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            source_root, package, receipt, sha, key = self.fixture(root)
            client = _FakeS3({key: {"body": package.read_bytes(), "metadata": {"sha256": sha}}})
            self.assertEqual(self.run_upload(root, client, source_root, package, receipt), 0)
            payload = json.loads(receipt.read_text())
            payload["bucket"] = "wrong"
            receipt.write_text(json.dumps(payload), encoding="utf-8")
            client.gets.clear()
            self.assertEqual(self.run_upload(root, client, source_root, package, receipt), 0)
            self.assertEqual(client.gets, [key])


if __name__ == "__main__":
    unittest.main()
