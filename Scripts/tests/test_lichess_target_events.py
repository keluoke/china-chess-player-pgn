from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "Scripts"))

import sync_lichess_broadcast_bulk as lichess  # noqa: E402


class LichessTargetEventTests(unittest.TestCase):
    def test_skippable_zstd_preamble_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp) / "broadcast.pgn.zst"
            path.write_bytes(
                b"\x50\x2a\x4d\x18"
                b"\x04\x00\x00\x00"
                b"meta"
                b"\x28\xb5\x2f\xfd"
                b"payload"
            )
            lichess.validate_local_shard(path, path.stat().st_size)

    def test_target_series_filter_keeps_standard_and_rejects_rapid(self) -> None:
        standard = {
            "series": "asian-youth",
            "name": "Asian Youth Chess Championships 2025 - U12",
        }
        rapid = {
            "series": "asian-youth",
            "name": "Asian Youth Rapid Chess Championships 2025 - U12",
        }
        self.assertTrue(lichess.target_series_event(standard))
        self.assertFalse(lichess.target_series_event(rapid))

    def test_broadcast_compatibility_rejects_other_disciplines_and_groups(self) -> None:
        event = {
            "series": "world-youth",
            "name": "World Youth Chess Championship 2025 - Girls 14",
            "date": "2025-11-20",
        }
        self.assertTrue(lichess.compatible_target_broadcast(event, {
            "BroadcastName": "FIDE World Youth Chess Championships 2025 | G14",
            "Date": "2025.11.10",
        }))
        self.assertFalse(lichess.compatible_target_broadcast(event, {
            "BroadcastName": "FIDE World Youth Rapid Championships 2025 | G14",
            "Date": "2025.11.10",
        }))
        self.assertFalse(lichess.compatible_target_broadcast(event, {
            "BroadcastName": "FIDE World Youth Chess Championships 2025 | O14",
            "Date": "2025.11.10",
        }))
        self.assertEqual(
            lichess.event_group("World Youth U14, U16 & U18 Championships 2025 - G18"),
            ("G", "18"),
        )
        self.assertEqual(
            lichess.event_group("FIDE World Cadet Chess Championships 2024 | Girls U12"),
            ("G", "12"),
        )

    def test_target_event_archive_keeps_lichess_attribution(self) -> None:
        detail = {
            "players": [
                {"playerNo": "1", "name": "Alpha, One", "fideID": "1001"},
                {"playerNo": "2", "name": "Beta, Two", "fideID": "1002"},
            ],
            "rounds": [{
                "round": "1",
                "pairings": [{
                    "board": "7",
                    "white": {"playerNo": "1", "name": "Alpha, One"},
                    "black": {"playerNo": "2", "name": "Beta, Two"},
                    "result": "1 - 0",
                }],
            }],
        }
        game = "\n".join([
            '[Event "Round 1: Alpha, One - Beta, Two"]',
            '[Round "1.1"]',
            '[White "Alpha, One"]',
            '[Black "Beta, Two"]',
            '[WhiteFideId "1001"]',
            '[BlackFideId "1002"]',
            '[Result "1-0"]',
            '[BroadcastName "World Youth Chess Championship Test | U12"]',
            '[BroadcastURL "https://lichess.org/broadcast/test/round-1/abc"]',
            "",
            "1. e4 e5 1-0",
        ])
        with tempfile.TemporaryDirectory() as temp:
            docs_data = pathlib.Path(temp)
            shard_path = docs_data / "bulk" / "lichess-broadcast" / "shards" / "test.pgn.zst"
            shard_path.parent.mkdir(parents=True)
            shard_path.write_bytes(b"placeholder")
            shard = lichess.BroadcastShard(
                month="2025-11",
                url="https://database.lichess.org/broadcast/test.pgn.zst",
                file_name=shard_path.name,
                size_text="1 kB",
                size_bytes=11,
                games=1,
                calendar_url="",
            )
            with (
                mock.patch.object(lichess, "DOCS_DATA", docs_data),
                mock.patch.object(lichess, "PUBLIC_DOCS_DATA", docs_data),
                mock.patch.object(lichess, "EXISTING_SHARD_ROOT", shard_path.parent),
                mock.patch.object(lichess, "LICHESS_EVENT_ROOT", docs_data / "bulk" / "lichess-events"),
                mock.patch.object(lichess, "target_event_rows", return_value=[{
                    "tournamentID": "999001",
                    "series": "world-youth",
                    "name": "World Youth Chess Championship Test U12",
                    "date": "2025-11-30",
                    "year": "2025",
                    "detail": detail,
                }]),
                mock.patch.object(lichess, "iter_zst_pgn_games", return_value=iter([game])),
            ):
                totals = lichess.build_target_event_archives([shard], dry_run=False)
                archive = (docs_data / "bulk" / "lichess-events" / "pgn" / "tnr999001.pgn").read_text()
                manifest = json.loads((docs_data / "bulk" / "lichess-events" / "manifest.json").read_text())
        self.assertEqual(totals["broadcastGames"], 1)
        self.assertIn('[Board "7"]', archive)
        self.assertIn('[Source "Lichess Broadcasts"]', archive)
        self.assertIn('[License "CC BY-SA 4.0"]', archive)
        self.assertTrue(manifest["events"][0]["broadcastComplete"])


if __name__ == "__main__":
    unittest.main()
