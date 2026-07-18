#!/usr/bin/env python3
"""Second-review (2026-07-18) mechanism tests.

Covers the ten required behaviours from review §7: archive-first matching,
natural-key safety, played-game denominators, per-board verification,
snapshot consistency, presentation-group formation, hard-conflict blocking,
dispute tombstones and input immutability.
"""

from __future__ import annotations

import copy
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "Scripts"))

import build_completeness_report as ccr  # noqa: E402
import sync_domestic_players as sdp  # noqa: E402
import validate_snapshot_consistency as vsc  # noqa: E402


def pairing(round_no, board, white_no, white, black_no, black, result="1 - 0", has_pgn=True):
    return {
        "board": str(board),
        "white": {"playerNo": str(white_no), "name": white},
        "black": {"playerNo": str(black_no), "name": black},
        "result": result,
        "hasPGN": has_pgn,
    }


def payload_with_rounds(rounds, players=None, standings=None, round_count=None):
    players = players or [
        {"playerNo": str(i), "name": f"Player, {chr(64 + i)}"} for i in range(1, 5)
    ]
    standings = standings if standings is not None else [
        {"playerNo": p["playerNo"], "rank": str(i + 1), "score": "1"}
        for i, p in enumerate(players)
    ]
    return {
        "tournamentID": "999001",
        "captureStatus": "complete",
        "roundCount": round_count if round_count is not None else len(rounds),
        "players": players,
        "standings": standings,
        "rounds": rounds,
    }


def archive_game(round_no, board, white, black, result="1-0"):
    return {"round": str(round_no), "board": str(board), "white": white, "black": black, "result": result}


class ArchiveFirstMatchingTest(unittest.TestCase):
    """§7.1: event archive alone must produce full matching without by-player."""

    def test_archive_matches_without_by_player_index(self):
        rounds = [{"round": "1", "pairings": [
            pairing(1, 1, 1, "Player, A", 2, "Player, B"),
            pairing(1, 2, 3, "Player, C", 4, "Player, D"),
        ]}]
        payload = payload_with_rounds(rounds)
        games = [
            archive_game(1, 1, "Player, A", "Player, B"),
            archive_game(1, 2, "Player, C", "Player, D"),
        ]
        with mock.patch.object(ccr, "parse_event_archive", return_value=games):
            report = ccr.event_report(payload, by_player_games={})
        self.assertEqual(report["counts"]["matchedPairings"], 2)
        self.assertEqual(report["gates"]["pgnMatched"]["matchedExact"], 2)
        self.assertEqual(report["archiveStatus"], "matched-full")
        self.assertTrue(report["eventComplete"])

    def test_fully_matched_event_leaves_the_queue(self):
        rounds = [{"round": "1", "pairings": [pairing(1, 1, 1, "Player, A", 2, "Player, B")]}]
        payload = payload_with_rounds(rounds)
        games = [archive_game(1, 1, "Player, A", "Player, B")]
        with mock.patch.object(ccr, "parse_event_archive", return_value=games):
            report = ccr.event_report(payload, by_player_games={})
        queue = ccr.supplement_queue([report], leads={})
        self.assertEqual(queue, [])


