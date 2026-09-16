#!/usr/bin/env python3
"""Bounded, read-only checks on ordinary production routes after deployment."""
from __future__ import annotations
import argparse
import hashlib
import json
import pathlib
import subprocess
import tempfile
from urllib.parse import urljoin
from build_static_player_pgn import split_pgn_games
from game_quality import inspect_game

ORIGINS = ['https://chessdb.aigclabs.cc', 'https://4chess.cc', 'https://www.4chess.cc', 'https://china-chess-player-pgn.pages.dev']
CORS_SAMPLE = 'https://data.chessdb.aigclabs.cc/data/pgn/objects/sha256/89/89a3aff2273c5fd7fe05a69e8db5828212865b7286b31d2f5434343e07a563b1.pgn'

def fetch(url, *, method='GET', origin=None, expected=200):
    with tempfile.TemporaryDirectory() as directory:
        root = pathlib.Path(directory)
        cmd = ['curl', '--silent', '--show-error', '--location', '--compressed', '--max-time', '45',
               '--dump-header', str(root/'headers'), '--output', str(root/'body'), '--write-out', '%{http_code}', url]
        if method == 'HEAD': cmd += ['--head']
        if origin: cmd += ['--header', 'Origin: ' + origin]
        run = subprocess.run(cmd, capture_output=True, text=True, check=True)
        if int(run.stdout) != expected:
            raise ValueError(f'CANARY_HTTP: {url}: {run.stdout} != {expected}')
        headers = {}
        for line in (root/'headers').read_text().splitlines():
            if line.startswith('HTTP/'): headers = {}
            elif ':' in line:
                key, value = line.split(':', 1); headers[key.lower()] = value.strip()
        return (root/'body').read_bytes(), headers

def check_pgn(body, expected_count, sha=None):
    if sha and hashlib.sha256(body).hexdigest() != sha:
        raise ValueError('CANARY_PGN_HASH')
    games = split_pgn_games(body.decode('utf-8-sig'))
    if len(games) != expected_count or not games:
        raise ValueError(f'CANARY_PGN_COUNT: {len(games)} != {expected_count}')
    if any(not inspect_game(game)['replayable'] for game in games):
        raise ValueError('CANARY_PGN_NOT_REPLAYABLE')
    return len(games)

