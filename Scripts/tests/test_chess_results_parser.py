"""Contract tests for the Chess-Results event collector.

All fixtures are synthetic minimal HTML with fictional players; no source
material is ever committed. These tests pin the per-format parser matrix,
page-level checkpointing, single-target batch isolation and the offline
replay guarantee (parser updates never re-request pages).
"""

from __future__ import annotations

import gzip
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import sync_chess_results_event as sce  # noqa: E402
from source_http import SourceHTTPError  # noqa: E402

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "chess_results"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def parse(name: str, url: str = "https://chess-results.com/tnr999001.aspx?lan=1") -> sce.TableParser:
    return sce.parse_html(fixture(name), url)


class TnrNormalizationTests(unittest.TestCase):
    def test_accepted_forms(self) -> None:
        for value, expected in [
            ("1110333", "1110333"),
            ("tnr1213323", "1213323"),
            ("TNR1213323", "1213323"),
            ("tnr1383", "1383"),  # legacy World Youth TNR
            ("https://chess-results.com/tnr1156008.aspx?lan=1", "1156008"),
            ("https://s1.chess-results.com/tnr1110333.aspx?lan=1&zeilen=99999", "1110333"),
        ]:
            self.assertEqual(sce.tournament_id(value), expected, value)

    def test_rejected_forms(self) -> None:
        for value in [
            "https://example.com/tnr1156008.aspx",  # wrong host
            "123",  # too short
            "",
            "not a target",
        ]:
            self.assertEqual(sce.tournament_id(value), "", value)

    def test_batch_deduplicates(self) -> None:
        ids = [sce.tournament_id(v) for v in ["1110333", "tnr1110333", "999001"]]
        self.assertEqual(list(dict.fromkeys(v for v in ids if v)), ["1110333", "999001"])


