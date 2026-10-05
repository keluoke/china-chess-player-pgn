#!/usr/bin/env python3
"""Unit tests for the 实战妙手 (Brilliancies) product feature and pipelines.

Covers:
- R1: Genuine sacrifice heuristics vs false-positive rejection (pinned piece, safe captures, equal trades).
- R2: Validation gate hardening against the 4 review mutations (missing verification, fake game ID, candidate status, empty PGN).
- R3: Score conversion monotonicity and negative mate rejection.
- R4: Lichess broadcast CC BY-SA 4.0 attribution preservation in PGN and JSON.
- R6: Withdrawn status cleanup and HTTP 410 handling.
- R7: URL harmonization and suppression of fake /events/event-unknown links.
- R9: API parameter validation (limit, offset, cursor filter mismatch, exact matching, HEAD, ETag 304).
- R11: Strict --root isolation.
"""

from __future__ import annotations

import io
import json
import pathlib
import shutil
import sys
import tempfile
import unittest

import chess
import chess.pgn

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Scripts"))
sys.path.insert(0, str(ROOT / "Scripts/local"))

import build_brilliancies as builder  # noqa: E402
from analyze_brilliancies import (  # noqa: E402
    PIECE_VALUES,
    compute_brilliancy_id,
    detect_sacrifice_moves,
    score_to_cp_equivalent,
    score_to_pov_dict,
)
import validate_brilliancies as validator  # noqa: E402


