import hashlib
import json
import pathlib
import tempfile
import unittest
from unittest import mock

from Scripts.local import cloudflare_ingest


class CloudflareIngestClientTests(unittest.TestCase):
    def test_signed_headers_match_canonical_hmac(self):
        with mock.patch.object(cloudflare_ingest.time, "time", return_value=123), mock.patch.object(
            cloudflare_ingest.secrets, "token_hex", return_value="f" * 32
        ):
            headers = cloudflare_ingest.signed_headers("POST", "/v1/releases", "a" * 64, "secret")
        canonical = "POST\n/v1/releases\n123\n" + "f" * 32 + "\n" + "a" * 64
        expected = __import__("hmac").new(b"secret", canonical.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(headers["x-chess-signature"], expected)

    def test_bundle_hash_is_verified_before_upload(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            files = root / "files"
            candidate = files / "data/generated/chess-results-event-details/tnr12345.json"
            candidate.parent.mkdir(parents=True)
            candidate.write_bytes(b"{}")
            digest = hashlib.sha256(b"{}").hexdigest()
            manifest = {
                "schemaVersion": 1,
                "runId": "20260812-120000-deadbeef",
                "command": "event-queue",
                "baseCommit": "a" * 40,
                "source": {"source": "Chess-Results", "releasePolicy": "full-data"},
                "files": [{
                    "path": "data/generated/chess-results-event-details/tnr12345.json",
                    "operation": "upsert",
                    "sha256": digest,
                    "bytes": 2,
                    "baseBlobOid": None,
                    "baseSha256": None,
                }],
            }
            path = root / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            projected, uploads = cloudflare_ingest.build_shadow_manifest(path, files)
            self.assertEqual(projected["files"][0]["sha256"], digest)
            self.assertEqual(uploads[0][1], candidate)
            candidate.write_bytes(b"tampered")
            with self.assertRaisesRegex(cloudflare_ingest.ShadowDeliveryError, "RELEASE_HASH_MISMATCH"):
                cloudflare_ingest.build_shadow_manifest(path, files)

    def test_large_logical_manifest_is_split_into_bounded_registration_chunks(self):
        files = [
            {
                "path": f"data/generated/chess-results-event-details/tnr{index:05d}.json",
                "operation": "upsert",
                "sha256": hashlib.sha256(str(index).encode()).hexdigest(),
                "baseSha256": None,
                "bytes": 1,
            }
            for index in range(50)
        ]
        payload = {
            "schemaVersion": 1,
            "runId": "20260812-120000-deadbeef",
            "command": "event-queue",
            "baseCommit": "a" * 40,
            "source": {"source": "Chess-Results", "releasePolicy": "full-data"},
            "files": files,
        }
        cloudflare_ingest.validate_shadow_limits(payload)
        header, chunks = cloudflare_ingest.registration_payloads(payload)
        self.assertEqual(header["expectedFiles"], 50)
        self.assertEqual(header["expectedChunks"], 5)
        self.assertEqual(header["expectedUpserts"], 50)
        self.assertTrue(all(len(chunk["files"]) <= cloudflare_ingest.MAX_CHUNK_FILES for chunk in chunks))
        self.assertEqual([chunk["chunkIndex"] for chunk in chunks], list(range(5)))
        self.assertTrue(all(chunk["manifestSha256"] == header["manifestSha256"] for chunk in chunks))
        self.assertEqual([chunk["chunkSha256"] for chunk in chunks], header["chunkSha256s"])
        self.assertEqual(
            chunks[0]["chunkSha256"],
            hashlib.sha256(cloudflare_ingest.chunk_fingerprint_bytes(files[:10])).hexdigest(),
        )

    def test_free_tier_preflight_rejects_oversized_logical_manifest_locally(self):
        payload = {
            "files": [
                {"path": f"data/generated/chess-results-event-details/tnr{index:05d}.json", "bytes": 1}
                for index in range(cloudflare_ingest.MAX_RELEASE_FILES + 1)
            ]
        }
        with self.assertRaisesRegex(
            cloudflare_ingest.ShadowDeliveryError, "FREE_TIER_RELEASE_FILE_LIMIT"
        ):
            cloudflare_ingest.validate_shadow_limits(payload)

    def test_terminal_legacy_release_skips_chunk_and_upload_replay(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            run_id = "20260812-120000-deadbeef"
            bundle = root / "outbox" / run_id
            candidate = bundle / "files/data/generated/event-completeness-report.json"
            candidate.parent.mkdir(parents=True)
            candidate.write_bytes(b"{}")
            digest = hashlib.sha256(b"{}").hexdigest()
            manifest = {
                "schemaVersion": 1,
                "runId": run_id,
                "command": "event-queue",
                "baseCommit": "a" * 40,
                "source": {"source": "Chess-Results", "releasePolicy": "full-data"},
                "files": [{
                    "path": "data/generated/event-completeness-report.json",
                    "operation": "upsert",
                    "sha256": digest,
                    "bytes": 2,
                    "baseSha256": None,
                }],
            }
            (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with mock.patch.object(
                cloudflare_ingest,
                "request_json",
                side_effect=[
                    {"ok": True, "status": "complete", "legacy": True},
                    {"ok": True, "status": "complete", "snapshot_id": "legacy-snapshot"},
                ],
            ) as request:
                result = cloudflare_ingest.deliver(run_id, "https://shadow.invalid", "secret", root)
            self.assertEqual(result["snapshot_id"], "legacy-snapshot")
            self.assertEqual(request.call_count, 2)

    def test_truly_oversized_bundle_writes_receipt_without_network_request(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            manifest_path = root / "manifest.json"
            manifest_path.write_text("{}", encoding="utf-8")
            payload = {
                "files": [
                    {"path": f"data/generated/tnr{index}.json", "bytes": 1}
                    for index in range(cloudflare_ingest.MAX_RELEASE_FILES + 1)
                ]
            }
            with (
                mock.patch.object(
                    cloudflare_ingest,
                    "bundle_paths",
                    return_value=(manifest_path, root / "files"),
                ),
                mock.patch.object(
                    cloudflare_ingest,
                    "build_shadow_manifest",
                    return_value=(payload, []),
                ),
                mock.patch.object(cloudflare_ingest, "request_json") as request,
            ):
                with self.assertRaisesRegex(
                    cloudflare_ingest.ShadowDeliveryError,
                    "FREE_TIER_RELEASE_FILE_LIMIT",
                ):
                    cloudflare_ingest.deliver("run-1", "https://shadow.invalid", "secret")
            request.assert_not_called()
            state = json.loads(
                (root / "shadow-delivery.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["status"], "ineligible")
            self.assertEqual(state["errorCode"], "FREE_TIER_RELEASE_FILE_LIMIT")


if __name__ == "__main__":
    unittest.main()