class NaturalKeyMatchingTest(unittest.TestCase):
    """§7.2: same names, reversed colors and different boards never mis-match."""

    def test_same_names_on_different_boards_do_not_double_match(self):
        rounds = [{"round": "1", "pairings": [
            pairing(1, 1, 1, "Wang, Wei", 2, "Li, Lei"),
            pairing(1, 2, 3, "Wang, Wei", 4, "Li, Lei"),  # same-name pair, other board
        ]}]
        payload = payload_with_rounds(rounds, players=[
            {"playerNo": "1", "name": "Wang, Wei"}, {"playerNo": "2", "name": "Li, Lei"},
            {"playerNo": "3", "name": "Wang, Wei"}, {"playerNo": "4", "name": "Li, Lei"},
        ])
        games = [archive_game(1, 1, "Wang, Wei", "Li, Lei")]
        match = ccr.match_archive_games(payload, games)
        self.assertEqual(match["matched"], 1)  # only board 1, never both

    def test_natural_key_with_disagreeing_names_is_not_silent(self):
        rounds = [{"round": "1", "pairings": [pairing(1, 1, 1, "Wang, Wei", 2, "Li, Lei")]}]
        payload = payload_with_rounds(rounds)
        games = [archive_game(1, 1, "Zhao, Min", "Qian, Hua")]  # key hit, names differ
        match = ccr.match_archive_games(payload, games)
        self.assertEqual(match["matched"], 0)
        self.assertEqual(match["keyNameMismatches"], 1)

    def test_name_fallback_is_reported_separately(self):
        rounds = [{"round": "1", "pairings": [pairing(1, 1, 1, "Wang, Wei", 2, "Li, Lei")]}]
        payload = payload_with_rounds(rounds)
        games = [{"round": "1", "board": "", "white": "Li, Lei", "black": "Wang, Wei", "result": "0-1"}]
        match = ccr.match_archive_games(payload, games)
        self.assertEqual(match["matchedExact"], 0)
        self.assertEqual(match["matchedNameFallback"], 1)  # reversed colors still one game


class PlayedGameDenominatorTest(unittest.TestCase):
    """§7.3: byes/forfeits leave the PGN denominator; results stay valid."""

    def test_forfeits_and_byes_leave_pgn_denominator(self):
        rounds = [{"round": "1", "pairings": [
            pairing(1, 1, 1, "Player, A", 2, "Player, B"),
            pairing(1, 2, 3, "Player, C", 4, "Player, D", result="+ - -", has_pgn=False),
            {"board": "3", "white": {"playerNo": "5", "name": "Player, E"},
             "black": {"name": "bye"}, "result": "1", "hasPGN": False},
        ]}]
        players = [{"playerNo": str(i), "name": f"Player, {chr(64 + i)}"} for i in range(1, 6)]
        payload = payload_with_rounds(rounds, players=players)
        with mock.patch.object(ccr, "parse_event_archive", return_value=[]):
            report = ccr.event_report(payload, by_player_games={})
        counts = report["counts"]
        self.assertEqual(counts["playedGames"], 1)
        self.assertEqual(counts["forfeits"], 1)
        self.assertEqual(report["pgnAvailability"], "advertised-full")  # 1/1 played advertised
        self.assertEqual(report["gates"]["results"]["valid"], 2)  # forfeit is a valid result

    def test_advertised_partial_uses_played_denominator(self):
        rounds = [{"round": "1", "pairings": [
            pairing(1, 1, 1, "Player, A", 2, "Player, B", has_pgn=True),
            pairing(1, 2, 3, "Player, C", 4, "Player, D", has_pgn=False),
        ]}]
        payload = payload_with_rounds(rounds)
        with mock.patch.object(ccr, "parse_event_archive", return_value=[]):
            report = ccr.event_report(payload, by_player_games={})
        self.assertEqual(report["pgnAvailability"], "advertised-partial")


class PerBoardVerificationTest(unittest.TestCase):
    """§7.4: advertised boards verify per pairing, not by comparing totals."""

    def test_totals_equal_but_wrong_board_does_not_complete(self):
        rounds = [{"round": "1", "pairings": [
            pairing(1, 1, 1, "Player, A", 2, "Player, B", has_pgn=True),
            pairing(1, 2, 3, "Player, C", 4, "Player, D", has_pgn=True),
        ]}]
        payload = payload_with_rounds(rounds)
        # Two archive games — but both are the same board; totals match (2),
        # per-board identity does not.
        games = [
            archive_game(1, 1, "Player, A", "Player, B"),
            archive_game(1, 1, "Player, A", "Player, B"),
        ]
        with mock.patch.object(ccr, "parse_event_archive", return_value=games):
            report = ccr.event_report(payload, by_player_games={})
        self.assertEqual(report["counts"]["matchedPairings"], 1)
        self.assertEqual(report["archiveStatus"], "matched-partial")
        self.assertFalse(report["eventComplete"])


