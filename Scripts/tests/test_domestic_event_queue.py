from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock


SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import build_domestic_event_queue as queue  # noqa: E402


class PublicDetailGapQueueTests(unittest.TestCase):
    def test_missing_public_detail_is_scheduled_but_published_detail_is_not(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            details = root / "details"
            details.mkdir()
            # Even a previously captured roster must be refreshed while the
            # public results-completeness gate still withholds its detail.
            (details / "tnr10001.json").write_text(json.dumps({
                "players": [{"playerNo": "1"}],
                "standings": [{"playerNo": "1"}],
                "rounds": [],
                "sourceSnapshots": [{"sha256": "abc"}],
                "fetchedAt": "2026-07-26T00:00:00+00:00",
            }), encoding="utf-8")
            public_events = root / "public-events.json"
            public_events.write_text(json.dumps({"events": [
                {
                    "tournamentID": "10001",
                    "displayName": "2026年亚洲青少年国际象棋锦标赛",
                    "series": "asian-youth",
                    "detailPath": None,
                },
                {
                    "tournamentID": "10002",
                    "displayName": "2026年全国国际象棋棋协大师赛",
                    "series": "chess-association-master",
                    "detailPath": "data/index/event-details/tnr10002.json",
                },
            ]}), encoding="utf-8")

            missing_csv = root / "missing.csv"
            with (
                mock.patch.object(queue, "STARTING_RANK", missing_csv),
                mock.patch.object(queue, "MASTER_GROUPS", missing_csv),
                mock.patch.object(queue, "SOURCE_CATALOG", missing_csv),
                mock.patch.object(queue, "DEMAND_GAPS", missing_csv),
                mock.patch.object(queue, "EVENT_DETAILS", details),
                mock.patch.object(queue, "PUBLIC_EVENTS", public_events),
            ):
                event_queue, _ = queue.build()

        self.assertEqual([row["tournamentID"] for row in event_queue["targets"]], ["10001"])
        target = event_queue["targets"][0]
        self.assertEqual(target["nextAction"], "capture-event")
        self.assertTrue(target["publicDetailMissing"])
        self.assertEqual(event_queue["totals"]["publicDetailMissing"], 1)


if __name__ == "__main__":
    unittest.main()
