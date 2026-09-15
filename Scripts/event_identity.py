"""Deterministic series aliases and section identities; fuzzy names never merge."""
from __future__ import annotations
import functools
import hashlib
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]

@functools.lru_cache(maxsize=1)
def series_rules():
    return json.loads((ROOT / 'data/community/event-series.json').read_text())

def classify(name):
    for rule in series_rules():
        if rule.get('exclude') and re.search(rule['exclude'], name, re.I):
            continue
        if rule['label'] in name or any(re.search(p, name, re.I) for p in rule['patterns']):
            return rule['id'], rule['label']
    return '', ''

def control(name):
    # A broadcast title may name both formats; its section identifies the game.
    scope = name.rsplit('|', 1)[-1]
    blitz = bool(re.search(r'\bblitz\b|超快', scope, re.I))
    rapid = bool(re.search(r'\brapid\b|(?<!超)快棋', scope, re.I))
    if blitz and rapid:return 'mixed'
    if blitz:return 'blitz'
    if rapid:return 'rapid'
    return 'standard'

def section(name):
    # Specific master groups must win over their shared master suffix.
    labels = ['女子候补棋协大师组','男子候补棋协大师组','女子候补大师组','男子候补大师组',
              '女子一级棋士A组','女子一级棋士B组','男子一级棋士A组','男子一级棋士B组',
              '女子一级棋士组','男子一级棋士组','女子棋协大师组','棋协大师组']
    for label in labels:
        if label in name:
            return label.replace('候补大师', '候补棋协大师')
    female = bool(re.search(r'\bgirls?\b|\bwomen\b|女子|女性',name,re.I))
    male = bool(re.search(r'\bboys?\b|\bmen\b|男子',name,re.I))
    match = re.search(r'(?<![A-Za-z0-9])([UGBO])\s*0?(\d{1,2})(?!\d)',name,re.I)
    if not match:
        match = re.search(r'\b(under)\s*0?(\d{1,2})(?!\d)',name,re.I)
    if match:
        prefix, age = match.groups()
        female |= prefix.upper() == 'G'
        male |= prefix.upper() in {'B','O'}
        return f'U{int(age)}' + ('女子组' if female else '公开组' if male else '组')
    for token, label in [('challengers','挑战者组'),('masters','大师组'),('futures','新秀组')]:
        if re.search(r'\b'+token+r'\b', name, re.I):return label
    if female:return '女子组'
    if male or re.search(r'\bopen\b',name,re.I):return '公开组'
    group = re.search(r'\bgroup\s+([A-Z])\b',name,re.I)
    if group:return group[1].upper()+'组'
    return ''

def station(name):
    from build_event_catalog import MASTER_STATION_TRANSLATIONS
    match = re.search(r'[（(·|]\s*([^（）()|·]{1,15}站)',name)
    if match:return match[1].strip()
    if re.search(r'master tourn',name,re.I):
        for key, value in sorted(MASTER_STATION_TRANSLATIONS.items(),key=lambda x:-len(x[0])):
            if re.search(r'\b'+key+r'\b',name,re.I):return value
    return ''

def describe(name, date='', canonical=''):
    series, label = classify(name)
    year_match = re.search(r'(?<!\d)(20\d{2}|19\d{2})(?!\d)',name)
    year = year_match[1] if year_match else str(date)[:4]
    group = section(name)
    place = station(name) if series == 'chess-association-master' else ''
    # Master station is essential; never merge all stations in one year.
    safe_series = series if series != 'chess-association-master' or place else ''
    base = f'{safe_series}|{year}|{place}' if safe_series and year else ''
    if not base:
        base_name = re.sub(r'(?i)\s*[|–-]?\s*(?:round|rd\.)\s*\d+.*$', '', name).strip()
        base = 'name|'+base_name.casefold()
    title = f'{year}年{label}' + (f'（{place}）' if place else '') if safe_series and year else name
    return {'series':series,'seriesLabel':label,'year':year,'groupLabel':group,
            'timeControl':control(name),'base':base,'title':title,'station':place,'canonical':canonical}

def stable_id(value):
    return 'edition-'+hashlib.sha256(value.encode()).hexdigest()[:20]
