"""Offline result disputes: stable pairing identity and hash-bound decisions.

Decisions never rewrite archive Result tags. Evidence is reviewed local material;
no source network access occurs here. Stale or unmatched decisions fail closed.
"""
from __future__ import annotations
import csv
import hashlib
import json
import pathlib
import re
import unicodedata

FINISHED = {'1-0', '0-1', '1/2-1/2'}

def normalize_result(value):
    value = re.sub(r'\s+', '', str(value or '')).replace('–', '-').replace('½', '1/2')
    return {'0.5-0.5': '1/2-1/2', '1:0': '1-0', '0:1': '0-1', '1/2': '1/2-1/2'}.get(value, value)

def name(value):
    value = unicodedata.normalize('NFKD', str(value or '')).casefold()
    return ''.join(c for c in value if c.isalnum())

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def conflict(fact, context):
    candidates = []
    for (round_no, board), pairing in (context.get('pairings') or {}).items():
        if str(round_no) != str(fact.get('round', '')):
            continue
        if fact.get('board') and str(board) != str(fact['board']):
            continue
        white = name((pairing.get('white') or {}).get('name'))
        black = name((pairing.get('black') or {}).get('name'))
        names = (name(fact.get('white')), name(fact.get('black')))
        if not white or not black or not all(names):
            continue
        if names == (white, black):
            reverse = False
        elif names == (black, white):
            reverse = True
        else:
            continue
        candidates.append((str(round_no), str(board), pairing, reverse))
    if len(candidates) != 1:
        return None
    round_no, board, pairing, reverse = candidates[0]
    table_result = normalize_result(pairing.get('result'))
    oriented = {'1-0':'0-1', '0-1':'1-0'}.get(table_result, table_result) if reverse else table_result
    pgn_result = normalize_result(fact.get('result'))
    if oriented not in FINISHED or pgn_result not in FINISHED or oriented == pgn_result:
        return None
    identity = [str(fact.get('tournamentID')), round_no, board,
                str((pairing.get('white') or {}).get('playerNo', '')),
                str((pairing.get('black') or {}).get('playerNo', ''))]
    issue = {'issueID': 'result-' + digest(identity)[:24],
             'tournamentID': identity[0], 'round': round_no, 'board': board,
             'gameSha256': fact['gameSha256'], 'tableResult': table_result,
             'tableResultInPgnOrientation': oriented, 'pgnResult': pgn_result,
             'orientation': 'reversed' if reverse else 'same', 'status': 'pending'}
    issue['bindingSha256'] = digest(issue)
    return issue

def load_decisions(root: pathlib.Path):
    path = root / 'data/community/game-result-decisions.csv'
    if not path.exists():
        return {}
    decisions = {}
    for row in csv.DictReader(path.open(encoding='utf-8')):
        key = row.get('issue_id', '').strip()
        if not key or key in decisions or row.get('decision') not in {'accept-table', 'accept-pgn'}:
            raise ValueError('RESULT_DECISION_INVALID_OR_DUPLICATE: ' + key)
        for field in ('binding_sha256', 'evidence_sha256'):
            if not re.fullmatch('[0-9a-f]{64}', row.get(field, '')):
                raise ValueError('RESULT_DECISION_HASH_INVALID: ' + key)
        evidence = (root / row.get('evidence_path', '')).resolve()
        evidence_root = (root / 'data/manual/result-evidence').resolve()
        if not evidence.is_relative_to(evidence_root) or not evidence.is_file():
            raise ValueError('RESULT_DECISION_EVIDENCE_MISSING: ' + key)
        if hashlib.sha256(evidence.read_bytes()).hexdigest() != row['evidence_sha256']:
            raise ValueError('RESULT_DECISION_EVIDENCE_HASH: ' + key)
        if not row.get('reviewer', '').strip() or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', row.get('reviewed_at', '')):
            raise ValueError('RESULT_DECISION_REVIEWER_REQUIRED: ' + key)
        decisions[key] = row
    return decisions

def annotate(facts, contexts, root):
    decisions = load_decisions(root)
    used = set()
    issues = []
    for fact in facts:
        issue = conflict(fact, contexts.get(str(fact.get('tournamentID')), {}))
        if issue is None:
            continue
        quality = fact['quality']
        quality.update(resultStatus='disputed', resultStatsEligible=False, issueID=issue['issueID'])
        decision = decisions.get(issue['issueID'])
        if decision:
            if decision['binding_sha256'] != issue['bindingSha256']:
                raise ValueError('RESULT_DECISION_STALE: ' + issue['issueID'])
            used.add(issue['issueID'])
            effective = issue['tableResultInPgnOrientation'] if decision['decision'] == 'accept-table' else issue['pgnResult']
            quality.update(resultStatus='reviewed', effectiveResult=effective,
                           resultStatsEligible=quality['replayable'] and effective in FINISHED)
            issue.update(status='resolved', decision=decision['decision'], effectiveResult=effective)
        issues.append(issue)
    if set(decisions) != used:
        raise ValueError('RESULT_DECISION_UNMATCHED: ' + ','.join(sorted(set(decisions)-used)))
    return issues

def annotate_pgn(text, quality):
    if quality.get('resultStatus') not in {'disputed', 'reviewed'}:
        return text
    fields = {'ResultStatus': quality['resultStatus'], 'ResultStatsEligible': str(quality['resultStatsEligible']).lower(),
              'ResultIssueID': quality['issueID']}
    if quality.get('effectiveResult'):
        fields['ReviewedResult'] = quality['effectiveResult']
    # Insert next to headers, never rewrite original result or movetext.
    lines = text.splitlines()
    index = next((i for i, line in enumerate(lines) if line.strip().startswith('[')), 0) + 1
    lines[index:index] = [f'[{key} "{value}"]' for key, value in fields.items()]
    return '\n'.join(lines)
