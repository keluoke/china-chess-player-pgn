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


if __name__ == "__main__":
    unittest.main()
