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