class SnapshotConsistencyTest(unittest.TestCase):
    """§7.5: a mixed snapshotId across public manifests fails the build."""

    def _docs_tree(self, ids):
        tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(tmp.name)
        docs = root / "docs"
        (docs / "data" / "index").mkdir(parents=True)
        (docs / "data" / "index" / "manifest.json").write_text(json.dumps({"snapshotId": ids[0]}))
        (docs / "data").joinpath("snapshot.json").write_text(json.dumps({"snapshotId": ids[1]}))
        return tmp, root, docs

    def test_mismatch_fails(self):
        tmp, root, docs = self._docs_tree(["snap-A", "snap-B"])
        with tmp, mock.patch.object(vsc, "ROOT", root), mock.patch.object(vsc, "DOCS", docs), \
             mock.patch.dict("os.environ", {"SNAPSHOT_ID": "snap-A"}):
            self.assertEqual(vsc.main(), 1)

    def test_consistent_passes(self):
        tmp, root, docs = self._docs_tree(["snap-A", "snap-A"])
        with tmp, mock.patch.object(vsc, "ROOT", root), mock.patch.object(vsc, "DOCS", docs), \
             mock.patch.dict("os.environ", {"SNAPSHOT_ID": "snap-A"}):
            self.assertEqual(vsc.main(), 0)


def sighting(**kwargs):
    base = dict(
        sighting_id="s-" + kwargs.get("event_id", "e"), source="chess-results-event",
        event_id="chess-results-tnr1", event_name="赛事", event_date="",
        group="", age_stage="", player_name="张三", chinese_name="张三",
        pinyin_name="zhang san", sex="M", birth_year=None, province="",
        club="", rank="", score="", source_player_no="1", source_url="", notes="",
        rounds="",
    )
    base.update(kwargs)
    return sdp.Sighting(**base)


def entity(domestic_id, sightings):
    player = sdp.DomesticPlayer(
        domestic_id=domestic_id, canonical_id=domestic_id, identity_status="unlinked",
        chinese_name="张三", display_name="张三", sex="M",
    )
    player.sightings = sightings
    return player


def promotion_pair(exact_dates=True, sex_b="M", concurrent=False, club_b="X俱乐部"):
    """Entity A: 65%+ in 一级组; entity B: later 候补组 appearance, same club."""
    date_low = "2024-05-01" if exact_dates else "2024"
    date_high = "2025-03-01" if exact_dates else "2025"
    a = entity("domestic-aaa", [sighting(
        sighting_id="s-a", event_id="chess-results-tnr100",
        group="男子一级棋士组", club="X俱乐部", score="6", rounds="9",
        event_date=date_low, sex="M",
    )])
    b = entity("domestic-bbb", [sighting(
        sighting_id="s-b",
        event_id="chess-results-tnr100" if concurrent else "chess-results-tnr200",
        group="男子候补棋协大师组", club=club_b, score="4", rounds="9",
        event_date=date_low if concurrent else date_high, sex=sex_b,
    )])
    b.sex = sex_b
    return a, b


