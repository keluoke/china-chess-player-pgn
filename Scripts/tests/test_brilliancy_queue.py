"""Durability, incremental discovery, budgets and fail-closed queue regressions."""
import io
import base64
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'Scripts'))
import brilliancy_queue as q


class FakeEngine:
    id = {'name': 'Stockfish test'}
    options = {}


class Missing(Exception):
    response = {'Error': {'Code': 'NoSuchKey'}}


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.puts = 0
        self.lists = 0
        self.corrupt = False

    def get_paginator(self, name):
        return self

    def paginate(self, **kwargs):
        yield {'Contents': [{'Key': key, 'Size': len(value[0])}
                            for key, value in self.objects.items()]}

    def list_objects_v2(self, **kwargs):
        self.lists += 1
        return {'Contents': [{'Key': key, 'Size': len(value[0])}
                             for key, value in self.objects.items()]}

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise Missing()
        data, etag = self.objects[Key]
        return {'Body': io.BytesIO(b'corrupt' if self.corrupt else data), 'ETag': etag}

    def put_object(self, Bucket, Key, Body, **kwargs):
        existing = self.objects.get(Key)
        if kwargs.get('IfNoneMatch') == '*' and existing:
            raise ValueError('PRECONDITION_FAILED')
        if 'IfMatch' in kwargs and (not existing or existing[1] != kwargs['IfMatch']):
            raise ValueError('PRECONDITION_FAILED')
        self.puts += 1
        tag = str(self.puts)
        self.objects[Key] = (Body, tag)
        return {'ETag': tag}


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.execute('CREATE TABLE games (id TEXT PRIMARY KEY, pgn TEXT, priority INTEGER NOT NULL, path TEXT, offset INTEGER)')
        self.add('a')
        self.state = q.new_state(0)
        self.saved = []

    def tearDown(self):
        self.db.close()

    def add(self, key):
        self.db.execute('INSERT INTO games VALUES (?,?,0,NULL,NULL)', (key * 64, '[Result "*"]\n\n1. e4 e5 *'))

    def save(self, state):
        self.saved.append(json.loads(json.dumps(state)))

    def run_queue(self, **kwargs):
        return q.run_queue(self.db, self.state, kwargs.pop('version', 'v1'), FakeEngine(), self.save,
                           seconds=10, max_games=100, checkpoint_games=1, **kwargs)

    @patch.object(q.analyzer, 'analyze_game', return_value=[])
    def test_zero_candidates_completed_incremental_only_and_version_rescan(self, analyze):
        result = self.run_queue()
        self.assertEqual(result['completedGames'], 1)
        self.assertEqual(result['candidatePositions'], 0)
        self.run_queue()
        self.assertEqual(analyze.call_count, 1)
        self.add('b')
        result = self.run_queue()
        self.assertEqual(analyze.call_count, 2)
        self.assertEqual(result['completedGames'], 2)
        self.run_queue(version='v2')
        self.assertEqual(analyze.call_count, 4)

    @patch.object(q.analyzer, 'analyze_game', side_effect=TimeoutError)
    def test_interrupted_game_not_completed(self, analyze):
        result = self.run_queue()
        self.assertEqual(result['pendingGames'], 1)
        self.assertEqual(self.state['records'], {})

    @patch.object(q.analyzer, 'analyze_game', side_effect=ValueError('bad game'))
    def test_failure_retries_with_backoff_not_false_zero(self, analyze):
        result = self.run_queue()
        self.assertEqual(result['retryGames'], 1)
        self.assertEqual(result['completedGames'], 0)
        self.run_queue()
        self.assertEqual(analyze.call_count, 1)
        self.state['records']['a' * 64]['nextRetryAt'] = 0
        self.run_queue()
        self.assertEqual(analyze.call_count, 2)
        self.assertEqual(self.state['records']['a' * 64]['attempts'], 2)

    def test_engine_failure_checkpoints_completed_games_and_propagates(self):
        self.add('b')
        with patch.object(q.analyzer, 'analyze_game', side_effect=[[], q.chess.engine.EngineError('dead')]):
            with self.assertRaises(q.chess.engine.EngineError):
                self.run_queue()
        self.assertEqual(len(self.saved[-1]['records']), 1)
        self.assertNotIn('b' * 64, self.state['records'])

    @patch.object(q.analyzer, 'analyze_game', return_value=[{'status': 'candidate'}])
    def test_candidates_persist_and_absent_games_not_counted(self, analyze):
        self.run_queue()
        restored = q.unpacked(q.packed(self.state))
        self.assertEqual(restored, self.state)
        self.assertEqual(q.summary(restored, self.db, 'v1')['candidatePositions'], 1)
        self.db.execute('DELETE FROM games')
        self.assertEqual(q.summary(restored, self.db, 'v1')['candidatePositions'], 0)

    def test_duplicate_provider_copies_once_across_fixed_shards(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'a.pgn'
            p.write_text('[White "A"]\n[Black "B"]\n[Site "One"]\n[Result "*"]\n\n1. e4 e5 *\n\n'
                         '[White "A"]\n[Black "B"]\n[Site "Two"]\n[Result "*"]\n\n1. e4 e5 *\n')
            count = 0
            for shard in range(q.SHARDS):
                with sqlite3.connect(':memory:') as db:
                    counts = q.catalog([p], db, shard)
                    count += counts['eligibleGames']
                    self.assertEqual(counts['archiveOccurrences'], 2)
            self.assertEqual(count, 1)

    def test_new_games_prioritized_before_history(self):
        self.add('b')
        self.db.execute("UPDATE games SET priority=1 WHERE id=?", ('a' * 64,))
        with patch.object(q.analyzer, 'analyze_game', return_value=[]):
            q.run_queue(self.db, self.state, 'v1', FakeEngine(), self.save,
                        seconds=10, max_games=1)
        self.assertIn('b' * 64, self.state['records'])
        self.assertNotIn('a' * 64, self.state['records'])

    def test_custom_fen_ply_is_relative_but_opening_filter_uses_move_number(self):
        game = q.chess.pgn.Game()
        game.setup(q.chess.Board('r6r/pp3pbk/2q1pBpp/3pn3/3R3R/2P2B1P/PP3PP1/2Q4K w - - 0 28'))
        game.add_variation(q.chess.Move.from_uci('c1h6'))
        candidate = q.analyzer.detect_sacrifice_moves(game)[0]
        self.assertEqual(candidate['ply'], 0)
        self.assertEqual(candidate['fullmoveNumber'], 28)

    def test_unchanged_packages_reuse_offsets_and_changed_package_invalidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'all.pgn'
            path.write_text('[White "A"]\n[Black "B"]\n[Result "*"]\n\n1. e4 e5 *\n')
            cache = {}
            with sqlite3.connect(':memory:') as db:
                first = q.catalog([path], db, 0, cache=cache)
                self.assertEqual(first['parsedPackages'], 1)
            with sqlite3.connect(':memory:') as db:
                with patch.object(q.chess.pgn, 'read_game', side_effect=AssertionError('must not parse unchanged file')):
                    second = q.catalog([path], db, 0, cache=cache)
                self.assertEqual(second['reusedPackages'], 1)
                self.assertEqual(second['eligibleGames'], first['eligibleGames'])
            path.write_text(path.read_text().replace('e4 e5', 'd4 d5'))
            with sqlite3.connect(':memory:') as db:
                third = q.catalog([path], db, 0, cache=cache)
                self.assertEqual(third['parsedPackages'], 1)

    def test_offset_queue_reads_original_game(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'all.pgn'
            path.write_text('[White "A"]\n[Black "B"]\n[Result "*"]\n\n1. e4 e5 *\n')
            seen = 0
            for shard in range(q.SHARDS):
                with sqlite3.connect(':memory:') as db:
                    q.catalog([path], db, shard)
                    def check(game, *args):
                        self.assertEqual(game.headers['White'], 'A')
                        return []
                    with patch.object(q.analyzer, 'analyze_game', side_effect=check):
                        result = q.run_queue(db, q.new_state(shard), 'v1', FakeEngine(), lambda state: None,
                                             seconds=10, max_games=100)
                        seen += result['completedGames']
            self.assertEqual(seen, 1)

    def test_arrival_priority_survives_checkpoint_and_next_slice(self):
        self.state['knownGames'] = ['a' * 64]
        self.add('b')
        self.assertTrue(q.prepare_catalog_state(self.state, self.db, 'v1'))
        self.assertEqual(self.state['priorityGames'], ['b' * 64])
        # A fresh catalog sees both games as already known, but the durable
        # priority marker must still place the unfinished arrival first.
        restored = q.unpacked(q.packed(self.state))
        self.db.execute('UPDATE games SET priority=1')
        q.prepare_catalog_state(restored, self.db, 'v1')
        self.assertEqual(self.db.execute('SELECT id FROM games ORDER BY priority,id LIMIT 1').fetchone()[0], 'b' * 64)
        restored['records']['b' * 64] = {'status': 'complete', 'version': 'v1', 'candidates': []}
        q.prepare_catalog_state(restored, self.db, 'v1')
        self.assertEqual(restored['priorityGames'], [])

    def test_profile_tracks_engine_and_detector(self):
        first = q.profile(FakeEngine(), 200000, 1500000)
        self.assertNotEqual(first, q.profile(FakeEngine(), 200001, 1500000))
        with patch.object(q.chess, '__version__', 'new'):
            self.assertNotEqual(first, q.profile(FakeEngine(), 200000, 1500000))

    def test_frozen_history_and_incremental_catalogs_are_disjoint(self):
        self.add('b')
        baseline = ['a' * 64]
        with sqlite3.connect(':memory:') as other:
            other.execute('CREATE TABLE games (id TEXT PRIMARY KEY, pgn TEXT, priority INTEGER NOT NULL, path TEXT, offset INTEGER)')
            other.executemany('INSERT INTO games VALUES (?,NULL,1,NULL,NULL)',
                              ((key * 64,) for key in ('a', 'b')))
            self.assertEqual(q.restrict_catalog(self.db, baseline, 'history'), 1)
            self.assertEqual(q.restrict_catalog(other, baseline, 'incremental'), 1)
            self.assertEqual(self.db.execute('SELECT id FROM games').fetchone()[0], 'a' * 64)
            self.assertEqual(other.execute('SELECT id FROM games').fetchone()[0], 'b' * 64)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.s3 = FakeS3()
        self.store = q.R2Store(self.s3, 'chess-data', 0, base64.b64encode(b'x' * 32))

    def test_checkpoint_roundtrip_and_stale_writer_refused(self):
        state = self.store.load()
        other = q.R2Store(self.s3, 'chess-data', 0, base64.b64encode(b'x' * 32))
        other.load()
        self.store.save(state)
        with self.assertRaisesRegex(ValueError, 'PRECONDITION_FAILED'):
            other.save(state)
        self.assertEqual(other.load(), state)
        self.store.save(state)
        with self.assertRaisesRegex(ValueError, 'PRECONDITION_FAILED'):
            other.save(state)

    def test_local_history_progress_survives_failed_r2_put_and_restart(self):
        self.store.save(dict(q.new_state(0), baselineGames=['a' * 64]))
        with tempfile.TemporaryDirectory() as tmp:
            local = q.LocalHistoryCheckpoint(self.store, tmp)
            state = local.load()
            state['records']['a' * 64] = {
                'status': 'complete', 'version': 'v1', 'candidates': []}
            local.save(state)
            self.assertNotIn(b'"records"', local.path.read_bytes())
            with patch.object(self.s3, 'put_object', side_effect=ConnectionError):
                with self.assertRaises(ConnectionError):
                    local.sync(state)
            restarted = q.LocalHistoryCheckpoint(
                q.R2Store(self.s3, 'chess-data', 0, base64.b64encode(b'x' * 32)), tmp)
            self.assertEqual(restarted.load(), state)
            restarted.sync(state)
            self.assertFalse(restarted.path.exists())
            self.assertEqual(self.store.load(), state)

    def test_local_history_never_overwrites_changed_remote_checkpoint(self):
        self.store.save(q.new_state(0))
        with tempfile.TemporaryDirectory() as tmp:
            local = q.LocalHistoryCheckpoint(self.store, tmp)
            state = local.load()
            state['marker'] = 'locally completed'
            local.save(state)
            other = q.R2Store(self.s3, 'chess-data', 0, base64.b64encode(b'x' * 32))
            other.load()
            other.save(dict(q.new_state(0), marker='other writer'))
            restarted = q.LocalHistoryCheckpoint(
                q.R2Store(self.s3, 'chess-data', 0, base64.b64encode(b'x' * 32)), tmp)
            with self.assertRaisesRegex(ValueError, 'REMOTE_CONFLICT'):
                restarted.load()

    def test_history_and_incremental_checkpoints_have_separate_keys(self):
        incremental = q.R2Store(self.s3, 'chess-data', 0, base64.b64encode(b'x' * 32), 'incremental')
        self.assertNotEqual(self.store.key, incremental.key)
        self.store.save(dict(q.new_state(0), baselineGames=['a' * 64]))
        incremental.save(dict(q.new_state(0), baselineGames=['a' * 64]))
        self.assertEqual(len(self.s3.objects), 2)

    def test_repeated_checkpoints_reuse_recent_inventory_and_account_own_bytes(self):
        self.store.save(q.new_state(0))
        first_lists = self.s3.lists
        first_bytes = self.store.total_bytes
        self.store.save(dict(q.new_state(0), extra='more state'))
        self.assertEqual(self.s3.lists, first_lists)
        self.assertGreater(self.store.total_bytes, first_bytes)
        with patch.object(q.time, 'monotonic', return_value=self.store.inventory_at + 601):
            self.store.save(dict(q.new_state(0), extra='third state'))
        self.assertGreater(self.s3.lists, first_lists)

    def test_corrupt_checkpoint_never_treated_as_empty(self):
        self.store.save(q.new_state(0))
        self.s3.corrupt = True
        with self.assertRaises(Exception):
            self.store.load()

    def test_checkpoint_body_readback_required(self):
        self.s3.corrupt = True
        with self.assertRaisesRegex(ValueError, 'BODY_MISMATCH'):
            self.store.save(q.new_state(0))

    def test_budget_reserves_both_workers_and_never_writes(self):
        with patch.object(q, 'BUCKET_BUDGET', q.PREFIX_BUDGET - 1):
            with self.assertRaisesRegex(ValueError, 'STORAGE_BUDGET'):
                self.store.save(q.new_state(0))
        self.assertEqual(self.s3.puts, 0)
        with patch.object(q, 'SHARD_BUDGET', 1):
            with self.assertRaisesRegex(ValueError, 'SHARD_BUDGET'):
                self.store.save(q.new_state(0))
        self.assertEqual(self.s3.puts, 0)

    def test_wrong_shard_checkpoint_rejected(self):
        self.store.save(q.new_state(1))
        with self.assertRaisesRegex(ValueError, 'CONTRACT_INVALID'):
            self.store.load()

    def test_ciphertext_not_plaintext_and_wrong_key_fails(self):
        state = q.new_state(0)
        state['sentinel'] = 'unreviewed-candidate'
        self.store.save(state)
        self.assertNotIn(b'unreviewed-candidate', self.s3.objects[self.store.key][0])
        wrong = q.R2Store(self.s3, 'chess-data', 0, base64.b64encode(b'y' * 32))
        with self.assertRaises(Exception):
            wrong.load()

    def test_request_budget_stops_before_write(self):
        self.store.class_a = 10000
        with self.assertRaisesRegex(ValueError, 'REQUEST_BUDGET'):
            self.store.save(q.new_state(0))
        self.assertEqual(self.s3.puts, 0)

    def test_transient_inventory_retry_is_charged(self):
        with patch.object(self.s3, 'list_objects_v2', side_effect=[TimeoutError(), {'Contents': []}]):
            with patch.object(q.time, 'sleep'):
                self.store.inventory()
        self.assertEqual(self.store.class_a, 2)

    def test_committed_put_timeout_recovered_by_exact_body(self):
        put = self.s3.put_object
        def uncertain(**kwargs):
            put(**kwargs)
            raise TimeoutError('response lost')
        with patch.object(self.s3, 'put_object', side_effect=uncertain):
            self.store.save(q.new_state(0))
        self.assertEqual(self.s3.puts, 1)
        self.assertEqual(self.store.etag, '1')
        self.assertEqual(self.store.load(), q.new_state(0))

    def test_transient_reads_exhaust_budget_without_empty_reset(self):
        with patch.object(self.s3, 'get_object', side_effect=TimeoutError()):
            with patch.object(q.time, 'sleep'):
                with self.assertRaises(TimeoutError):
                    self.store.load()
        self.assertEqual(self.store.class_b, 3)

    def test_raw_size_gate_prevents_writing_an_unloadable_checkpoint(self):
        with patch.object(q, 'MAX_STATE_RAW', 1):
            with self.assertRaisesRegex(ValueError, 'STATE_TOO_LARGE'):
                self.store.save(q.new_state(0))
        self.assertEqual(self.s3.puts, 0)

    def test_permission_failure_not_missing_state(self):
        with patch.object(self.s3, 'get_object', side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                self.store.load()


if __name__ == '__main__':
    unittest.main()
