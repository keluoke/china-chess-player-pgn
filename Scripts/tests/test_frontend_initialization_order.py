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
        ):
            self.assertLess(
                app.index(declaration),
                startup,
                f"{declaration} must be initialized before domestic deep-link startup",
            )


if __name__ == "__main__":
    unittest.main()
