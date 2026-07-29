from __future__ import annotations

import contextlib
import io
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

import build_leaderboards as leaderboards  # noqa: E402
import build_search_bootstrap as search_bootstrap  # noqa: E402
import validate_public_privacy as public_privacy  # noqa: E402


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

    def test_chinese_names_gain_default_and_surname_search_pinyin(self) -> None:
        expected = {
            "单亦平": "shan yi ping",
            "曾上喆": "zeng shang zhe",
            "仇一帆": "qiu yi fan",
            "解嘉祥": "xie jia xiang",
            "查云汐": "zha yun xi",
            "覃子晗": "qin zi han",
            "朴佳伊": "piao jia yi",
            "区海": "ou hai",
            "乐夏衡": "yue xia heng",
        }
        for name, surname_variant in expected.items():
            with self.subTest(name=name):
                aliases = search_bootstrap.chinese_name_pinyin_aliases(name)
                self.assertIn(surname_variant, aliases)
                self.assertGreaterEqual(len(aliases), 1)

    def test_derived_pinyin_enters_search_shard_and_routing_terms(self) -> None:
        row = {
            "domesticID": "domestic-a123",
            "displayName": "单亦平",
            "searchAliases": ["shan yi ping"],
        }
        self.assertIn("ps", search_bootstrap.shard_keys(row))
        self.assertIn("p:shanyiping", search_bootstrap.routing_terms(row))
        self.assertIn("p:shan", search_bootstrap.routing_terms(row))


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

    def test_public_markdown_is_deny_by_default_and_allowlisted_files_are_clean(self) -> None:
        action = (ROOT / ".github" / "actions" / "prepare-static-site" / "action.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("--exclude '*.md'", action)
        self.assertIn("--exclude 'data/index/player-participation/'", action)
        self.assertIn("Scripts/public_markdown_allowlist.txt", action)
        self.assertIn("validate_public_privacy.py --site-root", action)
        allowlist = public_privacy.public_markdown_allowlist()
        self.assertEqual(allowlist, ("API.md", "PUBLIC_METRICS.md"))
        for relative in allowlist:
            text = (ROOT / "docs" / relative).read_text(encoding="utf-8")
            self.assertEqual(public_privacy.markdown_offenses(text), [])

    def test_privacy_gate_rejects_unallowlisted_markdown_in_site_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            site = pathlib.Path(temp)
            (site / "reviews").mkdir()
            (site / "reviews" / "internal.md").write_text(
                "internal Chess-Results runbook", encoding="utf-8"
            )
            with (
                mock.patch.object(
                    sys, "argv", ["validate_public_privacy.py", "--site-root", str(site)]
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(public_privacy.main(), 1)

    def test_leaderboard_requires_versioned_schema_v2_data(self) -> None:
        script = (ROOT / "docs" / "leaderboards.js").read_text(encoding="utf-8")
        self.assertIn('snapshotURL.searchParams.set("resolve", String(Date.now()))', script)
        self.assertIn('fetch(snapshotURL, { cache: "no-store" })', script)
        self.assertIn('url.searchParams.set("v", snapshotId)', script)
        self.assertIn("assertLeaderboardSchema(payloads[0])", script)
        self.assertNotIn("data.controls ??", script)
        self.assertNotIn("data.sexes ??", script)
        self.assertNotIn("players: group?.players", script)

    def test_player_detail_compacts_search_hero_and_owns_h1(self) -> None:
        index = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
        app = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "docs" / "styles.css").read_text(encoding="utf-8")
        self.assertIn('id="searchCommand" data-mode="hero"', index)
        self.assertIn('els.searchCommand.dataset.mode =', app)
        self.assertIn('<h1>${escapeHTML(displayName(player))}</h1>', app)
        self.assertIn('.search-command[data-mode="compact"]', styles)
        self.assertIn(".detail-title {\n    flex-direction: column;", styles)
        self.assertIn("function identityDisputeHref(group, player)", app)
        self.assertIn('type: "identity-dispute"', app)
        self.assertIn("高置信归组", app)


if __name__ == "__main__":
    unittest.main()
