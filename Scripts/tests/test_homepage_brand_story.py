#!/usr/bin/env python3

from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]


class HomepageBrandStoryTest(unittest.TestCase):
    def test_search_stays_before_static_brand_story(self) -> None:
        html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
        self.assertIn('<body class="landing">', html)
        self.assertLess(html.index('id="searchInput"'), html.index('class="brand-story"'))
        self.assertEqual(html.count('class="brand-story"'), 1)
        self.assertEqual(html.count('class="story-panel'), 5)
        self.assertIn('data-player-total', html)
        self.assertIn('placeholder="输入中文名、拼音、FIDE ID，或赛事名称"', html)
        app = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
        self.assertIn('els.searchInput.placeholder = "输入中文名、拼音、FIDE ID，或赛事名称";', app)
        self.assertNotIn('els.searchInput.placeholder = "中文名 / 拼音 / FIDE ID / 赛事名";', app)
        search = html[html.index('class="search-command"'):html.index('id="searchResultsSection"')]
        for removed in ("hero-brand-mark", 'class="eyebrow"', "search-guidance"):
            self.assertNotIn(removed, search)
        self.assertLess(search.index('id="searchForm"'), search.index('id="searchSuggestions"'))
        self.assertIn("往下看，我们为什么做这件事", search)

    def test_brand_asset_is_reused_by_all_public_pages(self) -> None:
        for name in ("index.html", "coverage.html", "leaderboards.html", "events.html", "contribute.html"):
            html = (ROOT / "docs" / name).read_text(encoding="utf-8")
            self.assertIn('href="./brand.svg" type="image/svg+xml"', html)
            self.assertIn('href="./brand.svg#brand-mark"', html)
        svg = (ROOT / "docs" / "brand.svg").read_text(encoding="utf-8")
        self.assertIn('id="brand-mark"', svg)
        self.assertIn("#1f3b66", svg)
        self.assertIn("#c9a227", svg)

    def test_story_is_progressive_and_respects_motion_preferences(self) -> None:
        app = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "docs" / "styles.css").read_text(encoding="utf-8")
        self.assertIn("initializeLandingStory();", app)
        self.assertIn('IntersectionObserver', app)
        self.assertIn('prefers-reduced-motion: reduce', app)
        self.assertIn('scroll-snap-type: y proximity', styles)
        self.assertIn('data-focus-search', html := (ROOT / "docs" / "index.html").read_text(encoding="utf-8"))
        self.assertIn('reducedMotion ? "auto" : "smooth"', app)
        reduced = styles[styles.index("@media (prefers-reduced-motion: reduce)"):]
        self.assertIn("transition: none", reduced)
        self.assertIn(".landing-active .brand-story", styles)
        self.assertIn(".story-board.in-view .move-4", styles)
        self.assertIn("grid-column: 1 / -1", styles)

    def test_story_copy_does_not_name_external_data_sources(self) -> None:
        html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
        story = html[html.index('class="brand-story"'):]
        for forbidden in ("Chess-Results", "chess-results.com", "Lichess"):
            self.assertNotIn(forbidden, story)
        self.assertNotIn('href="./events.html"', story)
        self.assertNotIn('href="./coverage.html"', story)
        self.assertIn("已公开的对局", story)
        self.assertIn("GET /api/v1/manifest.json", story)
        self.assertIn('"players": "/api/v1/players.json"', story)
        for person_example in ("侯逸凡", "Hou", "HOU", "Yifan", "8602980"):
            self.assertNotIn(person_example, story)
        for kicker in ("01", "02", "03", "04", "05"):
            self.assertIn(f'<span class="story-kicker">{kicker}</span>', story)
        for removed_label in ("01 · 问题", "02 · 回答一", "03 · 回答二", "04 · 回答三", "05 · 立场"):
            self.assertNotIn(removed_label, story)
        opening = story[story.index('class="story-panel story-open"'):]
        self.assertNotIn("story-end-mark", opening)

    def test_default_suggestions_are_event_queries_not_people(self) -> None:
        app = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
        suggestions = app[app.index("function defaultSearchSuggestions"):app.index("function renderSearchSuggestions")]
        self.assertIn('["李成智杯", "全国青少年锦标赛", "棋协大师赛"]', suggestions)
        for person_example in ("侯逸凡", "Hou", "HOU", "Yifan", "8602980"):
            self.assertNotIn(person_example, suggestions)

    def test_heavy_viewer_assets_do_not_block_the_search_first_viewport(self) -> None:
        html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
        app = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn('id="searchTrust"', html)
        self.assertNotIn('<link rel="stylesheet" href="vendor/lichess-pgn-viewer/', html)
        self.assertNotIn('import LichessPgnViewer from', app)
        self.assertIn('import("./vendor/lichess-pgn-viewer/lichess-pgn-viewer.min.js")', app)
        self.assertIn('data-pgn-viewer-style', app)


if __name__ == "__main__":
    unittest.main()
