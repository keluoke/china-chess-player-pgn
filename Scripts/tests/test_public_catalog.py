"""Contract tests for the curated public event catalog and round gating."""

from __future__ import annotations

import pathlib
import sys
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import build_event_catalog as bec  # noqa: E402
import build_event_details as bed  # noqa: E402


class SeriesClassificationTests(unittest.TestCase):
    def test_canonical_prefixes(self) -> None:
        self.assertEqual(
            bec.classify_series({"canonicalEventID": "chess-association-master-2025-599e187306"}),
            "chess-association-master",
        )
        self.assertEqual(bec.classify_series({"canonicalEventID": "lichengzhi-cup-2025"}), "lichengzhi-cup")

    def test_name_based_youth_series(self) -> None:
        self.assertEqual(bec.classify_series({"name": "World Youth Chess Championship 2026 - Open 18"}), "world-youth")
        self.assertEqual(bec.classify_series({"name": "World Cadets Chess Championship U8"}), "world-youth")
        self.assertEqual(bec.classify_series({"name": "28th Asian Youth Blitz Chess Championship (U14)"}), "asian-youth")

    def test_full_domestic_master_titles_are_classified_without_manual_mapping(self) -> None:
        for name in (
            "2025 National Amateur Chess Master Tournament Yancheng Station",
            "2026 National CCA Master Tournament - Open (Yancheng Station)",
        ):
            self.assertEqual(bec.classify_series({"name": name}), "chess-association-master")

    def test_everything_else_stays_out(self) -> None:
        for name in ("Shenzhen Open 2025", "China Chess League Division A", "Random Weekend Rapid"):
            self.assertIsNone(bec.classify_series({"name": name}), name)


class StructuredFieldTests(unittest.TestCase):
    def test_master_display_name_includes_station_and_group(self) -> None:
        event = {
            "id": "chess-results:1213322", "tournamentID": "1213322",
            "canonicalEventID": "chess-association-master-2025-599e187306",
            "date": "2025-11-01", "chineseName": None, "name": "x", "level": "event",
        }
        master = {"year": "2025", "station": "杭州站", "group_code": "MEN_LEVEL_1", "sex": "M", "level": "LEVEL_1"}
        row = bec.public_event(event, "chess-association-master", master)
        self.assertEqual(row["displayName"], "2025年全国国际象棋棋协大师赛（杭州站）男子一级棋士组")
        self.assertEqual(row["groupLabel"], "男子一级棋士组")
        self.assertEqual(row["series"], "chess-association-master")
        self.assertEqual(row["year"], "2025")
        self.assertEqual(row["station"], "杭州站")

    def test_lichengzhi_group_parse(self) -> None:
        label, sex, age = bec.parse_chinese_group("2025年全国国际象棋青少年锦标赛（个人）暨第30届李成智杯 U12男子组")
        self.assertEqual((label, sex, age), ("U12男子组", "M", "U12"))
        label, sex, age = bec.parse_chinese_group("…李成智杯 女子甲组")
        self.assertEqual(label, "女子甲组")
        self.assertEqual(sex, "F")

    def test_group_token_parse(self) -> None:
        self.assertEqual(bec.parse_group_token("Asian Youth Blitz (U14)"), ("男子U14组", "M", "U14"))
        self.assertEqual(bec.parse_group_token("Asian Youth Rapid G10"), ("女子U10组", "F", "U10"))
        self.assertEqual(bec.parse_group_token("no group here"), (None, None, None))

    def test_missing_structured_fields_are_rejected_not_downgraded(self) -> None:
        self.assertIsNone(bec.public_event({"id": "x", "tournamentID": ""}, "world-youth", {}))
        self.assertIsNone(bec.public_event({"id": "x", "tournamentID": "1", "date": None}, "world-youth", {}))


class PublicFilterTests(unittest.TestCase):
    def test_test_demo_and_future_and_unmapped_are_excluded(self) -> None:
        today = "2026-07-15"
        self.assertEqual(bec.excluded_from_public({"name": "Parser Test Event", "date": "2026-01-01"}, today), "test-or-demo-name")
        self.assertEqual(bec.excluded_from_public({"name": "测试赛", "date": "2026-01-01"}, today), "test-or-demo-name")
        self.assertEqual(bec.excluded_from_public({"name": "Real Open", "date": "2027-04-21"}, today), "implausible-future-date")
        self.assertEqual(bec.excluded_from_public({"name": "Real Open", "date": ""}, today), "undated-and-unmapped")
        # Near-future events in season are fine; mapped undated events are fine.
        self.assertIsNone(bec.excluded_from_public({"name": "Real Open", "date": "2026-07-24"}, today))
        self.assertIsNone(bec.excluded_from_public({"name": "Real", "date": "", "chineseName": "某某赛"}, today))


class TruncatedNameRepairTests(unittest.TestCase):
    def test_pgn_header_extends_truncated_upstream_name(self) -> None:
        best, aliases = bec.best_source_name(
            "2025 China Youth Rapid Champio",
            {"2025 China Youth Rapid Championship(U14)"},
        )
        self.assertEqual(best, "2025 China Youth Rapid Championship(U14)")
        self.assertEqual(aliases, ["2025 China Youth Rapid Champio"])

    def test_unrelated_pgn_name_stays_alias_only(self) -> None:
        best, aliases = bec.best_source_name("Official Title", {"Completely Different"})
        self.assertEqual(best, "Official Title")
        self.assertEqual(aliases, ["Completely Different"])

    def test_event_detail_manifest_repairs_a_truncated_master_title(self) -> None:
        row = bec.build_upstream_event(
            {
                "source": "Chess-Results",
                "tournamentID": "1437536",
                "name": "2026 National CCA Master Tourn",
                "date": "2026-06-21",
                "players": [],
            },
            {},
            {},
            {
                "path": "data/index/event-details/tnr1437536.json",
                "displayName": "2026 National CCA Master Tournament - Open (Yancheng Station)",
                "playableComplete": True,
            },
            {},
        )
        self.assertEqual(row["name"], "2026 National CCA Master Tournament - Open (Yancheng Station)")
        self.assertIn("2026 National CCA Master Tourn", row["aliases"])
        self.assertEqual(bec.classify_series(row), "chess-association-master")
        self.assertTrue(row["playableComplete"])


class RoundGateTests(unittest.TestCase):
    def test_result_column_shift_is_detected(self) -> None:
        rounds = [{"round": 1, "pairings": [
            {"white": {"name": "甲"}, "black": {"name": "0"}, "result": "CHN"},
        ]}]
        self.assertGreater(bed.round_anomalies(rounds), 0)

    def test_legitimate_results_pass(self) -> None:
        rounds = [{"round": 1, "pairings": [
            {"white": {"name": "甲"}, "black": {"name": "乙"}, "result": "1 - 0"},
            {"white": {"name": "丙"}, "black": {"name": "丁"}, "result": "½ - ½"},
            {"white": {"name": "戊"}, "black": {"name": "己"}, "result": "+ - -"},
            {"white": {"name": "庚"}, "black": {"name": "辛"}, "result": "1 - 0 K"},
        ]}]
        self.assertEqual(bed.round_anomalies(rounds), 0)


if __name__ == "__main__":
    unittest.main()