class PresentationGroupTest(unittest.TestCase):
    """§7.6–7.10: high-confidence grouping, conflicts, disputes, immutability."""

    def _candidates(self, a, b):
        return sdp.build_identity_candidates([a, b])

    def test_club_plus_promotion_forms_high_confidence_group(self):
        a, b = promotion_pair()
        candidates, conflicts = self._candidates(a, b)
        self.assertEqual(conflicts, [])
        self.assertEqual(candidates[0]["queueTier"], "suggested-high")
        self.assertTrue(candidates[0]["presentationEligible"])
        groups = sdp.build_presentation_groups([a, b], candidates, conflicts)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["members"], ["domestic-aaa", "domestic-bbb"])
        self.assertEqual(groups[0]["identityBasis"], "presentation-high")

    def test_year_only_dates_do_not_aggregate(self):
        a, b = promotion_pair(exact_dates=False)
        candidates, conflicts = self._candidates(a, b)
        self.assertFalse(candidates[0]["presentationEligible"])
        groups = sdp.build_presentation_groups([a, b], candidates, conflicts)
        self.assertEqual(groups, [])

    def test_concurrent_event_blocks_grouping(self):
        a, b = promotion_pair(concurrent=True)
        candidates, conflicts = self._candidates(a, b)
        self.assertTrue(conflicts)
        self.assertIn("concurrent-event", conflicts[0]["reasons"][0])
        groups = sdp.build_presentation_groups([a, b], candidates, conflicts)
        self.assertEqual(groups, [])

    def test_sex_conflict_blocks_grouping(self):
        a, b = promotion_pair(sex_b="F")
        candidates, conflicts = self._candidates(a, b)
        self.assertTrue(any("sex-conflict" in edge["reasons"] for edge in conflicts))
        groups = sdp.build_presentation_groups([a, b], candidates, conflicts)
        self.assertEqual(groups, [])

    def test_dispute_tombstone_blocks_regrouping(self):
        a, b = promotion_pair()
        candidates, conflicts = self._candidates(a, b)
        blocked = {sdp.pair_hash("domestic-aaa", "domestic-bbb"): "disputed"}
        with mock.patch.object(sdp, "load_presentation_disputes", return_value=blocked):
            groups = sdp.build_presentation_groups([a, b], candidates, conflicts)
        self.assertEqual(groups, [])
        # §7.10: after the split the projection recomputes cleanly without
        # the pair — and re-forms once the dispute is withdrawn.
        with mock.patch.object(sdp, "load_presentation_disputes", return_value={}):
            regrouped = sdp.build_presentation_groups([a, b], candidates, conflicts)
        self.assertEqual(len(regrouped), 1)

    def test_grouping_never_mutates_inputs(self):
        a, b = promotion_pair()
        candidates, conflicts = self._candidates(a, b)
        before = (copy.deepcopy(a.payload()), copy.deepcopy(b.payload()),
                  copy.deepcopy(candidates))
        with mock.patch.object(sdp, "load_presentation_disputes", return_value={}):
            sdp.build_presentation_groups([a, b], candidates, conflicts)
        after = (a.payload(), b.payload(), candidates)
        self.assertEqual(before[0], after[0])
        self.assertEqual(before[1], after[1])
        self.assertEqual(before[2], after[2])

    def test_transitive_closure_never_bypasses_conflict_edge(self):
        # a—b and b—c are eligible; a—c is a hard conflict → whole component
        # must stay split (never merge a and c through b).
        a, b = promotion_pair()
        c = entity("domestic-ccc", [sighting(
            sighting_id="s-c", event_id="chess-results-tnr300",
            group="男子候补棋协大师组", club="X俱乐部", score="4", rounds="9",
            event_date="2025-06-01", sex="M",
        )])
        candidates, _ = sdp.build_identity_candidates([a, b, c])
        eligible = [dict(card, presentationEligible=True) for card in candidates
                    if not card.get("conflictEdges")]
        conflict = [{"domesticIDs": ["domestic-aaa", "domestic-ccc"],
                     "normalizedName": "张三", "reasons": ["sex-conflict"]}]
        with mock.patch.object(sdp, "load_presentation_disputes", return_value={}):
            groups = sdp.build_presentation_groups([a, b, c], eligible, conflict)
        for group in groups:
            self.assertFalse({"domestic-aaa", "domestic-ccc"} <= set(group["members"]))


if __name__ == "__main__":
    unittest.main()
