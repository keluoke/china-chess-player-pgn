#!/usr/bin/env python3

from __future__ import annotations

import csv
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Scripts"))

import build_player_participation as bpp  # noqa: E402
import sync_domestic_players as sdp  # noqa: E402


def write_csv(path: pathlib.Path, headers: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def domestic_player(event_id: str = "chess-results-tnr990921") -> sdp.DomesticPlayer:
    sighting = sdp.Sighting(
        sighting_id="s-jin", source="event", event_id=event_id,
        event_name="2024棋协大师赛苏州站公开组", event_date="2024-08-01",
        group="公开组", age_stage="OPEN", player_name="金鸿涛", chinese_name="金鸿涛",
        pinyin_name="jin hongtao", sex="M", birth_year=2010, province="江苏",
        club="南京博智弈国际象棋俱乐部", rank="1", score="7", source_player_no="1",
        source_url="", notes="", rounds="9",
    )
    return sdp.DomesticPlayer(
        domestic_id="domestic-3718d907d01f", canonical_id="domestic-3718d907d01f",
        identity_status="unlinked", chinese_name="金鸿涛", display_name="金鸿涛",
        pinyin_name="jin hongtao", sex="M", sightings=[sighting],
    )


def liu_xinmeng_player(index: int) -> sdp.DomesticPlayer:
    sighting = sdp.Sighting(
        sighting_id=f"s-liu-{index}", source="event",
        event_id=f"chess-results-tnr{900000 + index}",
        event_name=f"{2023 + index}棋协大师赛女子组",
        event_date=str(2023 + index), group="女子一级棋士组",
        age_stage="", player_name="刘欣萌", chinese_name="刘欣萌",
        pinyin_name="liu xin meng", sex="F", birth_year=None, province="上海",
        club=f"上海测试棋校{index}", rank=str(index + 1), score="5",
        source_player_no=str(index + 1), source_url="", notes="", rounds="9",
    )
    domestic_id = sdp.provisional_domestic_id(sighting)
    return sdp.DomesticPlayer(
        domestic_id=domestic_id, canonical_id=domestic_id,
        identity_status="unlinked", chinese_name="刘欣萌",
        display_name="刘欣萌", pinyin_name="liu xin meng",
        sex="F", sightings=[sighting],
    )


class IdentityEvidenceChainTest(unittest.TestCase):
    def test_same_event_fide_evidence_projects_to_fide_card(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            registry = root / "players.json"
            registry.write_text(json.dumps([{
                "fideID": "8640491", "displayName": "金鸿涛", "name": "Jin, Hongtao",
                "chineseName": "金鸿涛", "aliases": ["Jin Hongtao"],
            }], ensure_ascii=False), encoding="utf-8")
            events = root / "events.csv"
            write_csv(events, ["fide_id", "tnrid", "player_name", "club"], [{
                "fide_id": "8640491", "tnrid": "990921", "player_name": "Jin, Hongtao",
                "club": "南京博智弈国际象棋俱乐部",
            }])
            player = domestic_player()
            details = root / "details"
            details.mkdir()
            (details / "tnr990921.json").write_text(json.dumps({
                "tournamentID": "990921",
                "players": [{"fideID": "8640491", "chineseName": "金鸿涛"}],
            }, ensure_ascii=False), encoding="utf-8")
            candidates = sdp.build_fide_candidates([player], registry, events, [], root / "none.json", details)
            self.assertEqual(len(candidates), 1)
            self.assertTrue(candidates[0]["presentationEligible"])
            self.assertEqual(candidates[0]["directSameEventIDs"], ["990921"])
            with mock.patch.object(sdp, "load_presentation_disputes", return_value={}):
                groups = sdp.build_presentation_groups([player], [], [], candidates)
            self.assertEqual(groups[0]["canonicalFideID"], "8640491")
            self.assertEqual(groups[0]["disputeMembers"][0], "fide-8640491")

    def test_repeated_chinese_name_becomes_presentation_hint_not_registry_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            registry = root / "players.json"
            registry.write_text(json.dumps([{
                "fideID": "8608369", "displayName": "Dai, Wenzhi", "chineseName": "",
            }]), encoding="utf-8")
            events = root / "events.csv"
            write_csv(events, ["fide_id", "tnrid", "player_name", "club"], [
                {"fide_id": "8608369", "tnrid": "1", "player_name": "戴文智", "club": ""},
                {"fide_id": "8608369", "tnrid": "2", "player_name": "戴文智", "club": ""},
            ])
            name_map = root / "names.csv"
            write_csv(name_map, ["fide_id", "chinese_name", "evidence_tnrid"], [])
            candidates = sdp.build_chinese_name_candidates(registry, events, name_map)
            self.assertEqual(candidates[0]["suggestedChineseName"], "戴文智")
            self.assertTrue(candidates[0]["presentationEligible"])
            unchanged = json.loads(registry.read_text(encoding="utf-8"))[0]
            self.assertEqual(unchanged["chineseName"], "")

    def test_latin_fide_alias_nominates_every_member_of_large_chinese_cluster(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            registry = root / "players.json"
            registry.write_text(json.dumps([{
                "fideID": "8656819", "displayName": "Liu, Xinmeng",
                "name": "Liu, Xinmeng", "aliases": ["Liu Xinmeng", "Xinmeng Liu"],
                "sex": "F", "birthYear": 2013,
            }]), encoding="utf-8")
            events = root / "events.csv"
            write_csv(events, ["fide_id", "tnrid", "player_name", "club"], [])
            details = root / "details"
            details.mkdir()
            players = [liu_xinmeng_player(index) for index in range(4)]

            candidates = sdp.build_fide_candidates(
                players, registry, events, [], root / "none.json", details,
            )

            self.assertEqual(len(candidates), 4)
            self.assertEqual({row["candidateFideID"] for row in candidates}, {"8656819"})
            self.assertTrue(all(row["sameNameClusterSize"] == 4 for row in candidates))
            self.assertTrue(all("liuxinmeng" in row["matchedNameKeys"] for row in candidates))
            self.assertTrue(all(not row["presentationEligible"] for row in candidates))

    def test_large_chinese_cluster_needs_member_level_fide_evidence_for_display(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            registry = root / "players.json"
            registry.write_text(json.dumps([{
                "fideID": "8656819", "displayName": "刘欣萌",
                "name": "Liu, Xinmeng", "chineseName": "刘欣萌",
                "aliases": ["Liu Xinmeng"], "sex": "F", "birthYear": 2013,
            }], ensure_ascii=False), encoding="utf-8")
            events = root / "events.csv"
            write_csv(events, ["fide_id", "tnrid", "player_name", "club"], [{
                "fide_id": "8656819", "tnrid": "900000",
                "player_name": "刘欣萌", "club": "",
            }])
            details = root / "details"
            details.mkdir()
            players = [liu_xinmeng_player(index) for index in range(4)]

            candidates = sdp.build_fide_candidates(
                players, registry, events, [], root / "none.json", details,
            )

            self.assertEqual(len(candidates), 4)
            eligible = [row for row in candidates if row["presentationEligible"]]
            self.assertEqual(len(eligible), 1)
            self.assertEqual(eligible[0]["directSameEventIDs"], ["900000"])

    def test_reviewed_domestic_links_project_four_sightings_onto_one_fide_card(self):
        source_players = [liu_xinmeng_player(index) for index in range(4)]
        sightings = [player.sightings[0] for player in source_players]
        links = [
            sdp.IdentityLink(
                from_type="domestic", from_id=player.domestic_id,
                to_type="fide", to_id="8656819", confidence="reviewed-high",
                evidence="maintainer-confirmed", source_url="", reviewed_by="maintainer",
                reviewed_at="2026-07-25", notes="",
            )
            for player in source_players
        ]

        players = sdp.build_players(sightings, links)
        self.assertEqual(len(players), 1)
        self.assertEqual(players[0].fide_id, "8656819")
        self.assertEqual(len(players[0].sightings), 4)
        with mock.patch.object(sdp, "load_presentation_disputes", return_value={}):
            groups = sdp.build_presentation_groups(players, [], [])
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["canonicalFideID"], "8656819")
        self.assertEqual(groups[0]["suggestedChineseName"], "刘欣萌")
        self.assertEqual(groups[0]["sightingCount"], 4)
        self.assertEqual(groups[0]["identityBasis"], "reviewed-fide-link")


class ParticipationChainTest(unittest.TestCase):
    def test_result_without_pgn_still_enters_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            events = root / "events.csv"
            write_csv(events, [
                "fide_id", "tnrid", "tournament_name", "end_date", "rank", "rounds",
                "participants", "player_name", "club",
            ], [{
                "fide_id": "8608369", "tnrid": "123", "tournament_name": "测试赛事",
                "end_date": "2024-01-02", "rank": "7", "rounds": "9", "participants": "60",
                "player_name": "戴文智", "club": "测试俱乐部",
            }])
            catalog = root / "catalog.json"
            catalog.write_text(json.dumps({"events": []}), encoding="utf-8")
            details = root / "players"
            details.mkdir()
            rows = bpp.build_rows(events, catalog, details)
            self.assertEqual(len(rows["8608369"]), 1)
            self.assertEqual(rows["8608369"][0]["resultStatus"], "recorded")
            self.assertEqual(rows["8608369"][0]["pgnStatus"], "not-archived")
            self.assertNotIn("club", rows["8608369"][0])

    def test_future_event_is_not_labeled_as_final_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            events = root / "events.csv"
            write_csv(events, ["fide_id", "tnrid", "tournament_name", "end_date"], [{
                "fide_id": "8608369", "tnrid": "124", "tournament_name": "未来赛事",
                "end_date": "2999-01-01",
            }])
            catalog = root / "catalog.json"
            catalog.write_text(json.dumps({"events": []}), encoding="utf-8")
            details = root / "players"
            details.mkdir()
            rows = bpp.build_rows(events, catalog, details)
            self.assertEqual(rows["8608369"][0]["resultStatus"], "scheduled")


if __name__ == "__main__":
    unittest.main()
