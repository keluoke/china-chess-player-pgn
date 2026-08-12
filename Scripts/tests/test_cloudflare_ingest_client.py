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


if __name__ == "__main__":
    unittest.main()