class ParserMatrixTests(unittest.TestCase):
    def test_individual_starting_rank(self) -> None:
        players = sce.parse_players(parse("starting_rank_individual.html"))
        self.assertEqual(len(players), 4)
        self.assertEqual(players["1"]["fideID"], "900000001")
        self.assertEqual(players["1"]["chineseName"], "测试甲")

    def test_team_player_list_without_fideid(self) -> None:
        players = sce.parse_players(parse("team_player_list.html"))
        self.assertEqual(len(players), 3)
        self.assertEqual(players["1"]["name"], "Epsilon, Test")
        self.assertEqual(players["1"]["fideID"], "")

    def test_domestic_starting_rank_without_rating_or_fideid(self) -> None:
        players = sce.parse_players(parse("starting_rank_domestic_minimal.html"))
        self.assertEqual(list(players), ["1", "2"])
        self.assertEqual(players["1"]["name"], "测试甲")
        self.assertEqual(players["1"]["club"], "示例棋院")
        self.assertEqual(players["1"]["fideID"], "")

    def test_standings(self) -> None:
        players = sce.parse_players(parse("starting_rank_individual.html"))
        standings = sce.parse_standings(parse("standings_individual.html"), players)
        self.assertEqual(len(standings), 4)
        self.assertEqual(standings[0]["name"], "Alpha, Tester")
        self.assertEqual(standings[0]["score"], "2")
        self.assertEqual(standings[0]["tieBreaks"], ["3", "2.5"])

    def test_team_standings_use_team_serial_numbers(self) -> None:
        page = sce.parse_html("""
        <table><tr><th>Rk.</th><th>SNo</th><th>Team</th><th>Games</th><th>TB1</th><th>TB2</th></tr>
        <tr><td>1</td><td>7</td><td>Example Team</td><td>9</td><td>16</td><td>12.5</td></tr></table>
        """, "https://chess-results.com/tnr999001.aspx?art=0")
        standings = sce.parse_team_standings(page)
        self.assertEqual(standings, [{
            "rank": "1", "playerNo": "7", "name": "Example Team", "chineseName": "",
            "fideID": "", "federation": "", "rating": "", "club": "Example Team",
            "score": "16", "tieBreaks": ["16", "12.5"],
        }])

    def test_team_crosstable_uses_rank_when_no_team_serial_is_published(self) -> None:
        page = sce.parse_html("""
        <table><tr><th>Rk.</th><th>Team</th><th>1</th><th>TB1</th></tr>
        <tr><td>2</td><td>Example Team</td><td>*</td><td>14</td></tr></table>
        """, "https://chess-results.com/tnr999001.aspx?art=0")
        standings = sce.parse_team_standings(page)
        self.assertEqual(standings[0]["playerNo"], "2")
        self.assertEqual(standings[0]["score"], "14")

    def test_team_crosstable_discovers_round_robin_rounds(self) -> None:
        page = sce.parse_html("""
        <table><tr><th>Rk.</th><th>Team</th><th>1</th><th>2</th><th>3</th><th>TB1</th></tr>
        <tr><td>1</td><td>Alpha</td><td>*</td><td>2</td><td>2</td><td>4</td></tr></table>
        """, "https://chess-results.com/tnr999001.aspx?art=0")
        self.assertEqual(sce.team_crosstable_rounds(page), 2)

    def test_team_pairings(self) -> None:
        page = sce.parse_html("""
        <table><tr><th>Bo.</th><th>No.</th><th>Team</th><th>Result</th><th>Team</th><th>No.</th></tr>
        <tr><td>1</td><td>7</td><td>Example Team</td><td>2.5 - 1.5</td><td>Sample Team</td><td>3</td></tr></table>
        """, "https://chess-results.com/tnr999001.aspx?art=2&rd=1")
        pairings = sce.parse_team_pairings(page)
        self.assertEqual(len(pairings), 1)
        self.assertEqual(pairings[0]["white"], {"name": "Example Team", "playerNo": "7"})
        self.assertEqual(pairings[0]["black"], {"name": "Sample Team", "playerNo": "3"})
        self.assertEqual(pairings[0]["result"], "2.5 - 1.5")

    def test_team_pairings_with_caption_and_split_score(self) -> None:
        page = sce.parse_html("""
        <table><tr><td>Round 1 on 2025/01/01</td></tr>
        <tr><th>No.</th><th>Team</th><th>Team</th><th>Res.</th><th>:</th><th>Res.</th></tr>
        <tr><td>1</td><td>Example Team</td><td>Sample Team</td><td>2.5</td><td>:</td><td>1.5</td></tr></table>
        """, "https://chess-results.com/tnr999001.aspx?art=2&rd=1")
        pairings = sce.parse_team_pairings(page, {"example team": "7", "sample team": "3"})
        self.assertEqual(pairings[0]["white"]["playerNo"], "7")
        self.assertEqual(pairings[0]["black"]["playerNo"], "3")
        self.assertEqual(pairings[0]["result"], "2.5 - 1.5")
        self.assertFalse(pairings[0]["hasPGN"])

    def test_pairings_duplicate_headers_semantic_mapping(self) -> None:
        players = sce.parse_players(parse("starting_rank_individual.html"))
        pairings = sce.parse_pairings(parse("pairings_round.html"), players)
        self.assertEqual(len(pairings), 2)
        self.assertEqual(pairings[0]["white"]["playerNo"], "1")
        self.assertEqual(pairings[0]["black"]["playerNo"], "4")
        self.assertEqual(pairings[0]["black"]["chineseName"], "测试丁")
        self.assertEqual(pairings[0]["result"], "1 - 0")
        self.assertTrue(pairings[0]["hasPGN"])
        self.assertEqual(pairings[1]["result"], "½ - ½")
        self.assertFalse(pairings[1]["hasPGN"])

    def test_pairings_shifted_layout_not_fixed_columns(self) -> None:
        players = sce.parse_players(parse("starting_rank_individual.html"))
        pairings = sce.parse_pairings(parse("pairings_shifted.html"), players)
        self.assertEqual(len(pairings), 1)
        self.assertEqual(pairings[0]["white"]["name"], "Alpha, Tester")
        self.assertEqual(pairings[0]["black"]["name"], "Beta, Sample")
        self.assertEqual(pairings[0]["black"]["playerNo"], "2")
        self.assertEqual(pairings[0]["result"], "0 - 1")

    def test_pairings_without_numbers_use_unique_roster_names(self) -> None:
        players = sce.parse_players(parse("starting_rank_individual.html"))
        pairings = sce.parse_pairings(parse("pairings_names_only.html"), players)
        self.assertEqual(len(pairings), 2)
        self.assertEqual(pairings[0]["white"]["playerNo"], "1")
        self.assertEqual(pairings[0]["black"]["playerNo"], "4")
        self.assertEqual(pairings[1]["white"]["playerNo"], "2")
        self.assertEqual(pairings[1]["black"]["playerNo"], "3")

    def test_roster_name_fallback_rejects_ambiguous_names(self) -> None:
        players = {
            "1": {"playerNo": "1", "name": "Same, Name"},
            "2": {"playerNo": "2", "name": "Same Name"},
        }
        self.assertEqual(sce.roster_number_for_name("Same Name", players), "")

    def test_legacy_page_with_all_rounds_in_one_table_selects_requested_round(self) -> None:
        body = """
        <table>
          <tr><td colspan="8">Round 1 on 2022/01/01</td></tr>
          <tr><th>Bo.</th><th>No.</th><th>Club/City</th><th>White</th><th>Result</th><th>Black</th><th>Club/City</th><th>No.</th></tr>
          <tr><td>1</td><td>1</td><td></td><td>A</td><td>1 - 0</td><td>B</td><td></td><td>2</td></tr>
          <tr><td colspan="8">Round 2 on 2022/01/02</td></tr>
          <tr><th>Bo.</th><th>No.</th><th>Club/City</th><th>White</th><th>Result</th><th>Black</th><th>Club/City</th><th>No.</th></tr>
          <tr><td>1</td><td>2</td><td></td><td>B</td><td>0 - 1</td><td>A</td><td></td><td>1</td></tr>
        </table>
        """
        page = sce.parse_html(body, "https://chess-results.com/tnr1.aspx")
        players = {"1": {"playerNo": "1", "name": "A"}, "2": {"playerNo": "2", "name": "B"}}
        self.assertEqual(sce.parse_pairings(page, players), [])
        second = sce.parse_pairings_for_round(page, players, 2)
        self.assertEqual(len(second), 1)
        self.assertEqual(second[0]["white"]["playerNo"], "2")
        self.assertEqual(second[0]["black"]["playerNo"], "1")

    def test_fixed_fallback_validates_board_numbers(self) -> None:
        table = sce.find_table(parse("pairings_round.html"), {"bo", "white", "black", "result"})
        players = sce.parse_players(parse("starting_rank_individual.html"))
        rows = sce._parse_pairings_fixed(table, players)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["black"]["playerNo"], "4")

    def test_team_round_is_classified_not_layout_regression(self) -> None:
        page = parse("team_round.html")
        players = sce.parse_players(parse("team_player_list.html"))
        self.assertEqual(sce.parse_pairings(page, players), [])
        self.assertTrue(sce.looks_like_team_page(page))

    def test_empty_event_page(self) -> None:
        page = parse("empty_event.html")
        self.assertEqual(sce.find_player_table(page), [])
        self.assertLess(sce.data_row_count(page), 2)

    def test_missing_result_column_yields_no_pairings(self) -> None:
        body = "<table><tr><th>Bo.</th><th>White</th><th>Black</th></tr><tr><td>1</td><td>A</td><td>B</td></tr></table>"
        page = sce.parse_html(body, "https://chess-results.com/x")
        self.assertEqual(sce.parse_pairings(page, {}), [])


