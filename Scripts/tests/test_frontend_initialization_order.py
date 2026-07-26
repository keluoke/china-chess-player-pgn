#!/usr/bin/env python3

from __future__ import annotations

import pathlib
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

    def test_optional_bulk_indexes_degrade_without_json_errors(self) -> None:
        app = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
        loader = app[app.index("async function loadBulkStageIndex"):app.index("function bulkPlayerHitBlock")]
        self.assertIn("fetchJSON(stage.indexPath, false)", loader)
        self.assertIn(".catch(() => {", loader)
        self.assertIn("return [];", loader)
        self.assertNotIn("response.json()", loader)

    def test_event_roster_uses_detail_fide_ids_and_distinct_empty_states(self) -> None:
        app = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
        renderer = app[app.index("function renderEvent()"):app.index("function eventViewerPlayer")]
        self.assertIn("eventDetail.players", renderer)
        self.assertIn("名单已同步", renderer)
        self.assertIn("维护者本机补抓队列", renderer)
        self.assertNotIn("该赛事已有赛事记录，但棋手名单尚未同步", renderer)
        self.assertIn("中国棋手（名单标 CHN）", renderer)

    def test_styles_follow_system_dark_mode(self) -> None:
        styles = (ROOT / "docs" / "styles.css").read_text(encoding="utf-8")
        self.assertIn("@media (prefers-color-scheme: dark)", styles)
        dark = styles[styles.index("@media (prefers-color-scheme: dark)"):]
        self.assertIn("color-scheme: dark", dark)
        self.assertIn("--canvas: #101419", dark)


if __name__ == "__main__":
    unittest.main()
