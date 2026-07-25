from __future__ import annotations

import pathlib
import sys
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

import build_leaderboards as leaderboards  # noqa: E402
import build_search_bootstrap as search_bootstrap  # noqa: E402


class DomesticSearchRoutingTests(unittest.TestCase):
    def test_middle_hanzi_and_pinyin_route_to_existing_prefix_shard(self) -> None:
        row = {
            "domesticID": "domestic-a123",
            "displayName": "侯逸凡",
            "pinyin": "Hou Yifan",
            "aliases": ["侯逸凡"],
        }
        shards = search_bootstrap.shard_keys(row)
        terms = search_bootstrap.routing_terms(row)
        self.assertIn("g:逸凡", terms)
        self.assertIn("p:yifan", terms)
        self.assertIn("p:houyifan", terms)
        self.assertTrue(shards)
        self.assertIn(search_bootstrap.primary_search_shard(row), shards)


class LeaderboardDimensionTests(unittest.TestCase):
    def test_controls_and_sexes_are_ranked_independently(self) -> None:
        rows = [
            {"fideID": "1", "birthYear": 2016, "sex": "M", "standard": 1800, "rapid": None},
            {"fideID": "2", "birthYear": 2016, "sex": "F", "standard": 1700, "rapid": 1900},
            {"fideID": "3", "birthYear": 2015, "sex": "F", "standard": None, "rapid": 1800},
        ]
        standard = leaderboards.ranking_payload(rows, "standard", 100)
        rapid_female = leaderboards.ranking_payload(
            [row for row in rows if row["sex"] == "F"], "rapid", 100
        )
        self.assertEqual([row["fideID"] for row in standard["players"]], ["1", "2"])
        self.assertEqual([row["fideID"] for row in rapid_female["players"]], ["2", "3"])
        self.assertEqual(standard["birthYears"]["2016"]["totalEligible"], 2)


class PublicNavigationTests(unittest.TestCase):
    def test_internal_pages_are_noindex_but_privacy_request_stays_publicly_reachable(self) -> None:
        index = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
        self.assertIn("contribute.html?type=privacy-request", index)
        self.assertNotIn('href="./events.html"', index)
        self.assertNotIn('href="./coverage.html"', index)
        for name in ("events.html", "coverage.html", "contribute.html"):
            text = (ROOT / "docs" / name).read_text(encoding="utf-8")
            self.assertIn('name="robots" content="noindex,nofollow"', text)


if __name__ == "__main__":
    unittest.main()