class RoundDiscoveryTests(unittest.TestCase):
    def test_rank_after_round_heading(self) -> None:
        page = sce.parse_html("<h2>Rank after Round 9</h2>", "https://chess-results.com/tnr1.aspx")
        rounds, candidates = sce.discover_rounds([page], queue_rounds=0, max_rounds=0)
        self.assertEqual(rounds, 9)
        self.assertEqual(candidates["heading"], 9)

    def test_heading_and_links_agree(self) -> None:
        page = parse("standings_individual.html")
        rounds, candidates = sce.discover_rounds([page], queue_rounds=0, max_rounds=0)
        self.assertEqual(rounds, 2)
        self.assertEqual(candidates["heading"], 2)
        self.assertEqual(candidates["links"], 2)

    def test_queue_metadata_fallback(self) -> None:
        page = parse("standings_no_rounds.html")
        rounds, candidates = sce.discover_rounds([page], queue_rounds=9, max_rounds=0)
        self.assertEqual(rounds, 9)
        self.assertEqual(candidates["heading"], 0)
        self.assertEqual(candidates["links"], 0)

    def test_unknown_rounds_allows_partial(self) -> None:
        page = parse("standings_no_rounds.html")
        rounds, _ = sce.discover_rounds([page], queue_rounds=0, max_rounds=0)
        self.assertEqual(rounds, 0)

    def test_conflict_prefers_directly_observed_heading(self) -> None:
        page = parse("standings_individual.html")
        rounds, _ = sce.discover_rounds([page], queue_rounds=9, max_rounds=0)
        self.assertEqual(rounds, 2)

    def test_max_rounds_caps(self) -> None:
        page = parse("standings_individual.html")
        rounds, _ = sce.discover_rounds([page], queue_rounds=0, max_rounds=1)
        self.assertEqual(rounds, 1)


