#!/usr/bin/env python3
"""Build public editions and playable, fingerprint-deduplicated event packages.

Only immutable canonical facts/verified assets feed this layer. Player packages
are never inputs. Existing section IDs are retained as deep-link aliases.
"""
from __future__ import annotations
import collections
import datetime as dt
import hashlib
import io
import json
import logging
import pathlib
import re
import chess.pgn
from result_review import annotate_pgn
from game_quality import inspect_game, replayable
import build_static_player_pgn as pgn
from canonical_player_facts import PLAYER_GAME_FACTS, load_fact_dataset
from event_identity import describe, stable_id
from build_event_catalog import TEST_NAME_RE
from stable_json import write_json
from snapshot_context import snapshot_id

ROOT = pathlib.Path(__file__).resolve().parents[1]
CATALOG = ROOT / 'docs/data/index/public-events.json'
PACKAGES = ROOT / 'docs/data/pgn/catalog-events'
PACKAGE_INDEX = ROOT / 'docs/data/index/catalog-pgn-packages.json'


def playable(game):
    """Nonempty legal mainline; unfinished games remain usable and labelled."""
    return inspect_game(game)['replayable']


def section_order(row):
    label = row.get('groupLabel') or row.get('displayName') or row.get('name') or ''
    age = re.search(r'U(\d+)', label)
    level = 1 if '候补' in label else 2 if '一级' in label else 0
    return ({'standard':0,'rapid':1,'blitz':2}.get(row.get('timeControl'),3),
            int(age[1]) if age else 0, level, '女子' in label, label)


def build_editions(events, fingerprints):
    candidates = collections.defaultdict(list)
    identities = {}
    canonical_by_base = collections.defaultdict(set)
    canonical_dates = collections.defaultdict(list)
    for row in events:
        identity = describe(row.get('chineseName') or row.get('displayName') or row.get('name') or '', row.get('date') or '', row.get('canonicalEventID') or '')
        identities[row['id']] = identity
        if row.get('canonicalEventID') and identity['series'] and identity['year']:
            canonical_by_base[identity['base']].add(row['canonicalEventID'])
            if row.get('date'):
                canonical_dates[row['canonicalEventID']].append(row['date'])
    for row in events:
        identity = identities[row['id']]
        base = identity['base']
        canonical = row.get('canonicalEventID')
        bridges = canonical_by_base.get(base, set())
        if not canonical and len(bridges) == 1 and row.get('date'):
            candidate = next(iter(bridges))
            date = dt.date.fromisoformat(row['date'])
            if any(abs((date-dt.date.fromisoformat(day)).days) <= 45 for day in canonical_dates[candidate]):
                canonical = candidate
        if canonical:
            base = 'reviewed|' + canonical
        candidates[base].append((row, identity))
    editions = []
    for base, rows in sorted(candidates.items()):
        # Split repeated same-name events into bounded date windows. Never use
        # rolling adjacency, which could chain weekly events into a whole year.
        clusters = []
        for row, identity in sorted(rows, key=lambda pair: pair[0].get('date') or ''):
            date = row.get('date') or ''
            try: ordinal = dt.date.fromisoformat(date).toordinal()
            except ValueError: ordinal = None
            if not clusters or (not base.startswith('reviewed|') and (ordinal is None or clusters[-1][0] is None or ordinal-clusters[-1][0] > 45)):
                clusters.append((ordinal, []))
            clusters[-1][1].append((row, identity))
        for _, cluster in clusters:
            first, identity = max(cluster, key=lambda pair: (bool(pair[0].get('chineseName')), bool(pair[0].get('date'))))
            dates = sorted(r['date'] for r,_ in cluster if r.get('date'))
            eid = stable_id(base+'|'+(dates[0][:7] if dates else str(first['id'])))
            child_ids = []
            union = set()
            sections = collections.defaultdict(list)
            group_targets = collections.defaultdict(list)
            for member, info in cluster:
                if member.get('tournamentID') and info['groupLabel']:
                    group_targets[(info['timeControl'], info['groupLabel'])].append(member['tournamentID'])
            for row, info in cluster:
                group_key = (info['timeControl'], info['groupLabel'] or row.get('name'))
                targets = group_targets.get(group_key, [])
                tid = row.get('tournamentID') or (targets[0] if len(targets) == 1 else None)
                key = ('tnr', str(tid)) if tid else group_key
                sections[key].append(row)
            for members in sections.values():
                primary = min(members, key=lambda r:(not bool(r.get('tournamentID')), r['id']))
                combined = set().union(*(fingerprints.get(r['id'],set()) for r in members))
                child_ids.append(primary['id'])
                for member in members:
                    member['sectionID'] = primary['id']
                    fingerprints[member['id']] = combined
                    member['gameCount'] = len(combined)
            for row, info in cluster:
                row['editionID'] = eid
                row['timeControl'] = info['timeControl']
                row['groupLabel'] = info['groupLabel'] or row.get('groupLabel')
                if info['series']:
                    row['series'] = info['series']; row['seriesLabel'] = info['seriesLabel']
                    row['displayName'] = row.get('chineseName') or (info['title'] + (' · '+row['groupLabel'] if row.get('groupLabel') and row['groupLabel'] not in info['title'] else ''))
                    row['nameTranslationPending'] = False
                union.update(fingerprints.get(row['id'],set()))
            cluster_by_id = {member['id']:member for member, _ in cluster}
            child_ids.sort(key=lambda ident: section_order(cluster_by_id[ident]))
            title = identity['title']
            if first.get('station') and identity.get('station'):
                title = title.replace('（'+identity['station']+'）', '（'+first['station']+'）')
            editions.append({'id':eid,'displayName':title,'name':title,
                'year':identity['year'],'date':dates[-1] if dates else None,
                'dateBegin':dates[0] if dates else None,'series':identity['series'] or first.get('series'),
                'seriesLabel':identity['seriesLabel'] or first.get('seriesLabel'),
                'sections':child_ids,'sectionCount':len(child_ids),
                'gameCount':len(union),'detailStatus':'published' if any(r.get('detailPath') for r,_ in cluster) else 'missing-detail',
                'isEdition':True})
            fingerprints[eid] = union
    return editions