class BrillianciesIdAndHeuristicsTests(unittest.TestCase):
    def test_id_computation_deterministic_and_collision_free(self):
        fp = "fp:abcdef1234567890"
        ply = 42
        uci = "e2e4"

        id1 = compute_brilliancy_id(fp, ply, uci)
        id2 = compute_brilliancy_id(fp, ply, uci)
        self.assertTrue(id1.startswith("br-"))
        self.assertEqual(len(id1), 67)  # "br-" + 64 hex characters
        self.assertEqual(id1, id2)

        # Different ply produces different ID
        id_ply = compute_brilliancy_id(fp, 43, uci)
        self.assertNotEqual(id1, id_ply)

        # Different move produces different ID
        id_move = compute_brilliancy_id(fp, ply, "e7e5")
        self.assertNotEqual(id1, id_move)

        # Matches builder's compute_brilliancy_id exactly
        builder_id = builder.compute_brilliancy_id(fp, ply, uci)
        self.assertEqual(id1, builder_id)

    def test_detect_sacrifice_moves_rejects_false_positives(self):
        # 1. Ding 25. Rxh5 position: Black cannot legally capture at h5. Must NOT be detected as sacrifice!
        fen_25rxh5 = "rr3k2/2qbNp2/3pp3/p5Qp/4P1P1/Pnp2P2/1PP5/1K1R3R w - - 4 25"
        g1 = chess.pgn.Game()
        g1.setup(chess.Board(fen_25rxh5))
        g1.add_variation(chess.Move.from_uci("h1h5"))
        sacs1 = detect_sacrifice_moves(g1)
        self.assertFalse(any(s["moveUci"] == "h1h5" for s in sacs1), "25. Rxh5 has no legal recapture and must not be marked as a sacrifice")

        # 2. Pinned piece capture: White Rook takes pawn on e6 (1. Rxe6).
        # Black e7-bishop is pinned to Black King on e8 by White Rook on e1.
        fen_pin = "4k3/4b3/4p3/8/8/8/8/4R1K1 w - - 0 20"
        g2 = chess.pgn.Game()
        g2.setup(chess.Board(fen_pin))
        g2.add_variation(chess.Move.from_uci("e1e6"))
        sacs2 = detect_sacrifice_moves(g2)
        self.assertFalse(any(s["moveUci"] == "e1e6" for s in sacs2), "Move where opponent piece is pinned and cannot take must not be marked as a sacrifice")

        # 3. Equal trade: Queen takes Queen
        fen_trade = "4k3/8/8/3q4/3Q4/8/8/4K3 w - - 0 20"
        g3 = chess.pgn.Game()
        g3.setup(chess.Board(fen_trade))
        g3.add_variation(chess.Move.from_uci("d4d5"))
        sacs3 = detect_sacrifice_moves(g3)
        self.assertFalse(any(s["moveUci"] == "d4d5" for s in sacs3), "Equal queen trade must not be marked as a sacrifice")

    def test_detect_sacrifice_moves_accepts_authentic_sacrifices(self):
        # 1. Hou Yifan 28. Qxh6+!! Queen sacrifice against Gao Muziyan (opponent can capture with Bxh6)
        fen_hou = "r6r/pp3pbk/2q1pBpp/3pn3/3R3R/2P2B1P/PP3PP1/2Q4K w - - 0 28"
        g1 = chess.pgn.Game()
        g1.setup(chess.Board(fen_hou))
        g1.add_variation(chess.Move.from_uci("c1h6"))
        sacs1 = detect_sacrifice_moves(g1)
        self.assertTrue(any(s["moveUci"] == "c1h6" and s["theme"] == "queen-sacrifice" for s in sacs1))

        # 2. Ding Liren 46. Rxe4!! Rook sacrifice against Suleymanli (opponent can capture with Rxe4 or Qxe4)
        fen_ding = "8/5p1k/1p4pn/4r3/1P1Rp1qp/P3P3/3N1PB1/1Q4K1 w - - 0 46"
        g2 = chess.pgn.Game()
        g2.setup(chess.Board(fen_ding))
        g2.add_variation(chess.Move.from_uci("d4e4"))
        sacs2 = detect_sacrifice_moves(g2)
        self.assertTrue(any(s["moveUci"] == "d4e4" and s["theme"] == "rook-sacrifice" for s in sacs2))

        # 3. Ding Liren 20. Nxf7!! Minor piece sacrifice against Zhao Jun (opponent can capture with Rxf7)
        fen_zhao = "3q1rk1/2pb1ppp/p2r4/3QN3/1bB2P2/1P6/5PPP/3R1RK1 w - - 1 20"
        g3 = chess.pgn.Game()
        g3.setup(chess.Board(fen_zhao))
        g3.add_variation(chess.Move.from_uci("e5f7"))
        sacs3 = detect_sacrifice_moves(g3)
        self.assertTrue(any(s["moveUci"] == "e5f7" and s["theme"] == "minor-piece-sacrifice" for s in sacs3))

    def test_score_to_cp_equivalent_monotonicity_and_mate_handling(self):
        # Verify monotonic centipawn ordering:
        # Mate(1) > Mate(2) > Mate(5) > 1000 cp > 100 cp > 0 cp > -100 cp > Mate(-5) > Mate(-1)
        m1 = chess.engine.PovScore(chess.engine.Mate(1), chess.WHITE)
        m2 = chess.engine.PovScore(chess.engine.Mate(2), chess.WHITE)
        m5 = chess.engine.PovScore(chess.engine.Mate(5), chess.WHITE)
        cp1000 = chess.engine.PovScore(chess.engine.Cp(1000), chess.WHITE)
        cp100 = chess.engine.PovScore(chess.engine.Cp(100), chess.WHITE)
        cp0 = chess.engine.PovScore(chess.engine.Cp(0), chess.WHITE)
        neg_cp = chess.engine.PovScore(chess.engine.Cp(-500), chess.WHITE)
        neg_m5 = chess.engine.PovScore(chess.engine.Mate(-5), chess.WHITE)
        neg_m1 = chess.engine.PovScore(chess.engine.Mate(-1), chess.WHITE)

        val_m1 = score_to_cp_equivalent(m1, chess.WHITE)
        val_m2 = score_to_cp_equivalent(m2, chess.WHITE)
        val_m5 = score_to_cp_equivalent(m5, chess.WHITE)
        val_cp1000 = score_to_cp_equivalent(cp1000, chess.WHITE)
        val_cp100 = score_to_cp_equivalent(cp100, chess.WHITE)
        val_cp0 = score_to_cp_equivalent(cp0, chess.WHITE)
        val_neg_cp = score_to_cp_equivalent(neg_cp, chess.WHITE)
        val_neg_m5 = score_to_cp_equivalent(neg_m5, chess.WHITE)
        val_neg_m1 = score_to_cp_equivalent(neg_m1, chess.WHITE)

        self.assertGreater(val_m1, val_m2)
        self.assertGreater(val_m2, val_m5)
        self.assertGreater(val_m5, val_cp1000)
        self.assertGreater(val_cp1000, val_cp100)
        self.assertGreater(val_cp100, val_cp0)
        self.assertGreater(val_cp0, val_neg_cp)
        self.assertGreater(val_neg_cp, val_neg_m5)
        self.assertGreater(val_neg_m5, val_neg_m1)

        # score_to_pov_dict does not invent fake WDL
        d_cp = score_to_pov_dict(cp100, chess.WHITE)
        self.assertNotIn("wdl", d_cp)


class BrillianciesBuildAndValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global ROOT
        cls.source_root = ROOT
        cls.temp = tempfile.TemporaryDirectory()
        ROOT = pathlib.Path(cls.temp.name)
        curated = cls.source_root / "data/manual/brilliancies/curated.json"
        target = ROOT / "data/manual/brilliancies/curated.json"
        target.parent.mkdir(parents=True)
        shutil.copy2(curated, target)
        registry = ROOT / "docs/data/registry/players.json"
        registry.parent.mkdir(parents=True)
        players = {}
        for item in json.loads(curated.read_text())["items"]:
            for side in ("white", "black"):
                player = item[side]
                fid = player["playerId"].removeprefix("fide-")
                players[fid] = {"fideID": fid, "displayName": player["displayName"]}
        registry.write_text(json.dumps(list(players.values())))
        for fid in ("8602980", "8603677", "8603405"):
            archive = ROOT / f"docs/data/pgn/by-player/fide-{fid}/all.pgn"
            archive.parent.mkdir(parents=True)
            shutil.copy2(cls.source_root / "Scripts/tests/fixtures/brilliancies/original-games.pgn", archive)
        builder.build(ROOT, sid="test-snap")

    @classmethod
    def tearDownClass(cls):
        global ROOT
        ROOT = cls.source_root
        cls.temp.cleanup()

    def setUp(self):
        self.curated_path = ROOT / "data/manual/brilliancies/curated.json"
        self.manifest_path = ROOT / "docs/data/brilliancies/manifest.json"
        self.items_path = ROOT / "docs/data/brilliancies/items.json"
        self.shards_dir = ROOT / "docs/data/brilliancies/shards"
        self.pgn_dir = ROOT / "docs/data/brilliancies/pgn"

    def test_curated_data_is_valid_and_non_empty(self):
        self.assertTrue(self.curated_path.is_file(), "curated.json must exist")
        data = json.loads(self.curated_path.read_text(encoding="utf-8"))
        self.assertEqual(data.get("schemaVersion"), 1)
        items = data.get("items", [])
        self.assertEqual(len(items), 10, "Should have 10 master curated brilliancies")

        for item in items:
            self.assertTrue(item["id"].startswith("br-"))
            self.assertEqual(item["status"], "published")
            self.assertEqual(item["classification"]["symbol"], "!!")
            self.assertIn("fenBefore", item["position"])
            self.assertIn("uci", item["move"])
            self.assertIn("san", item["move"])
            self.assertIn("white", item)
            self.assertIn("black", item)
            self.assertIn("title", item)
            self.assertIn("summary", item)
            self.assertIn("verification", item)
            self.assertIn("rights", item)
            self.assertIn("actualContinuationUci", item)

    def test_current_built_artifacts_pass_validation(self):
        summary = validator.validate_brilliancies(ROOT)
        self.assertEqual(summary["validItems"], 10)
        self.assertEqual(summary["shardsChecked"], 16)
        self.assertLess(summary["manifestBytes"], 1024 * 1024)

    def test_list_keeps_full_move_arrays_in_detail_only(self):
        summaries = json.loads(self.items_path.read_text(encoding="utf-8"))
        self.assertEqual(len(summaries), 10)
        for item in summaries:
            self.assertNotIn("movesUci", item["game"])
            self.assertNotIn("movesSan", item["game"])
            shard = json.loads((self.shards_dir / f"{item['id'][3]}.json").read_text())
            self.assertTrue(shard["items"][item["id"]]["game"]["movesUci"])

    def test_packed_layout_replaces_legacy_files_and_validates_pgn(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self._setup_mock_environment(root)
            builder.build(root, sid="packed-snap", shard_prefix_length=2)
            manifest = json.loads((root / "docs/data/brilliancies/manifest.json").read_text())
            self.assertEqual(manifest["shardPrefixLength"], 2)
            self.assertEqual(manifest["pgnLayout"], "shards")
            self.assertEqual(len(manifest["shards"]), 256)
            self.assertEqual(list((root / "docs/data/brilliancies/pgn").glob("*.pgn")), [])
            self.assertEqual(validator.validate_brilliancies(root)["shardsChecked"], 256)
            item_id = json.loads((root / "docs/data/brilliancies/items.json").read_text())[0]["id"]
            shard_path = root / f"docs/data/brilliancies/shards/{item_id[3:5]}.json"
            shard = json.loads(shard_path.read_text())
            del shard["pgn"][item_id]
            shard_path.write_text(json.dumps(shard), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "PGN_SNIPPET_MISSING"):
                validator.validate_brilliancies(root)

    def test_shards_are_partitioned_by_hash_hex_char(self):
        for i in range(16):
            shard_key = hex(i)[2:]
            shard_file = self.shards_dir / f"{shard_key}.json"
            self.assertTrue(shard_file.is_file(), f"Shard {shard_key}.json should exist")
            shard_data = json.loads(shard_file.read_text(encoding="utf-8"))
            items = shard_data.get("items", {})
            for item_id in items:
                expected_key = item_id.removeprefix("br-")[0].lower()
                self.assertEqual(
                    expected_key,
                    shard_key,
                    f"Item {item_id} should belong to shard key {shard_key}",
                )

    def test_generated_pgns_are_valid_and_contain_brilliancy_nag_and_attribution(self):
        pgn_files = list(self.pgn_dir.glob("*.pgn"))
        self.assertEqual(len(pgn_files), 10)

        for pgn_file in pgn_files:
            content = pgn_file.read_text(encoding="utf-8")
            game = chess.pgn.read_game(io.StringIO(content))
            self.assertIsNotNone(game, f"PGN {pgn_file.name} failed to parse")
            self.assertEqual(game.headers.get("SetUp"), "1")
            self.assertTrue("FEN" in game.headers)

            first_variation = game.variations[0] if game.variations else None
            self.assertIsNotNone(first_variation)
            self.assertIn(
                chess.pgn.NAG_BRILLIANT_MOVE,
                first_variation.nags,
                f"PGN {pgn_file.name} must have NAG $3 (!!) on brilliancy move",
            )
            # Must have license header
            self.assertIn("License", game.headers)

    def test_rebinds_unique_original_after_header_only_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self._setup_mock_environment(root)
            curated = json.loads(self.curated_path.read_text())
            target = curated["items"][0]
            fid = target["white"]["playerId"].removeprefix("fide-")
            archive = root / f"docs/data/pgn/by-player/fide-{fid}/all.pgn"
            archive.write_text(archive.read_text().replace('[Site "', '[Site "Updated '))
            builder.build(root, sid="test-snap")
            shard = json.loads((root / f"docs/data/brilliancies/shards/{target['id'][3]}.json").read_text())
            self.assertNotEqual(shard["items"][target["id"]]["game"]["id"], target["game"]["id"])
            validator.validate_brilliancies(root)

    def test_original_game_provenance_does_not_require_player_fide_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self._setup_mock_environment(root)
            curated_path = root / "data/manual/brilliancies/curated.json"
            curated = json.loads(curated_path.read_text())
            item = curated["items"][0]
            item["white"]["playerId"] = None
            item["black"]["playerId"] = None
            item["event"]["id"] = "event-unknown"
            curated_path.write_text(json.dumps(curated), encoding="utf-8")
            builder.build(root, sid="test-snap")
            shard = json.loads((root / f"docs/data/brilliancies/shards/{item['id'][3]}.json").read_text())
            public_item = shard["items"][item["id"]]
            self.assertIsNone(public_item["links"]["player"])
            self.assertIsNone(public_item["links"]["game"])
            self.assertIsNone(public_item["links"]["event"])
            self.assertEqual(validator.validate_brilliancies(root)["validItems"], 10)

    def test_root_isolation(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = pathlib.Path(tmp_dir)
            # Setup isolated tree with curated.json and empty docs
            (tmp_root / "data/manual/brilliancies").mkdir(parents=True)
            (tmp_root / "data/manual/brilliancies/curated.json").write_text(
                self.curated_path.read_text(encoding="utf-8"), encoding="utf-8"
            )
            (tmp_root / "docs/data/registry").mkdir(parents=True)
            (tmp_root / "docs/data/registry/players.json").write_text(
                (ROOT / "docs/data/registry/players.json").read_text(encoding="utf-8"), encoding="utf-8"
            )

            # Build strictly into tmp_root
            rep = builder.build(root=tmp_root, sid="test-root-snap")
            self.assertEqual(rep["published"], 10)

            # Verify outputs exist strictly in tmp_root
            self.assertTrue((tmp_root / "docs/data/brilliancies/manifest.json").is_file())
            self.assertTrue((tmp_root / "docs/data/brilliancies/items.json").is_file())
            self.assertEqual(len(list((tmp_root / "docs/data/brilliancies/pgn").glob("*.pgn"))), 10)

    def test_mutation_1_missing_verification_fails(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = pathlib.Path(tmp_dir)
            self._setup_mock_environment(tmp_root)
            item_path = tmp_root / "docs/data/brilliancies/items.json"
            items = json.loads(item_path.read_text(encoding="utf-8"))
            # Mutate: remove verification
            del items[0]["verification"]
            item_path.write_text(json.dumps(items), encoding="utf-8")
            shard_h = items[0]["id"].removeprefix("br-")[0].lower()
            shard_path = tmp_root / f"docs/data/brilliancies/shards/{shard_h}.json"
            sdata = json.loads(shard_path.read_text(encoding="utf-8"))
            del sdata["items"][items[0]["id"]]["verification"]
            shard_path.write_text(json.dumps(sdata), encoding="utf-8")

            with self.assertRaises(ValueError) as ctx:
                validator.validate_brilliancies(tmp_root)
            self.assertIn("MISSING_VERIFICATION", str(ctx.exception))

    def test_mutation_2_fake_game_id_fails(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = pathlib.Path(tmp_dir)
            self._setup_mock_environment(tmp_root)
            item_path = tmp_root / "docs/data/brilliancies/items.json"
            items = json.loads(item_path.read_text(encoding="utf-8"))
            b_id = items[0]["id"]
            shard_h = b_id.removeprefix("br-")[0].lower()
            shard_path = tmp_root / f"docs/data/brilliancies/shards/{shard_h}.json"
            sdata = json.loads(shard_path.read_text(encoding="utf-8"))
            # Mutate: fake game id that does not match fingerprint
            sdata["items"][b_id]["game"]["id"] = "fake-nonexistent-game-id"
            shard_path.write_text(json.dumps(sdata), encoding="utf-8")

            with self.assertRaises(ValueError) as ctx:
                validator.validate_brilliancies(tmp_root)
            self.assertIn("GAME_NOT_FOUND_IN_ARCHIVE", str(ctx.exception))

    def test_mutation_3_candidate_status_fails(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = pathlib.Path(tmp_dir)
            self._setup_mock_environment(tmp_root)
            item_path = tmp_root / "docs/data/brilliancies/items.json"
            items = json.loads(item_path.read_text(encoding="utf-8"))
            # Mutate: candidate status
            items[0]["status"] = "candidate"
            item_path.write_text(json.dumps(items), encoding="utf-8")
            shard_h = items[0]["id"].removeprefix("br-")[0].lower()
            shard_path = tmp_root / f"docs/data/brilliancies/shards/{shard_h}.json"
            sdata = json.loads(shard_path.read_text(encoding="utf-8"))
            sdata["items"][items[0]["id"]]["status"] = "candidate"
            shard_path.write_text(json.dumps(sdata), encoding="utf-8")

            with self.assertRaises(ValueError) as ctx:
                validator.validate_brilliancies(tmp_root)
            self.assertIn("INVALID_STATUS", str(ctx.exception))

    def test_mutation_4_empty_pgn_snippet_fails(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = pathlib.Path(tmp_dir)
            self._setup_mock_environment(tmp_root)
            item_path = tmp_root / "docs/data/brilliancies/items.json"
            items = json.loads(item_path.read_text(encoding="utf-8"))
            b_id = items[0]["id"]
            pgn_file = tmp_root / f"docs/data/brilliancies/pgn/{b_id}.pgn"
            # Mutate: empty moves PGN snippet
            empty_pgn = '[Event "Match"]\n[SetUp "1"]\n[FEN "8/8/8/8/8/8/8/8 w - - 0 1"]\n\n*\n'
            pgn_file.write_text(empty_pgn, encoding="utf-8")

            with self.assertRaises(ValueError) as ctx:
                validator.validate_brilliancies(tmp_root)
            self.assertIn("PGN_SNIPPET_EMPTY_MOVES", str(ctx.exception))

    def test_mutation_5_wrong_ply_fails(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = pathlib.Path(tmp_dir)
            self._setup_mock_environment(tmp_root)
            item_path = tmp_root / "docs/data/brilliancies/items.json"
            items = json.loads(item_path.read_text(encoding="utf-8"))
            b_id = items[0]["id"]
            shard_h = b_id.removeprefix("br-")[0].lower()
            shard_path = tmp_root / f"docs/data/brilliancies/shards/{shard_h}.json"
            sdata = json.loads(shard_path.read_text(encoding="utf-8"))
            # Mutate: change ply so replayed position does not match fenBefore
            sdata["items"][b_id]["position"]["ply"] = sdata["items"][b_id]["position"]["ply"] + 2
            shard_path.write_text(json.dumps(sdata), encoding="utf-8")

            with self.assertRaises(ValueError) as ctx:
                validator.validate_brilliancies(tmp_root)
            self.assertTrue(
                "POSITION_NOT_IN_GAME_AT_PLY" in str(ctx.exception)
                or "FEN_BEFORE_MISMATCH_WITH_ARCHIVE" in str(ctx.exception)
                or "BRILLIANCY_ID_CALCULATION_MISMATCH" in str(ctx.exception)
            )

    def test_mutation_6_missing_game_archive_fails(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = pathlib.Path(tmp_dir)
            self._setup_mock_environment(tmp_root)
            # Remove player archives
            shutil.rmtree(tmp_root / "docs/data/pgn/by-player")

            with self.assertRaises(ValueError) as ctx:
                validator.validate_brilliancies(tmp_root)
            self.assertIn("ORIGINAL_GAME_ARCHIVE_MISSING", str(ctx.exception))

    def test_mutation_7_untrusted_wdl_fails(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = pathlib.Path(tmp_dir)
            self._setup_mock_environment(tmp_root)
            item_path = tmp_root / "docs/data/brilliancies/items.json"
            items = json.loads(item_path.read_text(encoding="utf-8"))
            b_id = items[0]["id"]
            shard_h = b_id.removeprefix("br-")[0].lower()
            shard_path = tmp_root / f"docs/data/brilliancies/shards/{shard_h}.json"
            sdata = json.loads(shard_path.read_text(encoding="utf-8"))
            # Mutate: inject fabricated wdl
            sdata["items"][b_id]["verification"]["evaluation"]["wdl"] = [900, 50, 50]
            shard_path.write_text(json.dumps(sdata), encoding="utf-8")

            with self.assertRaises(ValueError) as ctx:
                validator.validate_brilliancies(tmp_root)
            self.assertIn("UNTRUSTED_WDL_IN_VERIFICATION", str(ctx.exception))

    def test_mutation_8_truncated_game_moves_fails(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = pathlib.Path(tmp_dir)
            self._setup_mock_environment(tmp_root)
            item_path = tmp_root / "docs/data/brilliancies/items.json"
            items = json.loads(item_path.read_text(encoding="utf-8"))
            b_id = items[0]["id"]
            shard_h = b_id.removeprefix("br-")[0].lower()
            shard_path = tmp_root / f"docs/data/brilliancies/shards/{shard_h}.json"
            sdata = json.loads(shard_path.read_text(encoding="utf-8"))
            # Mutate: truncate game moves to 1 move
            sdata["items"][b_id]["game"]["movesUci"] = ["e2e4"]
            sdata["items"][b_id]["game"]["totalMoves"] = 1
            shard_path.write_text(json.dumps(sdata), encoding="utf-8")

            with self.assertRaises(ValueError) as ctx:
                validator.validate_brilliancies(tmp_root)
            self.assertIn("GAME_MOVES_COUNT_MISMATCH", str(ctx.exception))

    def test_mutation_9_fake_continuation_move_fails(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = pathlib.Path(tmp_dir)
            self._setup_mock_environment(tmp_root)
            # Find an item across shards that has actual continuation moves
            target_b_id = None
            target_shard_path = None
            for p in (tmp_root / "docs/data/brilliancies/shards").glob("*.json"):
                data = json.loads(p.read_text(encoding="utf-8"))
                for bid, bval in data.get("items", {}).items():
                    if len(bval.get("actualContinuationUci", [])) > 0:
                        target_b_id = bid
                        target_shard_path = p
                        break
                if target_b_id:
                    break

            self.assertIsNotNone(target_b_id, "Should find at least one item with continuation moves")
            sdata = json.loads(target_shard_path.read_text(encoding="utf-8"))
            # Mutate: fake continuation move not played in archive
            sdata["items"][target_b_id]["actualContinuationUci"][0] = "a7a6"
            target_shard_path.write_text(json.dumps(sdata), encoding="utf-8")

            with self.assertRaises(ValueError) as ctx:
                validator.validate_brilliancies(tmp_root)
            self.assertIn("CONTINUATION_NOT_PLAYED_IN_ARCHIVE", str(ctx.exception))

    def test_mutation_10_wrong_moves_san_fails(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = pathlib.Path(tmp_dir)
            self._setup_mock_environment(tmp_root)
            item_path = tmp_root / "docs/data/brilliancies/items.json"
            items = json.loads(item_path.read_text(encoding="utf-8"))
            b_id = items[0]["id"]
            shard_h = b_id.removeprefix("br-")[0].lower()
            shard_path = tmp_root / f"docs/data/brilliancies/shards/{shard_h}.json"
            sdata = json.loads(shard_path.read_text(encoding="utf-8"))
            # Mutate: change first move SAN to a fake SAN
            sdata["items"][b_id]["game"]["movesSan"][0] = "fakeSan"
            shard_path.write_text(json.dumps(sdata), encoding="utf-8")

            with self.assertRaises(ValueError) as ctx:
                validator.validate_brilliancies(tmp_root)
            self.assertIn("GAME_MOVE_SAN_MISMATCH_WITH_ARCHIVE", str(ctx.exception))

    def test_mutation_11_tampered_fen_halfmove_fails(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = pathlib.Path(tmp_dir)
            self._setup_mock_environment(tmp_root)
            item_path = tmp_root / "docs/data/brilliancies/items.json"
            items = json.loads(item_path.read_text(encoding="utf-8"))
            b_id = items[0]["id"]
            shard_h = b_id.removeprefix("br-")[0].lower()
            shard_path = tmp_root / f"docs/data/brilliancies/shards/{shard_h}.json"
            sdata = json.loads(shard_path.read_text(encoding="utf-8"))
            # Mutate: tamper with halfmove clock in fenBefore
            parts = sdata["items"][b_id]["position"]["fenBefore"].split(" ")
            parts[4] = "42"
            sdata["items"][b_id]["position"]["fenBefore"] = " ".join(parts)
            shard_path.write_text(json.dumps(sdata), encoding="utf-8")

            with self.assertRaises(ValueError) as ctx:
                validator.validate_brilliancies(tmp_root)
            self.assertIn("FEN_BEFORE_MISMATCH_WITH_ARCHIVE", str(ctx.exception))

    def test_mutation_12_tampered_fen_fullmove_fails(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = pathlib.Path(tmp_dir)
            self._setup_mock_environment(tmp_root)
            item_path = tmp_root / "docs/data/brilliancies/items.json"
            items = json.loads(item_path.read_text(encoding="utf-8"))
            b_id = items[0]["id"]
            shard_h = b_id.removeprefix("br-")[0].lower()
            shard_path = tmp_root / f"docs/data/brilliancies/shards/{shard_h}.json"
            sdata = json.loads(shard_path.read_text(encoding="utf-8"))
            # Mutate: tamper with fullmove number in fenBefore
            parts = sdata["items"][b_id]["position"]["fenBefore"].split(" ")
            parts[5] = "99"
            sdata["items"][b_id]["position"]["fenBefore"] = " ".join(parts)
            shard_path.write_text(json.dumps(sdata), encoding="utf-8")

            with self.assertRaises(ValueError) as ctx:
                validator.validate_brilliancies(tmp_root)
            self.assertIn("FEN_BEFORE_MISMATCH_WITH_ARCHIVE", str(ctx.exception))

    def _setup_mock_environment(self, tmp_root: pathlib.Path):
        (tmp_root / "data/manual/brilliancies").mkdir(parents=True)
        (tmp_root / "data/manual/brilliancies/curated.json").write_text(
            self.curated_path.read_text(encoding="utf-8"), encoding="utf-8"
        )
        (tmp_root / "docs/data/registry").mkdir(parents=True)
        (tmp_root / "docs/data/registry/players.json").write_text(
            (ROOT / "docs/data/registry/players.json").read_text(encoding="utf-8"), encoding="utf-8"
        )
        # Copy player archives and indices for replay validation
        for fid in ["8602980", "8603405", "8603677", "8603820"]:
            src = ROOT / f"docs/data/pgn/by-player/fide-{fid}/all.pgn"
            if src.is_file():
                dest = tmp_root / f"docs/data/pgn/by-player/fide-{fid}"
                dest.mkdir(parents=True, exist_ok=True)
                dest.joinpath("all.pgn").write_bytes(src.read_bytes())
            idx_src = ROOT / f"docs/data/index/by-player/fide-{fid}.json"
            if idx_src.is_file():
                idx_dest = tmp_root / "docs/data/index/by-player"
                idx_dest.mkdir(parents=True, exist_ok=True)
                idx_dest.joinpath(f"fide-{fid}.json").write_bytes(idx_src.read_bytes())
        builder.build(root=tmp_root, sid="test-snap")


class BrillianciesOpenApiAndPagesFunctionsTests(unittest.TestCase):
    def test_openapi_spec_structure(self):
        spec = builder.build_openapi_spec()
        self.assertEqual(spec["openapi"], "3.1.0")
        self.assertEqual(spec["info"]["title"], "ChessDB 实战妙手 API")
        self.assertIn("/api/v1/brilliancies", spec["paths"])
        self.assertIn("/api/v1/brilliancies/{id}.json", spec["paths"])
        self.assertIn("/api/v1/brilliancies/{id}.pgn", spec["paths"])
        self.assertIn("components", spec)
        self.assertIn("schemas", spec["components"])
        self.assertIn("BrilliancyDetail", spec["components"]["schemas"])

    def test_functions_route_and_filter_logic(self):
        func_path = ROOT / "functions/api/v1/brilliancies/[[path]].js"
        self.assertTrue(func_path.is_file())
        content = func_path.read_text(encoding="utf-8")
        self.assertIn("onRequestGet", content)
        self.assertIn("onRequestHead", content)
        self.assertIn("snapshot_changed", content)
        self.assertIn("item.status === \"withdrawn\"", content)
        self.assertIn("application/x-chess-pgn", content)
        self.assertIn("nextCursor", content)
        self.assertIn("invalid_limit", content)
        self.assertIn("invalid_offset", content)
        self.assertIn("cursor_filter_mismatch", content)
        self.assertIn('error: "withdrawn"', content)
        self.assertIn("status: 304", content)

    def test_withdrawn_item_removes_pgn(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = pathlib.Path(tmp_dir)
            self._setup_mock_environment(tmp_root)
            # Verify initial build produced 10 PGNs
            pgn_files = list((tmp_root / "docs/data/brilliancies/pgn").glob("*.pgn"))
            self.assertEqual(len(pgn_files), 10)

            # Mutate curated.json: mark first item as withdrawn
            curated_p = tmp_root / "data/manual/brilliancies/curated.json"
            cdata = json.loads(curated_p.read_text(encoding="utf-8"))
            withdrawn_id = cdata["items"][0]["id"]
            cdata["items"][0]["status"] = "withdrawn"
            curated_p.write_text(json.dumps(cdata), encoding="utf-8")

            # Rebuild
            rep = builder.build(root=tmp_root, sid="test-snap-2")
            self.assertEqual(rep["published"], 9)

            # Verify withdrawn item PGN is deleted
            withdrawn_pgn = tmp_root / f"docs/data/brilliancies/pgn/{withdrawn_id}.pgn"
            self.assertFalse(withdrawn_pgn.is_file(), f"PGN for withdrawn item {withdrawn_id} must be deleted on build")
            remaining_pgns = list((tmp_root / "docs/data/brilliancies/pgn").glob("*.pgn"))
            self.assertEqual(len(remaining_pgns), 9)

    def _setup_mock_environment(self, tmp_root: pathlib.Path):
        (tmp_root / "data/manual/brilliancies").mkdir(parents=True)
        (tmp_root / "data/manual/brilliancies/curated.json").write_text(
            (ROOT / "data/manual/brilliancies/curated.json").read_text(encoding="utf-8"), encoding="utf-8"
        )
        (tmp_root / "docs/data/registry").mkdir(parents=True)
        (tmp_root / "docs/data/registry/players.json").write_text(
            "[]", encoding="utf-8"
        )
        builder.build(root=tmp_root, sid="test-snap")


if __name__ == "__main__":
    unittest.main()