class SchedulingStateTests(unittest.TestCase):
    def test_v1_entries_migrate_to_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state = pathlib.Path(temp) / "capture-state.json"
            state.write_text(json.dumps({"events": {"123456": {"capturedAt": "2999-01-01T00:00:00+00:00"}}}))
            with mock.patch.object(sce, "CAPTURE_STATE", state):
                events = sce.load_capture_state()["events"]
            self.assertEqual(events["123456"]["status"], "complete")

    def test_skip_rules(self) -> None:
        future = "2999-01-01T00:00:00+00:00"
        past = "2000-01-01T00:00:00+00:00"
        self.assertEqual(sce.should_skip_target({"status": "complete", "capturedAt": future}, 30), "recently-captured")
        self.assertEqual(sce.should_skip_target({"status": "complete", "capturedAt": past}, 30), "")
        self.assertEqual(
            sce.should_skip_target(
                {"status": "quarantined", "nextRetryAt": future, "parserVersion": sce.PARSER_VERSION}, 30
            ),
            "quarantined",
        )
        # A parser update releases quarantined/unsupported targets.
        self.assertEqual(
            sce.should_skip_target({"status": "quarantined", "nextRetryAt": future, "parserVersion": "old"}, 30), ""
        )
        self.assertEqual(
            sce.should_skip_target({"status": "unsupported", "parserVersion": sce.PARSER_VERSION}, 30), "needs-parser"
        )
        self.assertEqual(sce.should_skip_target({"status": "unsupported", "parserVersion": "old"}, 30), "")
        self.assertEqual(sce.should_skip_target({"status": "partial"}, 30), "")

    def test_two_structural_failures_quarantine(self) -> None:
        events: dict = {}
        for _ in range(2):
            sce.record_target_result(
                events, "42", status="failed", error_code="PARSER_LAYOUT_CHANGED",
                failed_page="round-1", structural=True,
            )
        entry = events["42"]
        self.assertEqual(entry["status"], "quarantined")
        self.assertIsNotNone(entry["nextRetryAt"])
        self.assertEqual(entry["structureFailures"], 2)
        self.assertEqual(entry["attempts"], 2)

    def test_network_failure_sets_backoff_not_quarantine(self) -> None:
        events: dict = {}
        sce.record_target_result(events, "42", status="failed", error_code="SOURCE_NETWORK_FAILURE", structural=False)
        entry = events["42"]
        self.assertEqual(entry["status"], "retry-wait")
        self.assertIsNotNone(entry["nextRetryAt"])
        self.assertEqual(int(entry.get("structureFailures") or 0), 0)


class FakeFetcher:
    """Deterministic page server with per-page call accounting."""

    def __init__(self):
        self.calls: dict[tuple[str, int, int | None], int] = {}
        self.failures: dict[tuple[str, int, int | None], Exception] = {}

    def __call__(self, tid: str, art: int, rd: int | None, timeout: float, retries: int):
        key = (tid, art, rd)
        self.calls[key] = self.calls.get(key, 0) + 1
        if key in self.failures:
            raise self.failures[key]
        url = sce.page_url(tid, art, rd)
        if art == 0:
            if tid == "999404":
                return fixture("empty_event.html"), url
            return fixture("starting_rank_individual.html"), url
        if art in {15, 16}:
            return fixture("empty_event.html"), url
        if art == 1:
            return fixture("standings_individual.html"), url
        if art == 2:
            if tid == "999006":
                return fixture("pairings_out_of_roster.html"), url
            if tid == "999007":
                return fixture("pairings_missing_refs.html"), url
            return fixture("pairings_round.html"), url
        raise AssertionError(f"unexpected page request {key}")


