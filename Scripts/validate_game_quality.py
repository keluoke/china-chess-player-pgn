#!/usr/bin/env python3
"""Fail snapshots when public metrics/packages disagree with canonical quality."""
import collections
import json
import pathlib
from canonical_player_facts import PLAYER_GAME_FACTS, load_fact_dataset
from game_quality import fact_quality

ROOT = pathlib.Path(__file__).resolve().parents[1]

def validate(root=ROOT):
    facts, manifest = load_fact_dataset(root/'data/generated/player-game-facts/manifest.json', 'player-game-facts')
    data_path = root/'data/generated/player-game-facts'/manifest['dataFile']
    if data_path.stat().st_size >= 100 * 1024 * 1024:
        raise ValueError('FACT_FILE_EXCEEDS_GIT_LIMIT')
    event_facts, _ = load_fact_dataset(root/'data/generated/player-event-facts/manifest.json', 'player-event-facts')
    event_counts = collections.defaultdict(set)
    for fact in event_facts:
        event_counts[str(fact['fideID'])].add(str(fact['tournamentID']))
    counts = collections.defaultdict(lambda: [0, 0, 0])
    for fact in facts:
        quality = fact_quality(fact)
        if quality['resultStatus'] == 'disputed' and quality['resultStatsEligible']:
            raise ValueError('DISPUTED_RESULT_STATISTICS: ' + fact['id'])
        for fide_id in fact.get('playerFideIDs', []):
            counts[fide_id][0] += 1
            counts[fide_id][1] += int(quality['replayable'])
            counts[fide_id][2] += int(not quality['replayable'])
    expected_packages = set()
    for fide_id, (archived, playable, excluded) in counts.items():
        detail = json.loads((root/f'docs/data/index/by-player/fide-{fide_id}.json').read_text())
        totals = detail['totals']
        if (totals.get('games'), totals.get('playableGames'), totals.get('excludedGames')) != (archived, playable, excluded):
            raise ValueError('PLAYER_QUALITY_COUNTS: ' + fide_id)
        if totals.get('participationEvents') != len(event_counts[fide_id]):
            raise ValueError('PARTICIPATION_FACT_COUNTS: ' + fide_id)
        expected_packages.update(root / 'docs' / p['pgnPath'] for p in detail['packages'])
        package = next(p for p in detail['packages'] if p['id'] == 'all')
        if package['gameCount'] != playable:
            raise ValueError('PLAYER_PACKAGE_QUALITY_COUNT: ' + fide_id)
        api = json.loads((root/f'docs/api/v1/players/fide-{fide_id}.json').read_text())
        if (api.get('archivedGameCount'), api.get('playableGameCount'), api.get('excludedGameCount')) != (archived, playable, excluded):
            raise ValueError('API_QUALITY_COUNTS: ' + fide_id)
        if api.get('pgnEventCount') != totals.get('pgnEvents') or api.get('participationEventCount') != totals.get('participationEvents'):
            raise ValueError('API_EVENT_COUNTS: ' + fide_id)
    actual_packages = set((root/'docs/data/pgn/by-player').rglob('*.pgn'))
    if actual_packages != expected_packages:
        raise ValueError(f'PLAYER_PACKAGE_FILE_SET: missing={len(expected_packages-actual_packages)}, stale={len(actual_packages-expected_packages)}')
    metrics = json.loads((root/'docs/data/public-metrics.json').read_text())['totals']
    catalog = json.loads((root/'docs/data/index/public-events.json').read_text())
    if metrics.get('playableUniqueGames') != catalog['totals']['playableGames'] or metrics.get('archivedUniqueGames') != len(facts):
        raise ValueError('UNIQUE_GAME_METRICS')
    return {'qualityVersion':1, 'facts':len(facts), 'players':len(counts), 'snapshotId':manifest['snapshotId']}

if __name__ == '__main__':
    print(json.dumps(validate()))
