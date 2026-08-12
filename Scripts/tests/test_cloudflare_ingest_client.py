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

    def test_free_tier_preflight_rejects_oversized_manifest_locally(self):
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

    def test_ineligible_bundle_writes_receipt_without_network_request(self):
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