class BatchIsolationTests(unittest.TestCase):
    def run_main(self, temp: pathlib.Path, fetcher: FakeFetcher, targets: list[str], run_name: str) -> int:
        argv = [
            "sync_chess_results_event.py", *targets,
            "--private-root", str(temp / run_name), "--delay", "0",
        ]
        state = temp / "capture-state.json"
        queue = temp / "queue.json"
        if not queue.exists():
            queue.write_text(json.dumps({"targets": []}))
        with (
            mock.patch.object(sce, "CAPTURE_STATE", state),
            mock.patch.object(sce, "EVENT_QUEUE", queue),
            mock.patch.object(sce, "fetch_page_body", fetcher),
            mock.patch.object(sys, "argv", argv),
        ):
            return sce.main()

    def load_state(self, temp: pathlib.Path) -> dict:
        return json.loads((temp / "capture-state.json").read_text())["events"]

    def test_poison_target_does_not_block_batch(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cc-test-") as name:
            temp = pathlib.Path(name)
            fetcher = FakeFetcher()
            code = self.run_main(temp, fetcher, ["999001", "999404", "999002"], "run1")
            self.assertEqual(code, 4, "batch with an isolated failure returns partial-success")
            events = self.load_state(temp)
            self.assertEqual(events["999001"]["status"], "complete")
            self.assertEqual(events["999002"]["status"], "complete")
            self.assertEqual(events["999404"]["status"], "quarantined")
            self.assertEqual(events["999404"]["errorCode"], "EVENT_EMPTY")
            self.assertEqual(events["999404"]["failedPage"], "starting-rank")
            # Structure errors never trigger network retries of the same page.
            self.assertEqual(fetcher.calls[("999404", 0, None)], 1)
            # Structured per-target batch outcome for the panel: a mixed batch
            # is "2 complete / 1 quarantined", not one aggregated failure.
            result = json.loads((temp / "run1" / "result.json").read_text())
            self.assertEqual(result["targets"]["999001"]["status"], "complete")
            self.assertEqual(result["targets"]["999001"]["players"], 4)
            self.assertEqual(result["targets"]["999404"]["status"], "quarantined")
            self.assertEqual(result["targets"]["999404"]["errorCode"], "EVENT_EMPTY")
            self.assertEqual(result["summary"], {"complete": 2, "quarantined": 1})
            self.assertEqual(result["requested"], ["999001", "999404", "999002"])

    def test_pages_persist_before_parse_and_resume_fetches_only_missing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cc-test-") as name:
            temp = pathlib.Path(name)
            fetcher = FakeFetcher()
            fetcher.failures[("999003", 2, 2)] = SourceHTTPError("SOURCE_NETWORK_FAILURE", "cut")
            code = self.run_main(temp, fetcher, ["999003"], "run1")
            self.assertEqual(code, 1)
            raw = temp / "run1" / "raw" / "chess-results" / "tnr999003"
            for kind in ("starting-rank", "standings", "round-1"):
                self.assertTrue((raw / f"{kind}.html.gz").is_file(), kind)
            events = self.load_state(temp)
            self.assertEqual(events["999003"]["status"], "retry-wait")
            self.assertEqual(events["999003"]["errorCode"], "SOURCE_NETWORK_FAILURE")

            # Resume: the failed page recovers; previously captured pages are
            # reused from the prior run's raw store, not re-requested.
            del fetcher.failures[("999003", 2, 2)]
            code = self.run_main(temp, fetcher, ["999003"], "run2")
            self.assertEqual(code, 0)
            events = self.load_state(temp)
            self.assertEqual(events["999003"]["status"], "complete")
            self.assertEqual(fetcher.calls[("999003", 0, None)], 1)
            self.assertEqual(fetcher.calls[("999003", 1, None)], 1)
            self.assertEqual(fetcher.calls[("999003", 2, 1)], 1)
            self.assertEqual(fetcher.calls[("999003", 2, 2)], 2)

    def test_offline_replay_never_touches_network(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cc-test-") as name:
            temp = pathlib.Path(name)
            fetcher = FakeFetcher()
            self.assertEqual(self.run_main(temp, fetcher, ["999001"], "run1"), 0)
            total_calls = sum(fetcher.calls.values())

            def explode(*_args, **_kwargs):
                raise AssertionError("offline replay must not fetch")

            argv = [
                "sync_chess_results_event.py", "999001", "--replay", "--overwrite",
                "--private-root", str(temp / "run2"), "--delay", "0",
            ]
            with (
                mock.patch.object(sce, "CAPTURE_STATE", temp / "capture-state.json"),
                mock.patch.object(sce, "EVENT_QUEUE", temp / "queue.json"),
                mock.patch.object(sce, "fetch_page_body", explode),
                mock.patch.object(sys, "argv", argv),
            ):
                self.assertEqual(sce.main(), 0)
            self.assertEqual(sum(fetcher.calls.values()), total_calls)
            events = self.load_state(temp)
            self.assertEqual(events["999001"]["status"], "complete")

    def test_budget_exhaustion_stops_batch_without_erasing_success(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cc-test-") as name:
            temp = pathlib.Path(name)
            fetcher = FakeFetcher()
            fetcher.failures[("999005", 0, None)] = SourceHTTPError("VISIT_BUDGET_EXHAUSTED", "预算用完")
            code = self.run_main(temp, fetcher, ["999001", "999005", "999002"], "run1")
            self.assertEqual(code, 4)
            events = self.load_state(temp)
            self.assertEqual(events["999001"]["status"], "complete")
            self.assertEqual(events["999005"]["status"], "retry-wait")
            # The batch stopped at the global budget: the third target was
            # never attempted and remains unrecorded.
            self.assertNotIn("999002", events)


class PaginationAndRosterTests(unittest.TestCase):
    def test_every_page_request_disables_pagination(self) -> None:
        for art, rd in [(0, None), (1, None), (2, 3), (15, None)]:
            self.assertIn("zeilen=99999", sce.page_url("1213322", art, rd))

    def test_pairing_refs_outside_roster_block_the_capture(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cc-test-") as name:
            temp = pathlib.Path(name)
            fetcher = FakeFetcher()
            runner = BatchIsolationTests()
            code = runner.run_main(temp, fetcher, ["999006"], "run1")
            self.assertEqual(code, 1)
            events = runner.load_state(temp)
            self.assertEqual(events["999006"]["errorCode"], "PAIRING_REFS_OUTSIDE_ROSTER")
            self.assertEqual(events["999006"]["failedPage"], "round-1")
            # Structural: no pointless network retry of the same round page.
            self.assertEqual(fetcher.calls[("999006", 2, 1)], 1)

    def test_missing_pairing_refs_block_the_capture_but_explicit_bye_does_not(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cc-test-") as name:
            temp = pathlib.Path(name)
            fetcher = FakeFetcher()
            runner = BatchIsolationTests()
            code = runner.run_main(temp, fetcher, ["999007"], "run1")
            self.assertEqual(code, 1)
            events = runner.load_state(temp)
            self.assertEqual(events["999007"]["errorCode"], "PAIRING_REFS_MISSING")
            self.assertEqual(events["999007"]["failedPage"], "round-1")
            self.assertEqual(fetcher.calls[("999007", 2, 1)], 1)

    def test_truncated_cache_from_old_request_params_is_not_reused(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = pathlib.Path(name) / "raw"
            store = sce.PageStore(root, [])
            old_url = "https://chess-results.com/tnr1.aspx?lan=1&art=1"  # no zeilen
            store.save("1", "standings", old_url, "<html/>", request_url=old_url)
            current = sce.page_url("1", 1, None)
            self.assertIsNone(store.load("1", "standings", current))
            # Offline replay (no expected URL) still sees the evidence.
            self.assertIsNotNone(store.load("1", "standings"))
            store.save("1", "standings", current, "<html/>", request_url=current)
            self.assertIsNotNone(store.load("1", "standings", current))


class PageStoreTests(unittest.TestCase):
    def test_round_trip_and_unicode_resilience(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = pathlib.Path(name) / "raw"
            store = sce.PageStore(root, [])
            body = "<html>测试�</html>"
            entry = store.save("1", "standings", "https://chess-results.com/tnr1.aspx", body)
            loaded = store.load("1", "standings")
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded[0], body)
            self.assertEqual(loaded[1], "https://chess-results.com/tnr1.aspx")
            raw = gzip.decompress((root / "tnr1" / "standings.html.gz").read_bytes())
            self.assertEqual(len(raw), entry["bytes"])

    def test_cache_copies_forward_from_previous_run(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            old_root = pathlib.Path(name) / "old"
            new_root = pathlib.Path(name) / "new"
            sce.PageStore(old_root, []).save("1", "standings", "https://chess-results.com/tnr1.aspx", "<html/>")
            store = sce.PageStore(new_root, [old_root])
            self.assertIsNotNone(store.load("1", "standings"))
            self.assertTrue((new_root / "tnr1" / "standings.html.gz").is_file())

    def test_force_source_mode_does_not_reuse_previous_run_cache(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            old_root = pathlib.Path(name) / "old"
            new_root = pathlib.Path(name) / "new"
            sce.PageStore(old_root, []).save("1", "standings", "https://chess-results.com/tnr1.aspx", "<html>old</html>")
            forced = sce.PageStore(new_root, [old_root], reuse_cache=False)
            self.assertIsNone(forced.load("1", "standings"))


if __name__ == "__main__":
    unittest.main()