def run_checks(site, expected_snapshot='', cors_only=False):
    checks = []
    for origin in ORIGINS:
        for method in ('GET', 'HEAD'):
            _, headers = fetch(CORS_SAMPLE, origin=origin, method=method)
            if headers.get('access-control-allow-origin') != origin:
                raise ValueError('CANARY_CORS: ' + origin + ' ' + method)
            checks.append({'check':'cors', 'origin':origin, 'method':method, 'ok':True})
    if cors_only: return {'checks': checks}
    def get(path):
        body, headers = fetch(urljoin(site+'/', path.lstrip('/')))
        if 'json' not in headers.get('content-type', ''):
            raise ValueError('CANARY_JSON_MIME: ' + path)
        return json.loads(body)
    snapshot = get('data/snapshot.json'); sid = snapshot['snapshotId']
    if expected_snapshot and sid != expected_snapshot: raise ValueError('CANARY_SNAPSHOT: ' + sid)
    for path in ('docs/data/registry/players.json', 'docs/data/registry/manifest.json'):
        entries = [entry for entry in snapshot['outputs'] if entry['path'] == path]
        if len(entries) != 1: raise ValueError('CANARY_SNAPSHOT_OUTPUT: ' + path)
        body, _ = fetch(urljoin(site+'/', path.removeprefix('docs/')))
        if len(body) != entries[0]['bytes'] or hashlib.sha256(body).hexdigest() != entries[0]['sha256']:
            raise ValueError('CANARY_REGISTRY_HASH: ' + path)
    bootstrap = get('data/search-bootstrap.json')
    players = {str(row.get('fideID')): row for row in bootstrap['players']}
    for fide_id in ('8602980', '8603006', '8608288', '260290'):
        api = get(f'api/v1/players/fide-{fide_id}.json')
        if api.get('snapshotId') != sid: raise ValueError('CANARY_API_SNAPSHOT')
        for field in ('participationEventCount', 'pgnEventCount', 'playableGameCount', 'excludedGameCount'):
            if api.get(field) is None or api[field] != players[fide_id].get(field, 0):
                raise ValueError(f'CANARY_METRIC: {fide_id}:{field}')
        if fide_id == '8603006':
            package = next(p for p in api['packages'] if p['id'] == 'all')
            for route in (package['publicURL'], urljoin(site+'/', package['pgnPath'])):
                body, headers = fetch(route, origin=site)
                if 'chess-pgn' not in headers.get('content-type', ''): raise ValueError('CANARY_PGN_MIME')
                check_pgn(body, package['gameCount'], package['sha256'])
            checks.append({'check':'player-package', 'fideID':fide_id, 'games':package['gameCount'], 'sha256':package['sha256']})
    catalog = get('data/index/public-events.json')
    for tid in ('1458883', '1227491'):
        detail = get(f'data/index/event-details/tnr{tid}.json')
        quality = detail['completeness']
        if 'excludedArchivedGames' not in quality.get('counts', {}) or (quality.get('playableComplete') and quality['counts']['excludedArchivedGames']):
            raise ValueError('CANARY_KNOWN_INVALID_ARCHIVE: ' + tid)
        event = next(e for e in catalog['events'] if str(e.get('tournamentID')) == tid)
        body, headers = fetch(urljoin(site+'/', event['pgnPath']))
        check_pgn(body, event['gameCount'])
        checks.append({'check':'event-package', 'tournamentID':tid, 'games':event['gameCount'], 'coverage':quality.get('replayCoverage')})
    objects = get('data/index/event-pgn-objects.json')['events']
    for tid in ('1458883', '1227491'):
        entry = objects[tid]
        body, _ = fetch(urljoin(site+'/', f"api/event-pgn?tnr={tid}&sha={entry['sha256'][:16]}"))
        if hashlib.sha256(body).hexdigest() != entry['sha256']:
            raise ValueError('CANARY_ARCHIVE_HASH: ' + tid)
        checks.append({'check':'retained-archive', 'tournamentID':tid, 'sha256':entry['sha256'], 'records':len(split_pgn_games(body.decode('utf-8-sig')))})
    disputed = get('data/index/event-details/tnr1059818.json')
    pairing = next(pair for row in disputed['rounds'] if str(row['round']) == '1' for pair in row['pairings'] if str(pair['board']) == '62')
    quality = pairing.get('localGame', {}).get('quality', {})
    if quality.get('resultStatus') not in {'disputed', 'reviewed'} or (quality['resultStatus'] == 'disputed' and quality.get('resultStatsEligible') is not False):
        raise ValueError('CANARY_RESULT_DISPUTE')
    checks.append({'check':'result-dispute', 'tournamentID':'1059818', 'quality':quality})
    fetch(urljoin(site+'/', 'api/event-pgn?tnr=1458883&sha='+'0'*16), expected=409)
    metrics = get('data/public-metrics.json')['totals']
    if metrics.get('playableUniqueGames') != catalog['totals']['playableGames']:
        raise ValueError('CANARY_UNIQUE_METRICS')
    if get('data/snapshot.json')['snapshotId'] != sid: raise ValueError('CANARY_SNAPSHOT_CHANGED')
    return {'snapshotId':sid, 'inputCommit':snapshot['inputCommit'], 'checks':checks, 'metrics':metrics}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--site', default=ORIGINS[0]); parser.add_argument('--expect-snapshot', default='')
    parser.add_argument('--cors-only', action='store_true'); parser.add_argument('--output')
    args = parser.parse_args()
    result = run_checks(args.site.rstrip('/'), args.expect_snapshot, args.cors_only)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output: pathlib.Path(args.output).write_text(text+'\n')
    print(text)
if __name__ == '__main__': main()
