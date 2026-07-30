"""Contract tests for the curated public event catalog and round gating."""

from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import build_event_catalog as bec  # noqa: E402
import build_event_details as bed  # noqa: E402
import build_player_participation as bpp  # noqa: E402
import build_static_player_pgn as bsp  # noqa: E402
import validate_master_group_labels as vmgl  # noqa: E402


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

    def test_master_title_supplies_explicit_station_and_open_group(self) -> None:
        event = {
            "id": "chess-results:1437536", "tournamentID": "1437536",
            "date": "2026-06-21", "chineseName": None,
            "name": "2026 National CCA Master Tournament - Open (Yancheng Station)",
            "level": "event",
        }
        row = bec.public_event(event, "chess-association-master", {})
        self.assertEqual(row["station"], "盐城站")
        self.assertEqual(row["groupLabel"], "棋协大师组")
        self.assertEqual(row["level"], "OPEN")
        self.assertEqual(row["displayName"], "2026年全国国际象棋棋协大师赛（盐城站）棋协大师组")

    def test_master_title_does_not_invent_an_unstated_group(self) -> None:
        event = {
            "id": "chess-results:1313397", "tournamentID": "1313397",
            "date": "2025-12-01", "chineseName": None,
            "name": "2025 National Amateur Chess Master Tournament Hefei Station",
            "level": "event",
        }
        row = bec.public_event(event, "chess-association-master", {})
        self.assertEqual(row["station"], "合肥站")
        self.assertIsNone(row["groupLabel"])
        self.assertIsNone(row["level"])
        self.assertEqual(row["displayName"], "2025年全国国际象棋棋协大师赛（合肥站）")

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

    def test_published_detail_without_crawler_row_gets_a_catalog_record(self) -> None:
        event = bec.build_detail_only_event(
            "1437533",
            {
                "path": "data/index/event-details/tnr1437533.json",
                "displayName": "2026年全国国际象棋棋协大师赛（盐城站）男子一级棋士B组",
                "roundCount": 9,
                "standingCount": 146,
            },
            {},
            {},
        )
        self.assertEqual(bec.classify_series(event), "chess-association-master")
        row = bec.public_event(event, "chess-association-master", {})
        self.assertEqual(row["tournamentID"], "1437533")
        self.assertEqual(row["detailStatus"], "published")
        self.assertEqual(row["year"], "2026")

    def test_event_level_lichess_archive_has_neutral_id_and_pending_translation(self) -> None:
        event = bec.build_pgn_only_event(
            "lichess broadcasts",
            "name:Untranslated Invitational|2026-07-01",
            {
                "names": {"Untranslated Invitational"},
                "dates": {"2026-07-01"},
                "players": {"8600001"},
                "pgnCount": 1,
                "gameCount": 7,
                "canonicalEventIDs": set(),
            },
        )
        self.assertRegex(event["id"], r"^ev-[0-9a-f]{16}$")
        self.assertEqual(bec.classify_series(event), "archive")
        row = bec.public_event(event, "archive", {})
        self.assertTrue(row["nameTranslationPending"])
        self.assertEqual(row["license"], "CC BY-SA 4.0")

    def test_english_source_title_gets_a_chinese_public_title(self) -> None:
        event = {
            "id": "chess-results:1451321",
            "tournamentID": "1451321",
            "date": "2026-07-24",
            "name": "28th Asian Youth Blitz Chess Championships 2026 - G16",
        }
        row = bec.public_event(event, "asian-youth", {})
        self.assertEqual(row["displayName"], "2026年第28届亚洲青少年国际象棋锦标赛（超快棋·女子U16组）")
        self.assertRegex(row["displayName"], r"[\u3400-\u9fff]")

    def test_duplicate_tnr_rows_coalesce_and_keep_detail_and_pgn_counts(self) -> None:
        events = [
            {
                "id": "chess-results:42", "source": "Chess-Results", "tournamentID": "42",
                "date": "2026-07-24", "name": "Asian Youth U12", "displayName": "Asian Youth U12",
                "players": ["1"], "playerCount": 1, "gameCount": 0, "level": "event",
                "detailPath": "data/index/event-details/tnr42.json",
            },
            {
                "id": "static-pgn:42", "source": "Static PGN", "tournamentID": "42",
                "date": "2026-07-17", "name": "Asian Youth U12", "displayName": "Asian Youth U12",
                "players": ["1", "2"], "playerCount": 2, "gameCount": 9, "level": "event",
            },
        ]
        rows, excluded = bec.public_catalog(events, {}, today="2026-07-27")
        self.assertEqual(excluded, [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "chess-results:42")
        self.assertEqual(rows[0]["detailPath"], "data/index/event-details/tnr42.json")
        self.assertEqual(rows[0]["gameCount"], 9)
        self.assertEqual(rows[0]["playerCount"], 2)


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

    def test_unresolved_master_station_is_isolated(self) -> None:
        events = [{
            "id": "chess-results:1", "tournamentID": "1", "date": "2026-07-01",
            "name": "2026 National CCA Master Tournament - Open (Unknown Station)",
            "level": "event",
        }]
        rows, excluded = bec.public_catalog(events, {}, today="2026-07-30")
        self.assertEqual(rows, [])
        self.assertEqual(excluded[0]["reason"], "master-station-missing")


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


class PlayerProjectionTests(unittest.TestCase):
    def test_participation_only_projects_curated_chinese_non_test_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            player_events = root / "player-events.csv"
            player_events.write_text(
                "fide_id,tnrid,tournament_name,end_date,rank,rounds,participants\n"
                "8600001,1,Test Match,2026-01-01,1,9,10\n"
                "8600001,2,English Open,2026-01-02,2,9,20\n"
                "8600001,3,Raw English Title,2026-01-03,3,9,30\n",
                encoding="utf-8",
            )
            catalog = root / "public-events.json"
            catalog.write_text(
                '{"events":['
                '{"id":"chess-results:1","tournamentID":"1","displayName":"测试赛"},'
                '{"id":"chess-results:2","tournamentID":"2","displayName":"2026年亚洲青少年国际象棋锦标赛"}'
                "]}\n",
                encoding="utf-8",
            )
            rows = bpp.build_rows(player_events, catalog, root / "missing-player-details")
        self.assertEqual([row["tournamentID"] for row in rows["8600001"]], ["2"])
        self.assertEqual(rows["8600001"][0]["name"], "2026年亚洲青少年国际象棋锦标赛")

    def test_round_broadcast_items_collapse_to_one_event_and_tests_are_removed(self) -> None:
        games = [
            bsp.PlayerGame(
                pgn="", event=f"Round {round_number}: A - B",
                broadcast_name="2025年世界青少年国际象棋锦标赛（女子U12组）",
                date=f"2025-09-{day:02d}", white="A", black="B", result="1-0",
                source="Lichess Broadcasts",
            )
            for round_number, day in ((6, 23), (7, 24))
        ]
        games.append(bsp.PlayerGame(
            pgn="", event="Parser Test Round 1", broadcast_name="Parser Test Event",
            date="2025-09-25", white="A", black="B", result="1-0",
            source="Lichess Broadcasts",
        ))
        games.append(bsp.PlayerGame(
            pgn="", event="Round 12 & Semifinals: A - B",
            date="2025-09-26", white="A", black="B", result="1-0",
            source="Lichess Broadcasts",
        ))
        games.append(bsp.PlayerGame(
            pgn="", event="Untranslated English Open",
            date="2025-09-27", white="A", black="B", result="1-0",
            source="Lichess Broadcasts",
        ))
        rows = bsp.event_summaries(games)
        self.assertEqual(len(rows), 2)
        translated = next(row for row in rows if "世界青少年" in row["name"])
        untranslated = next(row for row in rows if row["name"] == "Untranslated English Open")
        self.assertEqual(translated["gameCount"], 2)
        self.assertNotRegex(translated["name"], bec.ROUND_ITEM_RE)
        self.assertRegex(untranslated["id"], r"^ev-[0-9a-f]{16}$")
        self.assertTrue(untranslated["nameTranslationPending"])


class MasterGroupValidationTests(unittest.TestCase):
    def test_mismatch_fails_and_isolated_target_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            groups = root / "groups.csv"
            details = root / "details"
            details.mkdir()
            groups.write_text(
                "tournament_id,group_code,evidence_status\n"
                "1,OPEN,source-list-needs-page-verify\n"
                "2,WOMEN_LEVEL_1,source-target-mismatch\n",
                encoding="utf-8",
            )
            (details / "tnr1.json").write_text(
                '{"sourceName":"示例赛事 女子一级棋士组"}', encoding="utf-8"
            )
            (details / "tnr2.json").write_text(
                '{"sourceName":"Unrelated Open"}', encoding="utf-8"
            )
            result = vmgl.validate(groups, details)
        self.assertFalse(result["ok"])
        self.assertEqual(len(result["failures"]), 1)
        self.assertEqual(result["isolated"], 1)


if __name__ == "__main__":
    unittest.main()
