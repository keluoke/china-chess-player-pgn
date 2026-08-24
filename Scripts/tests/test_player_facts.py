#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Scripts"))

import build_player_facts as bpf  # noqa: E402
import build_static_player_pgn as bsp  # noqa: E402
import canonical_player_facts as cpf  # noqa: E402
import snapshot_context  # noqa: E402


PGN = """[Event "Canonical Event"]
[Date "2026.08.01"]
[Round "1"]
[Board "1"]
[White "Alpha, One"]
[Black "Beta, Two"]
[WhiteFideId "1001"]
[BlackFideId "1002"]
[Result "1-0"]

1. e4 e5 2. Nf3 Nc6 1-0
"""


class CanonicalPlayerFactTest(unittest.TestCase):
    def fixture(self, root: pathlib.Path) -> dict[str, pathlib.Path]:
        registry = root / "docs/data/registry/players.json"
        registry.parent.mkdir(parents=True)
        registry.write_text(json.dumps([
            {"fideID": "1001", "name": "Alpha, One", "displayName": "Alpha, One", "birthYear": 2010},
            {"fideID": "1002", "name": "Beta, Two", "displayName": "Beta, Two", "birthYear": 2010},
        ]), encoding="utf-8")
        details = root / "data/generated/chess-results-event-details"
        details.mkdir(parents=True)
        (details / "tnr9001.json").write_text(json.dumps({
            "tournamentID": "9001", "sourceName": "Canonical Event",
            "dateEnd": "2026-08-01", "roundCount": 1,
            "players": [
                {"playerNo": "1", "fideID": "1001", "name": "Alpha, One"},
                {"playerNo": "2", "fideID": "1002", "name": "Beta, Two"},
            ],
            "standings": [
                {"playerNo": "1", "rank": "1", "score": "1"},
                {"playerNo": "2", "rank": "2", "score": "0"},
            ],
            "rounds": [{"round": 1, "pairings": [{
                "board": "1", "white": {"playerNo": "1", "fideID": "1001", "name": "Alpha, One"},
                "black": {"playerNo": "2", "fideID": "1002", "name": "Beta, Two"},
                "result": "1-0",
            }]}],
        }), encoding="utf-8")
        event_pgn = root / "data/generated/chess-results-event-pgn"
        event_pgn.mkdir(parents=True)
        archive = event_pgn / "tnr9001.pgn"
        archive.write_text(PGN, encoding="utf-8")
        receipt = root / "data/generated/r2-object-receipts/events--chess-results.json"
        receipt.parent.mkdir(parents=True)
        receipt.write_text(json.dumps({"objects": [{
            "key": "events/chess-results/tnr9001.pgn",
            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "publicURL": "https://data.example/events/chess-results/tnr9001.pgn",
        }]}), encoding="utf-8")
        static = root / "docs/data/pgn"
        static.mkdir(parents=True)
        lichess = root / "docs/data/bulk/lichess-events"
        (lichess / "pgn").mkdir(parents=True)
        return {
            "registry": registry, "details": details, "event_pgn": event_pgn,
            "receipt": receipt, "static": static, "lichess": lichess,
            "event_facts": root / "data/generated/player-event-facts",
            "game_facts": root / "data/generated/player-game-facts",
        }

    def patches(self, root: pathlib.Path, paths: dict[str, pathlib.Path]):
        return (
            mock.patch.object(bpf, "ROOT", root),
            mock.patch.object(bpf, "REGISTRY", paths["registry"]),
            mock.patch.object(bpf, "EVENT_DETAILS", paths["details"]),
            mock.patch.object(bpf, "EVENT_PGN", paths["event_pgn"]),
            mock.patch.object(bpf, "EVENT_PGN_RECEIPT", paths["receipt"]),
            mock.patch.object(bpf, "STATIC_PGN_ROOT", paths["static"]),
            mock.patch.object(bpf, "LICHESS_EVENT_ROOT", paths["lichess"]),
            mock.patch.object(bpf, "LICHESS_EVENT_MANIFEST", paths["lichess"] / "manifest.json"),
            mock.patch.object(bpf, "BULK_YOUTH_MANIFEST", root / "docs/data/bulk/youth/manifest.json"),
            mock.patch.object(bpf, "MAPPINGS", root / "data/community/tournament-name-mappings.csv"),
            mock.patch.object(bpf, "EVENT_FACT_ROOT", paths["event_facts"]),
            mock.patch.object(bpf, "GAME_FACT_ROOT", paths["game_facts"]),
        )

    def test_cold_and_warm_builds_ignore_previous_by_player_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            paths = self.fixture(root)
            patches = self.patches(root, paths)
            with mock.patch.dict(os.environ, {"SNAPSHOT_ID": "snap-facts"}), \
                 mock.patch.object(snapshot_context, "_cached", None), \
                 patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
                 patches[6], patches[7], patches[8], patches[9], patches[10], patches[11]:
                event_manifest, game_manifest = bpf.build()
                cold = {
                    path.relative_to(root).as_posix(): path.read_bytes()
                    for path in (paths["event_facts"].glob("*.json"))
                }
                cold.update({
                    path.relative_to(root).as_posix(): path.read_bytes()
                    for path in (paths["game_facts"].glob("*.json"))
                })
                poison = root / "docs/data/index/by-player/fide-1001.json"
                poison.parent.mkdir(parents=True)
                poison.write_text('{"games":[{"id":"old-feedback"}]}', encoding="utf-8")
                bpf.build()
                warm = {relative: (root / relative).read_bytes() for relative in cold}

            self.assertEqual(cold, warm)
            self.assertEqual(event_manifest["rows"], 2)
            self.assertEqual(game_manifest["rows"], 1)
            self.assertEqual(game_manifest["totals"]["playerGameLinks"], 2)
            with mock.patch.dict(os.environ, {"SNAPSHOT_ID": "snap-facts"}):
                facts, _ = cpf.load_fact_dataset(paths["game_facts"] / "manifest.json", "player-game-facts")
            self.assertEqual(facts[0]["playerFideIDs"], ["1001", "1002"])

    def test_unverified_event_archive_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            paths = self.fixture(root)
            paths["receipt"].write_text('{"objects":[]}', encoding="utf-8")
            patches = self.patches(root, paths)
            with mock.patch.dict(os.environ, {"SNAPSHOT_ID": "snap-facts"}), \
                 mock.patch.object(snapshot_context, "_cached", None), \
                 patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
                 patches[6], patches[7], patches[8], patches[9], patches[10], patches[11]:
                with self.assertRaisesRegex(RuntimeError, "CANONICAL_EVENT_PGN_RECEIPT_MISMATCH"):
                    bpf.build()

    def test_missing_fact_manifest_has_no_warm_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = pathlib.Path(directory) / "manifest.json"
            with self.assertRaisesRegex(RuntimeError, "REQUIRED_FACT_MANIFEST_MISSING"):
                cpf.load_fact_dataset(missing, "player-game-facts")

    def test_filename_player_hint_cannot_create_third_player_link(self):
        registry = {
            "1001": {"fideID": "1001", "name": "Alpha, One"},
            "1002": {"fideID": "1002", "name": "Beta, Two"},
            "1003": {"fideID": "1003", "name": "Gamma, Three"},
        }
        with self.assertRaisesRegex(RuntimeError, "CANONICAL_STATIC_PGN_PLAYER_HINT_MISMATCH"):
            bpf.add_game(
                {},
                game=PGN,
                asset_path=pathlib.Path("docs/data/pgn/fide-1003.pgn"),
                game_index=0,
                source_kind="canonical-static-pgn",
                source_label="Static PGN",
                registry=registry,
                names={},
                contexts={},
                player_hint="1003",
            )

    def test_player_package_bytes_are_cold_warm_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            docs_data = pathlib.Path(directory) / "docs/data"
            target = docs_data / "pgn/by-player/fide-1001/all.pgn"
            game = bsp.PlayerGame(
                pgn=PGN,
                event="Canonical Event",
                date="2026.08.01",
                white="Alpha, One",
                black="Beta, Two",
                result="1-0",
                source="fixture",
            )
            with mock.patch.object(bsp, "DOCS_DATA", docs_data), \
                 mock.patch.object(bsp, "OUTPUT_PGN_ROOT", docs_data / "pgn/by-player"):
                first = bsp.build_package("1001", "all", "全部 PGN", [game], target, False, {})
                first_bytes = target.read_bytes()
                target.write_text("poison old output", encoding="utf-8")
                second = bsp.build_package("1001", "all", "全部 PGN", [game], target, False, {})
            self.assertEqual(target.read_bytes(), first_bytes)
            self.assertEqual(first["sha256"], second["sha256"])
            self.assertNotIn(b"% Created:", first_bytes)


if __name__ == "__main__":
    unittest.main()
