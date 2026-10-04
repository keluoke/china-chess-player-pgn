"""Aggregate release preview reads authenticated current-version evidence only."""
import base64
import copy
import json
from pathlib import Path
import sys
import unittest

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Scripts"))
import brilliancy_publication_preview as preview
import brilliancy_quality as quality
from brilliancy_quality_backup import QualityBackup, record_key
from brilliancy_queue import R2Store, new_state
from Scripts.tests.test_brilliancy_publication_gate import proof
from Scripts.tests.test_brilliancy_queue import FakeS3


class PublicationPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.item = json.loads((ROOT / "data/manual/brilliancies/curated.json").read_text())["items"][1]

    def test_missing_identity_does_not_change_quality_tier(self):
        item = copy.deepcopy(self.item)
        item["white"]["playerId"] = None
        item["black"]["playerId"] = None
        item["event"]["id"] = "event-unknown"
        first = (quality.digest(item), proof(item, 1_000_000))
        deep = (quality.digest(item), proof(item, 5_000_000))
        self.assertEqual(preview.assess_shard({item["id"]: item},
                        {item["id"]: first}, {item["id"]: deep}), {"A": 1})

    def test_input_change_isolated_and_missing_deep_pending(self):
        item = self.item
        cid = item["id"]
        first = (quality.digest(item), proof(item, 1_000_000))
        self.assertEqual(preview.assess_shard({cid: item}, {cid: first}, {}),
                         {"pending-independent-evidence": 1})
        self.assertEqual(preview.assess_shard({cid: item}, {cid: first},
                         {cid: ("0" * 64, proof(item, 5_000_000))}),
                         {"isolated-input-conflict": 1})

    def test_history_baseline_must_be_complete_and_duplicates_exact(self):
        state = {"shards": 2, "activeVersion": "v", "baselineGames": ["game"],
                 "records": {"game": {"status": "retry", "version": "v",
                                      "candidates": [self.item]}}}
        with self.assertRaisesRegex(ValueError, "QC_PREVIEW_HISTORY_INCOMPLETE"):
            preview.source_candidates(state, "history")
        state["records"]["game"]["status"] = "complete"
        state["records"]["another"] = {"status": "complete", "version": "v",
                                         "candidates": [self.item]}
        items, pending = preview.source_candidates(state, "history")
        self.assertEqual((len(items), pending), (1, 0))
        different = copy.deepcopy(self.item)
        different["game"]["result"] = "*"
        state["records"]["another"]["candidates"] = [different]
        with self.assertRaisesRegex(ValueError, "QC_PREVIEW_CANDIDATE_CONFLICT"):
            preview.source_candidates(state, "history")

    def test_encrypted_r2_preview_cannot_publish_and_rejects_profile_drift(self):
        client = FakeS3()
        key_bytes = bytes(range(32))
        key = base64.b64encode(key_bytes).decode()
        cid = self.item["id"]
        game_id = "a" * 64
        for lane in ("history", "incremental"):
            for shard in (0, 1):
                state = new_state(shard)
                state["activeVersion"] = "v"
                state["baselineGames"] = [game_id] if lane == "history" else []
                if lane == "history":
                    state["records"][game_id] = {"status": "complete", "version": "v",
                                                "candidates": [self.item] if shard == 0 else []}
                R2Store(client, "chess-data", shard, key, lane).save(state)
        for quality_lane, nodes in (("first", 1_000_000), ("deep", 5_000_000)):
            profile = preview.PROFILES["history"][quality_lane]
            outcome = proof(self.item, nodes)
            outcome["grade"] = "A"
            nonce = bytes([nodes // 1_000_000] * 12)
            encrypted = nonce + AESGCM(key_bytes).encrypt(
                nonce, json.dumps(outcome).encode(), (cid + profile).encode())
            row = {"status": "complete", "version": profile, "candidates": [{
                "candidateId": cid, "inputHash": quality.digest(self.item),
                "profile": profile, "grade": "A",
                "encrypted": base64.b64encode(encrypted).decode()}]}
            backup = QualityBackup(client, "chess-data", 0, key, quality_lane)
            state = new_state(0)
            state["records"][record_key(cid, profile)] = row
            backup.store.save(state)
        result = preview.inspect(client, "chess-data", key)
        self.assertFalse(result["publicAutoPublish"])
        self.assertEqual(result["lanes"]["history"]["certifiedEvidenceTiers"], {"A": 1})
        self.assertEqual(result["uniqueCandidatePositions"], 1)
        self.assertNotIn(cid, json.dumps(result))
        altered = copy.deepcopy(row)
        altered["version"] = "f" * 64
        altered["candidates"][0]["profile"] = "f" * 64
        nonce = b"\x09" * 12
        altered["candidates"][0]["encrypted"] = base64.b64encode(
            nonce + AESGCM(key_bytes).encrypt(nonce, json.dumps(outcome).encode(),
                                             (cid + "f" * 64).encode())).decode()
        backup = QualityBackup(client, "chess-data", 0, key, "deep")
        state = new_state(0)
        state["records"][record_key(cid, "f" * 64)] = altered
        backup.store.load()
        backup.store.save(state)
        with self.assertRaisesRegex(ValueError, "QC_PREVIEW_PROFILE_DRIFT"):
            preview.inspect(client, "chess-data", key)


if __name__ == "__main__":
    unittest.main()
