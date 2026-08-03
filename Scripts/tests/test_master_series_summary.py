import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Scripts"))

import build_master_series_summary as summary  # noqa: E402


def event(tournament_id, year, name, *, detail="published", station=None, group=None):
    return {
        "id": f"chess-results:{tournament_id}",
        "series": "chess-association-master",
        "year": str(year),
        "station": station,
        "groupLabel": group,
        "tournamentID": str(tournament_id),
        "date": f"{year}-07-01",
        "name": name,
        "detailStatus": detail,
        "participants": "40",
        "rounds": "9",
    }


def report(tournament_id, status, archived=0, played=100):
    return {
        "tournamentID": str(tournament_id),
        "pgnIngestStatus": status,
        "counts": {"archivedGames": archived, "playedGames": played, "players": 40, "roundsExpected": 9},
    }


class MasterSeriesSummaryTests(unittest.TestCase):
    def test_counts_only_published_details_and_keeps_pgn_categories_distinct(self):
        public = {"events": [
            event(1, 2026, "2026 National CCA Master Tournament - Open (Bengbu Station)"),
            event(2, 2026, "2026 全国国际象棋棋协大师赛", station="盐城站", group="棋协大师组"),
            event(3, 2025, "2025 全国国际象棋棋协大师赛", station="上海站", group="男子一级棋士组"),
            event(4, 2025, "2025 全国国际象棋棋协大师赛", station="上海站", group="女子一级棋士组"),
            event(5, 2024, "2024 全国国际象棋棋协大师赛", station="北京站", detail="missing-detail"),
            {"series": "other", "year": "2026", "detailStatus": "published"},
        ]}
        completeness = {"events": [
            report(1, "source-published-complete", 90, 220),
            report(2, "full-board-complete", 100, 100),
            report(3, "not-published", 0, 180),
            report(4, "source-published-missing", 0, 170),
        ]}

        payload = summary.build_summary(public, completeness)

        self.assertEqual(payload["totals"]["groups"], 4)
        self.assertEqual(payload["totals"]["stations"], 3)
        self.assertEqual(payload["totals"]["metadataOnlyExcluded"], 1)
        self.assertEqual(payload["totals"]["statusCounts"]["full"], 1)
        self.assertEqual(payload["totals"]["statusCounts"]["live"], 1)
        self.assertEqual(payload["totals"]["statusCounts"]["missing"], 1)
        self.assertEqual(payload["totals"]["statusCounts"]["none"], 1)
        year_2026 = payload["years"][0]
        self.assertEqual(year_2026["stationCount"], 2)
        self.assertIn("蚌埠站", {station["station"] for station in year_2026["stations"]})

    def test_unknown_group_is_visible_and_marked_pending(self):
        row = summary.group_row(
            event(9, 2025, "2025 National Amateur Chess Master Tournament Hefei Station"),
            report(9, "source-published-partial", 10, 100),
        )
        self.assertEqual(row["station"], "合肥站")
        self.assertEqual(row["groupLabel"], "组别待核")
        self.assertTrue(row["groupLabelPending"])
        self.assertEqual(row["pgnStatus"], "partial")
        self.assertEqual(row["allBoardCoveragePercent"], 10.0)

    def test_frontend_refresh_is_cache_busted_and_accessible(self):
        script = (ROOT / "docs" / "master-series.js").read_text(encoding="utf-8")
        page = (ROOT / "docs" / "master-series.html").read_text(encoding="utf-8")
        self.assertIn('url.searchParams.set("resolve", String(Date.now()))', script)
        self.assertIn('fetch(url, { cache: "no-store" })', script)
        self.assertIn('id="refreshStatus" role="status" aria-live="polite"', page)
        self.assertIn('id="refreshButton"', page)


if __name__ == "__main__":
    unittest.main()
