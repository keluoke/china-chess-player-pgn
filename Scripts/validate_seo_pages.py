#!/usr/bin/env python3
"""Fail closed on stale templates, mixed snapshots, unsafe paths or incorrect HTML."""
from __future__ import annotations
import argparse, hashlib, json, re
from pathlib import Path, PurePosixPath
from html.parser import HTMLParser
from xml.etree import ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
ORIGIN='https://chessdb.aigclabs.cc'

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def safe_file(value):
    p=PurePosixPath(value)
    if p.is_absolute() or '..' in p.parts or not re.fullmatch(r'[a-zA-Z0-9_./-]+',value):raise ValueError('SEO_UNSAFE_PATH')
    if not (value in {'sitemap.xml','llms.txt','index.html','events.html','master-series.html','leaderboards.html','about.html','methodology.html','developers.html'} or re.fullmatch(r'(?:players|events|leaderboards|master-series)/[a-zA-Z0-9_/-]+\.html',value)):raise ValueError('SEO_UNEXPECTED_OUTPUT')
    return p

class Document(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.canonical=[];self.h1=0;self.robots=[];self.sid=[];self.links=[];self.ld=[];self.in_ld=False;self.script=''
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='link' and a.get('rel')=='canonical': self.canonical.append(a.get('href'))
        if tag=='h1':self.h1+=1
        if tag=='meta' and a.get('name')=='robots':self.robots.append(a.get('content',''))
        if tag=='meta' and a.get('name')=='chessdb-snapshot':self.sid.append(a.get('content'))
        if tag=='a':self.links.append(a.get('href',''))
        if tag=='script' and a.get('type')=='application/ld+json':self.in_ld=True;self.script=''
    def handle_data(self,data):
        if self.in_ld:self.script+=data
    def handle_endtag(self,tag):
        if tag=='script' and self.in_ld:self.ld.append(json.loads(self.script));self.in_ld=False

def validate(root=ROOT,seo_root=None,expected_snapshot=None,check_snapshot=True,check_inputs=True):
    docs=root/'docs';seo=seo_root or docs/'data/seo'; manifest=json.loads((seo/'manifest.json').read_text())
    sid=expected_snapshot or json.loads((docs/'data/snapshot.json').read_text())['snapshotId']
    if manifest.get('snapshotId')!=sid or manifest.get('origin')!=ORIGIN:raise ValueError('SEO_SNAPSHOT_MISMATCH')
    if check_inputs and manifest.get('builderSha256')!=sha(Path(__file__).with_name('build_seo_pages.py')):raise ValueError('SEO_BUILDER_REBUILD_REQUIRED')
    for group in (('templates','assets') if check_inputs else ()):
        for name,expected in manifest[group].items():
            if '/' in name or sha(docs/name)!=expected:raise ValueError('SEO_TEMPLATE_REBUILD_REQUIRED: '+name)
    files=manifest['files'];pages=manifest['pages'];paths=[f['file'] for f in files]
    if len(paths)!=len(set(paths)) or len(pages)>500:raise ValueError('SEO_OUTPUT_BUDGET_OR_DUPLICATE')
    actual={p.relative_to(seo/'output').as_posix() for p in (seo/'output').rglob('*') if p.is_file()}
    if actual!=set(paths):raise ValueError('SEO_OUTPUT_INVENTORY_MISMATCH')
    for f in files:
        path=seo/'output'/safe_file(f['file'])
        if path.is_symlink() or not path.resolve().is_relative_to((seo/'output').resolve()) or sha(path)!=f['sha256'] or path.stat().st_size!=f['bytes']:raise ValueError('SEO_OUTPUT_HASH_MISMATCH')
    routes={p['route'] for p in pages}
    if len(routes)!=len(pages):raise ValueError('SEO_DUPLICATE_ROUTE')
    for p in pages:
        if p['file'] != ('index.html' if p['route']=='/' else p['route'].strip('/')+'.html') or p['file'] not in paths or not p['file'].endswith('.html'):raise ValueError('SEO_PAGE_MISSING')
        path=seo/'output'/p['file']; raw=path.read_text();doc=Document();doc.feed(raw)
        if sha(path)!=p['sha256'] or path.stat().st_size!=p['bytes']:raise ValueError('SEO_PAGE_HASH_MISMATCH')
        if doc.canonical!=[ORIGIN+p['route']] or doc.h1!=1 or doc.sid!=[sid] or not doc.ld:raise ValueError('SEO_HTML_CONTRACT: '+p['route'])
        if any('noindex' in r or 'nofollow' in r for r in doc.robots):raise ValueError('SEO_PAGE_NOINDEX')
        if any(x in raw.lower() for x in ('chess-results.com','/volumes/','sourcerefs','sourcechinesename','scripts/local/')):raise ValueError('SEO_PRIVATE_CONTENT')
        for link in doc.links:
            if link.startswith(('javascript:','data:','//')):raise ValueError('SEO_UNSAFE_LINK')
            clean=link.split('?')[0].split('#')[0]
            if clean.startswith(('/players/','/events/','/master-series/','/leaderboards/')) and clean not in routes:raise ValueError('SEO_DANGLING_ENTITY_LINK: '+clean)
    sitemap=ET.parse(seo/'output/sitemap.xml'); locs=[e.text for e in sitemap.findall('.//{*}loc')]
    if len(locs)!=len(routes) or set(locs)!={ORIGIN+r for r in routes}:raise ValueError('SEO_SITEMAP_MISMATCH')
    for mapping in manifest['routes'].values():
        if any(v not in routes for v in mapping.values()):raise ValueError('SEO_ROUTE_MAP_MISMATCH')
    if check_snapshot:
        snapshot=json.loads((docs/'data/snapshot.json').read_text());refs=[r for r in snapshot.get('outputs',[]) if r['path']=='docs/data/seo/manifest.json']
        if len(refs)!=1 or refs[0].get('sha256')!=sha(seo/'manifest.json'):raise ValueError('SEO_MANIFEST_NOT_CERTIFIED')
    return manifest

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=ROOT);a=p.parse_args();m=validate(a.root);print(f"SEO verified: {len(m['pages'])} pages, snapshot {m['snapshotId']}")