def validate_catalog(catalog, packages):
    """Fail the snapshot on dangling links, duplicate parents or count drift."""
    rows = [*catalog['events'], *catalog['editions']]
    by_id = {row['id']:row for row in rows}
    if len(by_id) != len(rows):
        raise ValueError('EVENT_LIBRARY_DUPLICATE_ID')
    for parent in catalog['editions']:
        children = parent['sections']
        if len(set(children)) != len(children) or parent['sectionCount'] != len(children):
            raise ValueError('EVENT_LIBRARY_SECTION_COUNT')
        if any(child not in by_id or by_id[child].get('editionID') != parent['id'] for child in children):
            raise ValueError('EVENT_LIBRARY_PARENT_LINK')
    for row in rows:
        if not row.get('gameCount'):
            if row.get('pgnPath'):
                raise ValueError('EVENT_LIBRARY_EMPTY_PACKAGE')
            continue
        ident = 'event-'+hashlib.sha256(row['id'].encode()).hexdigest()[:24]
        package = packages.get(ident)
        if not package or package['gameCount'] != row['gameCount'] or row.get('pgnPath') != f"api/event-pgn?event={ident}&sha={package['sha256'][:16]}":
            raise ValueError('EVENT_LIBRARY_PACKAGE_COUNT_OR_LINK')


