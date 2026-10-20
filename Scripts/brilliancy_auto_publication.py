#!/usr/bin/env python3
"""Adjudicate authenticated R2 evidence, stage an exact release, and fast-forward it.

No engine is launched and no source website is contacted. Rejected candidates
and complete engine records never enter the release or public artifacts.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from collections import Counter, defaultdict

import chess
import brilliancy_auto_policy as policy
import brilliancy_approved_store as approved
import brilliancy_publication_gate as gate
import brilliancy_publication_preview as preview
import brilliancy_quality as quality
import brilliancy_quality_gate as classifier
from brilliancy_quality_backup import QualityBackup, check_remote_record
from brilliancy_queue import R2Store, SHARD_BUDGET
from monthly_release import digest, publish, validate_release
from validate_brilliancies import validate_privacy
from validate_public_privacy import offending_keys

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = approved.DIR
RECEIPT = (DIRECTORY / 'manifest.json').as_posix()


def allowed(path: str) -> bool:
    return bool(re.fullmatch(r'data/generated/brilliancies-approved/(manifest|[0-9a-f]{2})\.json', path))


def json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode()


def read_checkpoint(store, label: str):
    """Bound memory and pin every range to a single ETag, including final HEAD."""
    first = store.request('B', lambda: store.client.head_object(Bucket=store.bucket, Key=store.key))
    size, etag = first.get('ContentLength'), first.get('ETag')
    if type(size) is not int or not 0 < size <= SHARD_BUDGET or not etag:
        raise ValueError('QC_RELEASE_HEAD_INVALID')
    body = bytearray()
    step = 4 * 1024 * 1024
    for start in range(0, size, step):
        end = min(size, start + step) - 1
        def read(start=start, end=end):
            response = store.client.get_object(Bucket=store.bucket, Key=store.key,
                         Range=f'bytes={start}-{end}', IfMatch=etag)
            stream = response['Body']
            try:
                if response.get('ETag') != etag or response.get('ContentRange') != f'bytes {start}-{end}/{size}':
                    raise ValueError('QC_RELEASE_BASELINE_CHANGED')
                data = stream.read(end - start + 2)
            finally:
                stream.close()
            if len(data) != end - start + 1:
                raise ConnectionError('QC_RELEASE_SHORT_READ')
            return data
        body.extend(store.request('B', read))
    current = store.request('B', lambda: store.client.head_object(Bucket=store.bucket, Key=store.key))
    if current.get('ETag') != etag or current.get('ContentLength') != size or len(body) != size:
        raise ValueError('QC_RELEASE_BASELINE_CHANGED')
    raw = bytes(body)
    return store.decode(raw), {'label': label, 'etag': etag, 'bytes': size,
                               'sha256': hashlib.sha256(raw).hexdigest()}


def quality_rows(backup, state, profile):
    records = state['records']
    backup.checked_records(records)
    output = {}
    for key, row in records.items():
        cid, ihash, observed, _grade, enc = check_remote_record(key, row)
        if observed in preview.LEGACY_PROFILES:
            continue
        if observed != profile or cid in output:
            raise ValueError('QC_RELEASE_PROFILE_DRIFT')
        outcome = json.loads(backup.store.cipher.decrypt(enc[:12], enc[12:], (cid + profile).encode()))
        output[cid] = (ihash, outcome)
    return output


def adjudicate(item, first, deep):
    expected = quality.digest(item)
    if first is None or deep is None:
        return {'tier': 'pending-independent-evidence'}, None
    if first[0] != expected or deep[0] != expected:
        return {'tier': 'isolated-input-conflict'}, None
    try:
        # A quick negative decision avoids replaying millions of moves for
        # non-releasable candidates. Every positive must pass the legal gate.
        preliminary = classifier.assess(first[1], deep[1])
        if preliminary['tier'] not in ('S', 'A'):
            return preliminary, None
        decision = gate.certify(item, first[1], deep[1])
        return decision, {'first': first[1], 'deep': deep[1]}
    except ValueError:
        return {'tier': 'isolated-invalid-evidence'}, None


def public_item(item, decision, proofs, published_at):
    keep = ('id', 'schemaVersion', 'game', 'event', 'white', 'black', 'position',
            'move', 'themes', 'rights', 'actualContinuationUci', 'actualContinuationSan')
    result = {name: copy.deepcopy(item[name]) for name in keep if name in item}
    result['status'] = 'published'
    result['publishedAt'] = published_at
    result['classification'] = {'symbol': '!!', 'ruleVersion': gate.RULE_VERSION}
    result['title'] = f"实战弃子：{item['move']['san']}"
    explanations = {
        'decisive_persistent_sacrifice': '弃子后仍有持续补偿，且明显优于其他选择。',
        'stable_persistent_sacrifice': '接受或拒绝弃子，走子方均保有稳定补偿。',
        'forced_tactical_payoff': '弃子后通过强制战术收回材料，并取得额外收益。',
    }
    result['summary'] = explanations[decision['reason']]
    result['explanation'] = result['summary'] + ' 已通过两个引擎、两档搜索预算的独立验证。'
    engines = proofs['deep']['engines']
    result['verification'] = {
        'engine': 'Stockfish 16 + Stockfish 17.1',
        'nodes': min(e['chosen']['nodes'] for e in engines),
        'depth': min(e['chosen']['depth'] for e in engines),
        'evaluation': {'pov': item['position']['sideToMove'], 'type': 'cp',
                       'score': min(e['chosen']['cp'] for e in engines)},
    }
    board = chess.Board(item['position']['fenBefore'])
    board.push(chess.Move.from_uci(item['move']['uci']))
    after_fen = board.fen()
    chosen = next(e for e in engines if e['engine'] == 'Stockfish 17.1')['chosen']['pv']
    chosen = chosen[1:]
    sans = []
    for uci in chosen:
        move = chess.Move.from_uci(uci)
        sans.append(board.san(move)); board.push(move)
    result['analysisLines'] = [{'title': '最佳应对变化', 'fenBefore': after_fen,
                                'uci': chosen, 'san': sans}]
    validate_privacy(json.dumps(result, ensure_ascii=False), 'machine-public-item')
    if list(offending_keys(result)):
        raise ValueError('QC_RELEASE_PUBLIC_FIELDS_INVALID')
    result['approval'] = {'tier': decision['tier'], 'ruleVersion': gate.RULE_VERSION,
                          'candidateHash': quality.digest(item), 'proofHash': quality.digest(proofs),
                          'publicHash': quality.digest(result)}
    return result


def previous_items(root):
    # Revalidate the existing bundle before retaining timestamps or tombstones.
    approved.load(root)
    folder = root / DIRECTORY
    result = {}
    if folder.exists():
        for file in sorted(folder.glob('??.json')):
            for item in json.loads(file.read_text())['items']:
                result[item['id']] = item
    return result


def requires_rebuild(release):
    rows = json.loads((release / 'release.json').read_text())['files']
    return any((row.get('sha256') != row['baseSha256']) if row['operation'] == 'upsert'
               else row['baseSha256'] is not None for row in rows)


def collect(client, bucket, key, release: Path, *, root=ROOT):
    policy_value, policy_hash = policy.load(root)
    if release.exists():
        raise ValueError('QC_RELEASE_OUTPUT_EXISTS')
    base = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    prior = previous_items(root)
    withdrawals = json.loads((root / 'data/manual/brilliancies/withdrawals.json').read_text())
    ids = withdrawals.get('ids')
    if withdrawals.get('schemaVersion') != 1 or not isinstance(ids, list) or any(
            not isinstance(cid, str) or not approved.BRILLIANCY_ID.fullmatch(cid) for cid in ids):
        raise ValueError('QC_RELEASE_WITHDRAWALS_INVALID')
    withdrawn = set(ids)
    seen, published, baselines, stats, coverage = set(), [], [], {}, {}
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    for lane in ('history', 'incremental'):
        counts = Counter(); games = candidates = 0
        for shard in (0, 1):
            store = R2Store(client, bucket, shard, key, lane)
            state, receipt = read_checkpoint(store, f'{lane}-candidates-{shard}')
            baselines.append((store, receipt))
            items, incomplete = preview.source_candidates(state, lane)
            games += sum(row.get('status') == 'complete' and row.get('version') == state['activeVersion']
                         for row in state['records'].values())
            candidates += len(items)
            counts['pending-source-games'] += incomplete
            results = {}
            for stage in ('first', 'deep'):
                backup = QualityBackup(client, bucket, shard, key,
                         stage if lane == 'history' else f'incremental-{stage}')
                data, receipt = read_checkpoint(backup.store, f'{lane}-{stage}-{shard}')
                baselines.append((backup.store, receipt))
                results[stage] = quality_rows(backup, data, preview.PROFILES[lane][stage])
            for cid, item in items.items():
                if cid in seen:
                    raise ValueError('QC_RELEASE_CROSS_LANE_DUPLICATE')
                seen.add(cid)
                decision, proof = adjudicate(item, results['first'].get(cid), results['deep'].get(cid))
                if decision['tier'] in policy_value['allowedTiers']:
                    try:
                        value = public_item(item, decision, proof, prior.get(cid, {}).get('publishedAt', now))
                    except ValueError:
                        counts['isolated-public-projection'] += 1
                        continue
                    if cid in withdrawn or prior.get(cid, {}).get('status') == 'withdrawn':
                        value['status'] = 'withdrawn'
                        value['approval']['publicHash'] = quality.digest({k:v for k,v in value.items() if k != 'approval'})
                        counts['withdrawn'] += 1
                    else:
                        counts[decision['tier']] += 1
                    published.append(value)
                else:
                    counts[decision['tier']] += 1
        stats[lane] = dict(counts)
        coverage[lane] = {'completedGames': games, 'candidatePositions': candidates}
    # A disappearing candidate is a queue drift, not permission to silently
    # erase an earlier public record. Explicit withdrawals retain a tombstone.
    if set(prior) - seen:
        raise ValueError('QC_RELEASE_PREVIOUS_CANDIDATE_MISSING')
    released = {item['id'] for item in published}
    for cid in set(prior) - released:
        value = copy.deepcopy(prior[cid]); value['status'] = 'withdrawn'
        value['approval']['publicHash'] = quality.digest({k:v for k,v in value.items() if k != 'approval'})
        published.append(value)
    # Inputs may have advanced while quality was being read. Cancel the entire
    # batch before creating an artifact if even one baseline has changed.
    for store, receipt in baselines:
        current = store.request('B', lambda store=store: client.head_object(Bucket=bucket, Key=store.key))
        if current.get('ETag') != receipt['etag'] or current.get('ContentLength') != receipt['bytes']:
            raise ValueError('QC_RELEASE_BASELINE_CHANGED')
    stage = release.parent / (release.name + '.staging')
    if stage.exists():
        raise ValueError('QC_RELEASE_STAGING_EXISTS')
    folder = stage / DIRECTORY
    folder.mkdir(parents=True)
    grouped = defaultdict(list)
    for item in sorted(published, key=lambda row: row['id']):
        grouped[item['id'][3:5]].append(item)
    files = []
    for prefix, items in sorted(grouped.items()):
        path = folder / (prefix + '.json')
        path.write_bytes(json_bytes({'schemaVersion': 1, 'bucket': prefix, 'items': items}))
        if path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError('QC_RELEASE_SHARD_BUDGET')
        files.append({'path': path.name, 'sha256': digest(path), 'bytes': path.stat().st_size})
    manifest = {'schemaVersion': 1, 'qualityRuleVersion': gate.RULE_VERSION,
                'policyId': policy.POLICY_ID, 'policySha256': policy_hash,
                'totalItems': len(published), 'files': files, 'coverage': coverage,
                'authenticatedInputs': [receipt for _, receipt in baselines]}
    previous_manifest = root / RECEIPT
    if previous_manifest.is_file():
        old = json.loads(previous_manifest.read_text())
        stable = lambda value: {k:v for k,v in value.items() if k != 'authenticatedInputs'}
        if stable(old) == stable(manifest):
            # Freshly authenticated but semantically identical R2 ciphertext
            # must not cause a publish -> rebuild -> analyze -> publish loop.
            manifest = old
    (folder / 'manifest.json').write_bytes(json_bytes(manifest))
    rows = []
    wanted = {p.relative_to(stage).as_posix() for p in folder.iterdir()}
    existing = {p.relative_to(root).as_posix() for p in (root / DIRECTORY).glob('*.json')}
    for path in sorted(wanted | existing):
        source, current = stage / path, root / path
        row = {'path': path, 'baseSha256': digest(current) if current.is_file() else None,
               'operation': 'upsert' if source.exists() else 'delete'}
        if source.exists():
            row.update(sha256=digest(source), bytes=source.stat().st_size)
        rows.append(row)
    (stage / 'release.json').write_bytes(json_bytes({'schemaVersion': 1, 'inputCommit': base, 'files': rows}))
    validate_release(stage, base, root=root, allowed=allowed, receipt=RECEIPT)
    # Validate with the exact policy that will be read by the rebuild.
    import shutil
    policy_folder = stage / policy.PATH.parent
    policy_folder.mkdir(parents=True)
    shutil.copyfile(root / policy.PATH, stage / policy.PATH)
    approved.load(stage)
    (stage / policy.PATH).unlink()
    stage.rename(release)
    return {'schemaVersion': 1, 'ruleVersion': gate.RULE_VERSION, 'adjudication': stats,
            'coverage': coverage, 'approvedItems': sum(x['status'] == 'published' for x in published),
            'withdrawnItems': sum(x['status'] == 'withdrawn' for x in published)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('collect', 'publish'))
    parser.add_argument('--release', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'publish':
        # Revalidate policy and the approved layer before touching the checkout.
        import tempfile, shutil
        with tempfile.TemporaryDirectory(prefix='brilliancy-validate-') as directory:
            scratch = Path(directory)
            shutil.copytree(args.release / DIRECTORY, scratch / DIRECTORY)
            (scratch / policy.PATH.parent).mkdir(parents=True)
            shutil.copyfile(ROOT / policy.PATH, scratch / policy.PATH)
            approved.load(scratch)
        publish(args.release, root=ROOT, allowed=allowed, receipt=RECEIPT,
                message='Publish automatically certified brilliancies')
        # The immutable artifact retains this decision on a rerun even when
        # its commit already landed but the rebuild dispatch previously failed.
        with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
            stream.write(f"rebuild_required={str(requires_rebuild(args.release)).lower()}\n")
    else:
        from brilliancy_incremental_quality import r2_client
        client, bucket = r2_client()
        key = os.environ.get('BRILLIANCY_QUEUE_KEY', '')
        print(json.dumps(collect(client, bucket, key, args.release), ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
