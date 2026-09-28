#!/usr/bin/env python3
"""One resumable queue for archived-game backfill and incremental sacrifice mining.

Only the certified local snapshot and a private R2 checkpoint are read. No source
sites, public API mutation, manual data writes, or per-position R2 requests.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time

import chess
import chess.engine
import chess.pgn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Scripts/local'))
import analyze_brilliancies as analyzer
import validate_player_pgn_r2_receipt as archive_gate

PREFIX = 'private/brilliancies/queue-v1/'
SHARDS = 2  # Persistent partition contract; changing this requires migration.
PREFIX_BUDGET = 500_000_000
BUCKET_BUDGET = 8_000_000_000
SHARD_BUDGET = PREFIX_BUDGET // SHARDS
MAX_STATE_RAW = 500_000_000


def packed(value):
    return gzip.compress(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode(), mtime=0)


def unpacked(data):
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as handle:
        raw = handle.read(MAX_STATE_RAW + 1)
    if len(raw) > MAX_STATE_RAW:
        raise ValueError('QUEUE_STATE_TOO_LARGE')
    return json.loads(raw)


def profile(engine, screen, verify):
    return hashlib.sha256(json.dumps({
        'engine': engine.id['name'], 'screen': screen, 'verify': verify,
        'threads': 1, 'hashMb': 64, 'chess': chess.__version__,
        'detector': hashlib.sha256(Path(analyzer.__file__).read_bytes()).hexdigest(),
        'queueRules': 1,
    }, sort_keys=True).encode()).hexdigest()


def new_state(shard):
    return {'schemaVersion': 1, 'shard': shard, 'shards': SHARDS, 'records': {}}


class R2Store:
    def __init__(self, client, bucket, shard, encryption_key):
        if bucket != 'chess-data' or shard not in range(SHARDS):
            raise ValueError('QUEUE_STORAGE_CONFIG_INVALID')
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        key = base64.b64decode(encryption_key, validate=True)
        if len(key) != 32:
            raise ValueError('QUEUE_ENCRYPTION_KEY_INVALID')
        self.cipher = AESGCM(key)
        self.client, self.bucket, self.shard = client, bucket, shard
        self.key = f'{PREFIX}shard-{shard}.bin'
        self.class_a = self.class_b = 0
        self.etag = None
        self.total_bytes = self.prefix_bytes = self.previous_bytes = 0

    def charge(self, kind):
        field = 'class_a' if kind == 'A' else 'class_b'
        used = getattr(self, field) + 1
        if used > (10000 if kind == 'A' else 5000):
            raise ValueError('QUEUE_REQUEST_BUDGET_EXCEEDED')
        setattr(self, field, used)

    def inventory(self):
        total = own = pages = 0
        self.charge('A')
        for page in self.client.get_paginator('list_objects_v2').paginate(Bucket=self.bucket):
            if page.get('IsTruncated'):
                self.charge('A')
            pages += 1
            if pages > 1000:
                raise ValueError('QUEUE_INVENTORY_REQUEST_BUDGET')
            for row in page.get('Contents', []):
                total += row['Size']
                if row['Key'].startswith(PREFIX):
                    own += row['Size']
        self.total_bytes, self.prefix_bytes = total, own
        # Reserve both shards' maximum size, so concurrent writers cannot spend
        # the same free space. Other applications/account buckets are independent.
        if own > PREFIX_BUDGET or total - own + PREFIX_BUDGET > BUCKET_BUDGET:
            raise ValueError(f'QUEUE_STORAGE_BUDGET_EXCEEDED: bucketBytes={total}, queueBytes={own}')

    def load(self):
        self.charge('B')
        try:
            obj = self.client.get_object(Bucket=self.bucket, Key=self.key)
        except Exception as error:
            if getattr(error, 'response', {}).get('Error', {}).get('Code') in {'NoSuchKey', '404'}:
                return new_state(self.shard)
            raise
        body = obj['Body']
        try:
            data = body.read(SHARD_BUDGET + 1)
        finally:
            body.close()
        if len(data) > SHARD_BUDGET:
            raise ValueError('QUEUE_STATE_TOO_LARGE')
        if data[:4] != b'BRQ1':
            raise ValueError('QUEUE_CIPHERTEXT_INVALID')
        state = unpacked(self.cipher.decrypt(data[4:16], data[16:], self.key.encode()))
        if (state.get('schemaVersion'), state.get('shard'), state.get('shards')) != (1, self.shard, SHARDS):
            raise ValueError('QUEUE_STATE_CONTRACT_INVALID')
        if not isinstance(state.get('records'), dict):
            raise ValueError('QUEUE_RECORDS_INVALID')
        for key, row in state['records'].items():
            if (len(key) != 64 or any(c not in '0123456789abcdef' for c in key)
                    or row.get('status') not in {'complete', 'retry'}
                    or not isinstance(row.get('version'), str)
                    or not isinstance(row.get('candidates', []), list)):
                raise ValueError('QUEUE_RECORD_INVALID')
        self.etag, self.previous_bytes = obj['ETag'], len(data)
        return state

    def save(self, state):
        nonce = os.urandom(12)
        data = b'BRQ1' + nonce + self.cipher.encrypt(nonce, packed(state), self.key.encode())
        if len(data) > SHARD_BUDGET:
            raise ValueError('QUEUE_SHARD_BUDGET_EXCEEDED')
        # Recheck the bucket at each checkpoint, not just at task startup.
        self.inventory()
        condition = {'IfMatch': self.etag} if self.etag else {'IfNoneMatch': '*'}
        self.charge('A')
        self.charge('B')
        response = self.client.put_object(Bucket=self.bucket, Key=self.key, Body=data,
            ContentType='application/octet-stream', **condition)
        proof = self.client.get_object(Bucket=self.bucket, Key=self.key)
        body = proof['Body']
        try:
            actual = body.read(SHARD_BUDGET + 1)
        finally:
            body.close()
        if actual != data or proof['ETag'] != response['ETag']:
            raise ValueError('QUEUE_CHECKPOINT_BODY_MISMATCH')
        self.etag, self.previous_bytes = proof['ETag'], len(data)


def certified_files(root):
    base = root / 'docs/data'
    archive_gate.validate(bucket_root=base / 'index/by-player-buckets',
        receipt_path=base / 'index/player-pgn-r2-receipt.json',
        snapshot_path=base / 'snapshot.json', pgn_root=base / 'pgn',
        package_manifest_path=base / 'index/by-player/manifest.json', check_local_files=True)
    receipt = json.loads((base / 'index/player-pgn-r2-receipt.json').read_text())
    files = []
    for row in receipt['playerObjects']:
        if row['path'].endswith('/all.pgn'):
            path = (root / 'docs' / row['path']).resolve()
            if not path.is_relative_to((base / 'pgn/by-player').resolve()):
                raise ValueError('QUEUE_ARCHIVE_PATH_INVALID')
            files.append(path)
    if not files:
        raise ValueError('QUEUE_ARCHIVE_EMPTY')
    return sorted(set(files)), json.loads((base / 'snapshot.json').read_text())


def catalog(files, db, shard, known=(), cache=None):
    """Cache authenticated package offsets, never duplicate PGN bodies in R2."""
    known = set(known)
    cache = cache if cache is not None else {}
    db.execute('CREATE TABLE games (id TEXT PRIMARY KEY, pgn TEXT, priority INTEGER NOT NULL, path TEXT, offset INTEGER)')
    occurrences = rejected = reused = parsed = 0
    cache_version = hashlib.sha256((chess.__version__ +
        hashlib.sha256(Path(analyzer.pgn_helper.__file__).read_bytes()).hexdigest() +
        '|offset-catalog-v1').encode()).hexdigest()
    for path in files:
        # Certification has already established package membership and contents;
        # this digest additionally binds text offsets to exactly those bytes.
        digest = archive_gate.file_sha256(path)
        cache_key = path.parent.name + '/' + path.name
        previous = cache.get(cache_key, {})
        if previous.get('sha256') == digest and previous.get('version') == cache_version:
            entry = previous
            reused += 1
        else:
            entry = {'sha256': digest, 'version': cache_version, 'games': [],
                     'occurrences': 0, 'excluded': 0}
            parsed += 1
            with path.open(encoding='utf-8') as handle:
                while True:
                    offset = handle.tell()
                    game = chess.pgn.read_game(handle)
                    if game is None:
                        break
                    entry['occurrences'] += 1
                    board = game.board()
                    if (game.errors or not board.is_valid() or board.chess960
                            or type(board) is not chess.Board or game.end().ply() == game.ply()):
                        entry['excluded'] += 1
                        continue
                    text = game.accept(chess.pgn.StringExporter(headers=True, variations=False, comments=False))
                    fp = analyzer.pgn_helper.game_fingerprint(text)
                    identity = hashlib.sha256((board.fen() + '|' + fp).encode()).hexdigest()
                    if int(identity[:8], 16) % SHARDS == shard:
                        entry['games'].append([identity, offset])
            cache[cache_key] = entry
        occurrences += entry['occurrences']
        rejected += entry['excluded']
        for identity, offset in entry['games']:
            db.execute('INSERT OR IGNORE INTO games VALUES (?,NULL,?,?,?)',
                       (identity, int(identity in known), str(path), offset))
    db.commit()
    return {'archiveOccurrences': occurrences, 'excludedOccurrences': rejected,
            'reusedPackages': reused, 'parsedPackages': parsed,
            'eligibleGames': db.execute('SELECT count(*) FROM games').fetchone()[0]}


def prepare_catalog_state(state, db, version):
    """New arrivals retain priority across slices until successfully completed."""
    known = [row[0] for row in db.execute('SELECT id FROM games ORDER BY id')]
    old_known = set(state.get('knownGames', []))
    arrivals = set(known) - old_known if old_known else set()
    priority = (set(state.get('priorityGames', [])) | arrivals) & set(known)
    priority = {key for key in priority if not (
        state['records'].get(key, {}).get('version') == version and
        state['records'].get(key, {}).get('status') == 'complete')}
    changed = (known != state.get('knownGames') or version != state.get('activeVersion')
               or sorted(priority) != state.get('priorityGames', []))
    state.update(knownGames=known, priorityGames=sorted(priority), activeVersion=version)
    db.executemany('UPDATE games SET priority=0 WHERE id=?', ((key,) for key in priority))
    return changed


class DeadlineEngine:
    def __init__(self, engine, deadline):
        self.engine, self.deadline, self.id = engine, deadline, engine.id

    def analyse(self, *args, **kwargs):
        if time.monotonic() >= self.deadline:
            raise TimeoutError('QUEUE_SLICE_FINISHED')
        return self.engine.analyse(*args, **kwargs)


def summary(state, db, version):
    ids = {row[0] for row in db.execute('SELECT id FROM games')}
    rows = [row for key, row in state['records'].items()
            if key in ids and row['version'] == version]
    complete = sum(row['status'] == 'complete' for row in rows)
    return {'eligibleGames': len(ids), 'completedGames': complete,
            'pendingGames': len(ids) - complete,
            'retryGames': sum(row['status'] == 'retry' for row in rows),
            'candidatePositions': sum(len(row.get('candidates', [])) for row in rows
                                      if row['status'] == 'complete')}


def run_queue(db, state, version, engine, save, *, seconds, max_games,
              nodes_screen=200000, nodes_verify=1500000, checkpoint_games=25):
    deadline = time.monotonic() + seconds
    limited = DeadlineEngine(engine, deadline)
    processed = errors = dirty = 0
    last_saved = time.monotonic()
    # Deterministic ordering and one ledger serve both old and newly added games.
    for identity, text, path, offset in db.execute('SELECT id,pgn,path,offset FROM games ORDER BY priority,id'):
        if processed >= max_games or time.monotonic() >= deadline:
            break
        previous = state['records'].get(identity, {})
        if previous.get('version') == version:
            if previous.get('status') == 'complete' or previous.get('nextRetryAt', 0) > time.time():
                continue
        try:
            if text is not None:
                game = chess.pgn.read_game(io.StringIO(text))
            else:
                with open(path, encoding='utf-8') as handle:
                    handle.seek(offset)
                    game = chess.pgn.read_game(handle)
            if game is None or game.errors:
                raise ValueError('QUEUE_ARCHIVE_GAME_INVALID')
            # Reset cross-game hash state for reproducible per-game budgets.
            if 'Clear Hash' in getattr(engine, 'options', {}):
                engine.configure({'Clear Hash': None})
            candidates = analyzer.analyze_game(game, limited, nodes_screen, nodes_verify)
            record = {'version': version, 'status': 'complete', 'candidates': candidates}
        except TimeoutError:
            break  # Partial game is never marked complete.
        except (chess.engine.EngineError, chess.engine.EngineTerminatedError):
            if dirty:
                save(state)
            raise  # Infrastructure failure is not a bad game or an empty result.
        except ValueError as error:
            attempt = (previous.get('attempts', 0) if previous.get('version') == version else 0) + 1
            record = {'version': version, 'status': 'retry', 'attempts': attempt,
                      'nextRetryAt': time.time() + min(86400 * 7, 3600 * 2 ** min(attempt, 8)),
                      'error': type(error).__name__, 'candidates': []}
            errors += 1
        state['records'][identity] = record
        processed += 1
        dirty += 1
        if dirty >= checkpoint_games or time.monotonic() - last_saved >= 60:
            save(state)
            dirty = 0
            last_saved = time.monotonic()
            print(json.dumps({'checkpoint': processed, **summary(state, db, version)}), flush=True)
    if dirty:
        save(state)
    return {'processedThisRun': processed, 'errorsThisRun': errors, **summary(state, db, version)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--shard', type=int, required=True, choices=range(SHARDS))
    parser.add_argument('--engine', default='/usr/games/stockfish')
    parser.add_argument('--seconds', type=int, default=3000)
    parser.add_argument('--max-games', type=int, default=5000)
    parser.add_argument('--summary', type=Path)
    parser.add_argument('--export-candidates', type=Path, help='Read-only export of current candidates outside repository')
    args = parser.parse_args()
    if not 1 <= args.seconds <= 3300 or not 1 <= args.max_games <= 10000:
        parser.error('seconds must be 1..3300; max-games must be 1..10000')
    if not args.summary and not args.export_candidates:
        parser.error('--summary or --export-candidates is required')
    for output in (args.summary, args.export_candidates):
        if output and output.resolve().is_relative_to(args.root.resolve()):
            parser.error('outputs must be outside repository')
    import boto3
    from botocore.config import Config
    for name in ('R2_ENDPOINT', 'R2_ACCESS_KEY_ID', 'R2_SECRET_ACCESS_KEY', 'R2_BUCKET', 'BRILLIANCY_QUEUE_KEY'):
        if not os.environ.get(name):
            raise ValueError(f'R2_CONFIG_MISSING:{name}')
    client = boto3.client('s3', endpoint_url=os.environ['R2_ENDPOINT'], region_name='auto',
        aws_access_key_id=os.environ['R2_ACCESS_KEY_ID'],
        aws_secret_access_key=os.environ['R2_SECRET_ACCESS_KEY'],
        config=Config(connect_timeout=15, read_timeout=60, retries={'total_max_attempts': 1}))
    store = R2Store(client, os.environ['R2_BUCKET'], args.shard, os.environ['BRILLIANCY_QUEUE_KEY'])
    state = store.load()
    if args.export_candidates:
        known = set(state.get('knownGames', []))
        candidates = [candidate for key, row in state['records'].items()
                      if key in known and row['status'] == 'complete'
                      and row['version'] == state.get('activeVersion')
                      for candidate in row['candidates']]
        args.export_candidates.parent.mkdir(parents=True, exist_ok=True)
        args.export_candidates.write_text(json.dumps({'schemaVersion': 1, 'candidates': candidates}, ensure_ascii=False))
        print(json.dumps({'exportedCandidates': len(candidates), 'shard': args.shard}))
        return
    store.inventory()
    files, snapshot = certified_files(args.root)
    print(json.dumps({'phase': 'catalog', 'files': len(files), 'shard': args.shard}), flush=True)
    with tempfile.TemporaryDirectory(prefix='brilliancy-queue-') as temporary:
        db = sqlite3.connect(Path(temporary) / 'catalog.sqlite')
        catalog_cache = state.setdefault('catalogFiles', {})
        counts = catalog(files, db, args.shard, state.get('knownGames', []), catalog_cache)
        print(json.dumps({'phase': 'analysis', **counts}), flush=True)
        engine = chess.engine.SimpleEngine.popen_uci(args.engine, timeout=120)
        try:
            engine.configure({'Threads': 1, 'Hash': 64})
            version = profile(engine, 200000, 1500000)
            changed_catalog = prepare_catalog_state(state, db, version)
            state['lastInputSnapshot'] = snapshot['snapshotId']
            state['lastEngine'] = engine.id['name']
            if changed_catalog or counts['parsedPackages']:
                store.save(state)
            result = run_queue(db, state, version, engine, store.save,
                               seconds=args.seconds, max_games=args.max_games)
            result.update(counts)
            result.update({'shard': args.shard, 'shards': SHARDS, 'version': version,
                'engine': engine.id['name'], 'inputSnapshot': snapshot['snapshotId'],
                'checkpointBytes': store.previous_bytes, 'bucketBytes': store.total_bytes,
                'classARequests': store.class_a, 'classBRequests': store.class_b,
                'status': 'complete' if result['pendingGames'] == 0 else 'in-progress',
                'scope': 'archived-standard-games-sacrifice-candidates',
                'publicAutoPublish': False})
            args.summary.parent.mkdir(parents=True, exist_ok=True)
            args.summary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
            print(json.dumps(result), flush=True)
        finally:
            engine.quit()
            db.close()


if __name__ == '__main__':
    main()