def main():
    logging.getLogger('chess.pgn').setLevel(logging.CRITICAL)
    catalog = json.loads(CATALOG.read_text())
    events = catalog['events']
    facts, manifest = load_fact_dataset(PLAYER_GAME_FACTS, 'player-game-facts')
    by_tid = {str(e['tournamentID']):e for e in events if e.get('tournamentID')}
    by_name_date = collections.defaultdict(list)
    for row in events:
        if not row.get('tournamentID'):
            for name in {row.get('name'),row.get('displayName'),*(row.get('aliases') or [])} - {None,''}:
                by_name_date[(name,row.get('date') or '')].append(row)
    report_path = ROOT / 'data/generated/event-completeness-report.json'
    report = json.loads(report_path.read_text()) if report_path.exists() else {}
    isolated = {str(e['tournamentID']) for e in report.get('events', []) if e.get('resultsStatus') == 'partial'}
    archive_rows = {}
    fingerprints = collections.defaultdict(set)
    accepted = {}; errors = collections.Counter(); assets = {}
    # Group by asset to read each source once; discard it when finished.
    for fact in facts:assets.setdefault(fact['assetPath'],[]).append(fact)
    for asset, rows in sorted(assets.items()):
        path = ROOT / asset
        games = pgn.split_pgn_games(path.read_text(encoding='utf-8-sig'))
        for fact in rows:
            index = fact['gameIndex']
            if index >= len(games):raise RuntimeError('EVENT_LIBRARY_GAME_INDEX:'+asset)
            game = pgn.repair_pgn_text(games[index])
            if pgn.stable_game_hash(game) != fact['gameSha256']:
                raise RuntimeError('EVENT_LIBRARY_GAME_HASH:'+asset)
            if not replayable(fact, game):errors['emptyOrInvalidMainline']+=1;continue
            game = annotate_pgn(game, fact.get('quality') or inspect_game(game))
            refs = []
            origins = fact.get('provenance') or [fact]
            for origin in origins:
                tid = str(origin.get('tournamentID') or '')
                if tid:
                    row = by_tid.get(tid)
                    # Partial structured events remain isolated by the existing gate.
                    if row and tid not in isolated:refs.append(row)
                else:
                    refs.extend(by_name_date.get((origin.get('event') or fact.get('event'),origin.get('date') or fact.get('date') or ''),[]))
            if not refs:
                tid = str(fact.get('tournamentID') or '')
                name = fact.get('event') or '赛事档案'
                if tid in isolated or TEST_NAME_RE.search(name):
                    errors['isolatedOrTestEvent'] += 1
                    continue
                # Valid PGNs remain discoverable even without a results page or
                # a registry member. Unknown dates are never guessed.
                key = 'tnr|' + tid if tid else name + '|' + (fact.get('date') or '')
                if key not in archive_rows:
                    ident = 'archive-' + hashlib.sha256(key.encode()).hexdigest()[:20]
                    info = describe(name, fact.get('date') or '')
                    archive_rows[key] = {'id':ident, 'name':name, 'displayName':name,
                        'date':fact.get('date'), 'year':info['year'],
                        'series':info['series'] or 'archive', 'seriesLabel':info['seriesLabel'] or '棋谱档案',
                        'detailStatus':'missing-detail', 'gameCount':0}
                    events.append(archive_rows[key])
                refs = [archive_rows[key]]
                errors['independentArchiveGames'] += 1
            fid = fact['id']; accepted[fid]=(game,fact)
            for row in refs:fingerprints[row['id']].add(fid)
    for row in events:row['gameCount']=len(fingerprints[row['id']])
    editions=build_editions(events,fingerprints)
    PACKAGES.mkdir(parents=True,exist_ok=True)
    expected=set(); packages={}
    events_by_id = {row['id']:row for row in events}
    for row in [*events,*editions]:
        ids=fingerprints[row['id']]
        if not ids:continue
        texts=[]
        labels = {}
        for child_id in row.get('sections', []):
            label = events_by_id[child_id].get('groupLabel')
            if label:
                for fid in fingerprints[child_id]:
                    labels.setdefault(fid, label)
        for fid in sorted(ids,key=lambda k:(accepted[k][1].get('date') or '',accepted[k][1].get('round') or '',k)):
            game,fact=accepted[fid]
            if row.get('isEdition'):
                label=describe(fact.get('event') or '',fact.get('date') or '')['groupLabel']
                if not label:
                    label=labels.get(fid, '')
                if label:game='[Section "'+label.replace('"','')+'"]\n'+game
            texts.append(game)
        # Put Event first for PGN readers that use it as the record boundary.
        text='\n\n'.join(texts).rstrip()+'\n'
        text=re.sub(r'\[Section "([^"\n]*)"\]\n(\[Event [^\n]*\])',r'\2\n[Section "\1"]',text)
        object_id='event-'+hashlib.sha256(row['id'].encode()).hexdigest()[:24]
        path=PACKAGES/(object_id+'.pgn');expected.add(path);path.write_text(text,encoding='utf-8')
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        packages[object_id]={'fileName':path.name,'sha256':digest,'bytes':path.stat().st_size,'gameCount':len(ids)}
        row['pgnPath']=f'api/event-pgn?event={object_id}&sha={digest[:16]}'
        if any(any(o.get('source')=='Lichess Broadcasts' for o in (accepted[fid][1].get('provenance') or [accepted[fid][1]])) for fid in ids):
            row.update(attribution='Lichess Broadcasts',license='CC BY-SA 4.0',licenseURL='https://creativecommons.org/licenses/by-sa/4.0/')
    for path in PACKAGES.glob('event-*.pgn'):
        if path not in expected:path.unlink()
    catalog['editions']=editions
    catalog['totals'].update(events=len(events),editions=len(editions),playableGames=len(accepted),withPGN=sum(bool(e.get('pgnPath')) for e in editions))
    for row in editions:
        if row.get('series') and row.get('seriesLabel'):catalog['series'][row['series']]=row['seriesLabel']
    validate_catalog(catalog, packages)
    write_json(CATALOG,catalog,ensure_ascii=False,indent=2)
    write_json(PACKAGE_INDEX,{'schemaVersion':1,'snapshotId':snapshot_id(),'factSHA256':manifest['dataSha256'],'packages':packages,'diagnostics':dict(errors)},ensure_ascii=False,indent=2)
    print(json.dumps({'editions':len(editions),'playableGames':len(accepted),'packages':len(packages),'diagnostics':dict(errors)}))

if __name__=='__main__':main()
