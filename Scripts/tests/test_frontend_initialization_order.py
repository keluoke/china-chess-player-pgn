#!/usr/bin/env python3

from __future__ import annotations

import pathlib
import struct
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]


class FrontendInitializationOrderTest(unittest.TestCase):
    def test_domestic_deep_link_state_exists_before_startup(self) -> None:
        app = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
        startup = app.index("initialize();")
        for declaration in (
            "const domesticSeenIDs = new Set();",
            "const domesticShardLoaded = new Set();",
            "let domesticFullLoaded = false;",
            "let defaultSuggestionCache = null;",
            "let domesticRouting = null;",
            "let domesticRoutingRequest = null;",
        ):
            self.assertLess(
                app.index(declaration),
                startup,
                f"{declaration} must be initialized before domestic deep-link startup",
            )

    def test_reviewed_domestic_row_enriches_instead_of_duplicating_fide_card(self) -> None:
        app = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
        merge_start = app.index("function mergeDomesticRows(rows)")
        prepare_start = app.index("const player = preparePlayer({", merge_start)
        fide_merge = app.index("if (row.fideID)", merge_start)
        self.assertLess(fide_merge, prepare_start)
        self.assertIn(
            "players.find(player => String(player.fideID || \"\") === String(row.fideID))",
            app[fide_merge:prepare_start],
        )
        self.assertIn("return;", app[fide_merge:prepare_start])

    def test_user_input_during_bootstrap_is_preserved(self) -> None:
        app = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
        data_ready = app.index("const data = await loadData();")
        capture = app.index('const typedBeforeDataReady = els.searchInput?.value || "";')
        startup = app.index("initialize();")
        self.assertLess(data_ready, capture)
        self.assertLess(capture, startup)
        self.assertIn(
            'new URLSearchParams(location.search).get("q") || typedBeforeDataReady || ""',
            app,
        )

    def test_player_detail_actions_and_module_order_match_product_contract(self) -> None:
        app = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
        fide_detail = app[app.index("function renderDetail()"):app.index("function renderDomesticPlayerDetail")]
        domestic_detail = app[
            app.index("function renderDomesticPlayerDetail"):
            app.index("function requestDomesticShardPath")
        ]
        for detail in (fide_detail, domestic_detail):
            for removed in ("返回搜索", "分享档案", "身份异议", "删除 / 匿名化请求"):
                self.assertNotIn(removed, detail)
        self.assertLess(fide_detail.index('ratingCard("标准棋"'), fide_detail.index("staticPlayerHitBlock"))
        self.assertLess(fide_detail.index("staticPlayerHitBlock"), fide_detail.index("playerEventHistory"))

    def test_excluded_bulk_youth_indexes_are_not_requested(self) -> None:
        app = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("data/bulk/youth/manifest.json", app)
        self.assertNotIn("loadBulkStageIndex", app)
        self.assertNotIn("bulkPlayerHitBlock", app)

    def test_event_roster_uses_detail_fide_ids_and_distinct_empty_states(self) -> None:
        app = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
        renderer = app[app.index("function renderEvent()"):app.index("function eventViewerPlayer")]
        self.assertIn("eventDetail.players", renderer)
        self.assertIn("名单已同步", renderer)
        self.assertIn("已列入补录计划", renderer)
        self.assertIn("未单独发布完整名单", renderer)
        self.assertNotIn("该赛事已有赛事记录，但棋手名单尚未同步", renderer)
        self.assertIn('["日期", eventDateLabel(event)]', renderer)
        self.assertIn("中国棋手（名单标 CHN）", renderer)
        self.assertIn("eventPGNArchive(eventDetail)", renderer)
        self.assertIn("打开整场 PGN（", renderer)
        self.assertIn("eventArchive.gameCount", renderer)
        self.assertIn("detail.completeness?.matchedPairings", renderer)

    def test_styles_follow_system_dark_mode(self) -> None:
        styles = (ROOT / "docs" / "styles.css").read_text(encoding="utf-8")
        self.assertIn("@media (prefers-color-scheme: dark)", styles)
        dark = styles[styles.index("@media (prefers-color-scheme: dark)"):]
        self.assertIn("color-scheme: dark", dark)
        self.assertIn("--canvas: #0f141a", dark)

    def test_theme_control_supports_auto_and_manual_preferences(self) -> None:
        theme = (ROOT / "docs" / "theme.js").read_text(encoding="utf-8")
        self.assertIn('window.matchMedia("(prefers-color-scheme: dark)")', theme)
        self.assertIn('new Set(["auto", "light", "dark"])', theme)
        self.assertIn('localStorage.setItem(STORAGE_KEY, next)', theme)
        self.assertIn('systemDark.addEventListener("change"', theme)
        self.assertIn('window.addEventListener("storage"', theme)

        for name in ("index.html", "coverage.html", "leaderboards.html", "events.html", "contribute.html"):
            html = (ROOT / "docs" / name).read_text(encoding="utf-8")
            self.assertIn('theme.js?v=20260727-1', html)
            self.assertIn('data-theme-choice="auto"', html)
            self.assertIn('data-theme-choice="light"', html)
            self.assertIn('data-theme-choice="dark"', html)
            self.assertLess(html.index("theme.js?v=20260727-1"), html.index("styles.css?v=20260729-1"))

    def test_brand_and_search_hero_use_theme_appropriate_logos(self) -> None:
        index = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
        styles = (ROOT / "docs" / "styles.css").read_text(encoding="utf-8")
        header = index[index.index('class="topbar"'):index.index("</header>")]
        hero = index[index.index('class="search-command"'):index.index('id="searchResultsSection"')]
        for section in (header, hero):
            self.assertIn('src="assets/chessdb-logo-light.png"', section)
            self.assertIn('src="assets/chessdb-logo-dark.png"', section)
        self.assertIn('class="theme-logo brand-logo"', header)
        self.assertIn('class="theme-logo hero-logo"', hero)
        self.assertIn(':root[data-theme="dark"] .theme-logo .logo-on-light { display: none; }', styles)
        self.assertIn(':root[data-theme="dark"] .theme-logo .logo-on-dark { display: block; }', styles)
        self.assertIn('.search-command[data-mode="compact"] > .hero-logo,', styles)

        expected_dimensions = {
            "chessdb-logo-light.png": (726, 157),
            "chessdb-logo-dark.png": (726, 157),
            "chessdb-favicon-light.png": (512, 512),
            "chessdb-favicon-dark.png": (512, 512),
        }
        for name, expected in expected_dimensions.items():
            payload = (ROOT / "docs" / "assets" / name).read_bytes()
            self.assertEqual(payload[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(struct.unpack(">II", payload[16:24]), expected)

    def test_dark_search_surface_has_no_light_hero_background(self) -> None:
        styles = (ROOT / "docs" / "styles.css").read_text(encoding="utf-8")
        manual_dark = styles[
            styles.index(':root[data-theme="dark"] {'):
            styles.index("@media (prefers-color-scheme: dark)")
        ]
        self.assertIn("--hero-gradient:", manual_dark)
        self.assertIn("--search-field: #1b2530", manual_dark)
        self.assertIn(':root[data-theme="dark"] .search-box', manual_dark)
        self.assertIn(':root[data-theme="dark"] .hero-search-form > button', manual_dark)


if __name__ == "__main__":
    unittest.main()
