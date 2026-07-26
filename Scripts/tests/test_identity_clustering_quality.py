#!/usr/bin/env python3

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Scripts"))

import validate_identity_clustering as vic  # noqa: E402


class IdentityClusteringQualityTest(unittest.TestCase):
    def test_conflicting_roster_key_stays_excluded_after_later_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            event_root = pathlib.Path(tmp)
            (event_root / "tnr1.json").write_text(
                json.dumps({
                    "tournamentID": "1",
                    "players": [
                        {"playerNo": "7", "fideID": "100", "name": "张三"},
                        {"playerNo": "7", "fideID": "200", "name": "张三"},
                    ],
                    "standings": [
                        {"playerNo": "7", "fideID": "100", "name": "张三"},
                    ],
                }),
                encoding="utf-8",
            )
            _, roster_truth = vic.load_truth(event_root)
        self.assertNotIn(("1", "7"), roster_truth)

    def test_chinese_name_baseline_reports_collisions_without_hiding_recall(self):
        rows = [
            {"nameKey": "张三", "fideID": "1"},
            {"nameKey": "张三", "fideID": "1"},
            {"nameKey": "张三", "fideID": "2"},
            {"nameKey": "李四", "fideID": "3"},
            {"nameKey": "李四", "fideID": "3"},
        ]
        metrics = vic.pairwise_name_metrics(rows)
        self.assertEqual(metrics["ambiguousNames"], 1)
        self.assertEqual(metrics["affectedPlayers"], 2)
        self.assertEqual(metrics["predictedPairs"], 4)
        self.assertEqual(metrics["correctPairs"], 2)
        self.assertEqual(metrics["truthPairs"], 2)
        self.assertEqual(metrics["precision"], 0.5)
        self.assertEqual(metrics["recall"], 1.0)

    def test_projection_precision_uses_embedded_fide_truth(self):
        players = [
            {"domesticID": "a", "sightings": []},
            {"domesticID": "b", "sightings": []},
            {"domesticID": "c", "sightings": []},
        ]
        groups = [
            {"groupID": "good", "members": ["a", "b"]},
            {"groupID": "wrong", "canonicalFideID": "9", "members": ["c"]},
        ]
        metrics = vic.projection_metrics(
            players,
            groups,
            {"a": "1", "b": "1", "c": "8"},
        )
        self.assertEqual(metrics["evaluatedEdges"], 2)
        self.assertEqual(metrics["correctEdges"], 1)
        self.assertEqual(metrics["incorrectGroups"], 1)
        self.assertEqual(metrics["precision"], 0.5)

    def test_age_stage_reversal_is_a_hard_conflict(self):
        earlier = {
            "domesticID": "a",
            "sightings": [{
                "eventID": "chess-results-tnr1",
                "eventDate": "2024-01-01",
                "ageStage": "U14",
            }],
        }
        later = {
            "domesticID": "b",
            "sightings": [{
                "eventID": "chess-results-tnr2",
                "eventDate": "2025-01-01",
                "ageStage": "U8",
            }],
        }
        self.assertIn("age-stage-conflict", vic.payload_hard_conflicts(earlier, later))

    def test_sex_mismatch_is_not_a_hard_conflict(self):
        left = {
            "domesticID": "a",
            "sightings": [{"eventID": "tnr1", "sex": "M"}],
        }
        right = {
            "domesticID": "b",
            "sightings": [{"eventID": "tnr2", "sex": "F"}],
        }
        self.assertEqual(vic.payload_hard_conflicts(left, right), [])

    def test_same_event_only_conflicts_when_roster_slots_differ(self):
        left = {
            "domesticID": "a",
            "sightings": [{"eventID": "tnr1", "playerNo": "7"}],
        }
        same_slot = {
            "domesticID": "b",
            "sightings": [{
                "eventID": "chess-results-tnr1",
                "playerNo": "7",
            }],
        }
        different_slot = {
            "domesticID": "c",
            "sightings": [{"eventID": "tnr1", "playerNo": "8"}],
        }
        self.assertEqual(vic.payload_hard_conflicts(left, same_slot), [])
        self.assertIn(
            "concurrent-event",
            vic.payload_hard_conflicts(left, different_slot),
        )


if __name__ == "__main__":
    unittest.main()
