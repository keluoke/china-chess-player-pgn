#!/usr/bin/env python3
"""Verify ordinary URLs, redirects and rendered source after deployment; no source capture."""
from __future__ import annotations
import argparse, hashlib, json, subprocess, tempfile
from pathlib import Path
from xml.etree import ElementTree as ET
from validate_seo_pages import Document, ORIGIN
ROOT=Path(__file__).resolve().parents[1]

def fetch(url,follow=True):
    with tempfile.TemporaryDirectory() as d:
        body=Path(d)/'body';head=Path(d)/'headers'
        cmd=['curl','-sS','--compressed','--max-time','45','--retry','2','-D',str(head),'-o',str(body),'-w','%{json}']
        if follow:cmd.append('-L')
        p=subprocess.run(cmd+[url],capture_output=True,text=True,check=True)
        meta=json.loads(p.stdout);headers=head.read_text();return meta,body.read_bytes(),headers

def verify(root=ROOT,site=ORIGIN):
    manifest=json.loads((root/'docs/data/seo/manifest.json').read_text());checks=[]
    def check(path,status=200,mime=None,expected=None):
        m,b,h=fetch(site+path)
        if m['http_code']!=status or (mime and mime not in str(m['content_type'])):raise ValueError('SEO_HTTP_MISMATCH '+path+' '+str(m['http_code']))
        if expected and hashlib.sha256(b).hexdigest()!=expected:raise ValueError('SEO_BODY_MISMATCH '+path)
        checks.append({'path':path,'status':status,'ok':True});return b,h
    for path,mime in [('/robots.txt','text/plain'),('/sitemap.xml','xml'),('/llms.txt','text/plain')]:check(path,mime=mime)
    check('/__seo_missing_page_probe__',404)
    sitemap=ET.fromstring(check('/sitemap.xml'));urls={e.text for e in sitemap.findall('.//{*}loc')}
    if urls!={ORIGIN+p['route'] for p in manifest['pages']}:raise ValueError('SEO_ONLINE_SITEMAP_MISMATCH')
    chosen=[]
    for prefix in ['/','/events','/leaderboards','/master-series','/about','/methodology','/developers']:
        chosen.extend(p for p in manifest['pages'] if p['route']==prefix)
    for prefix in ['/players/','/events/','/master-series/','/leaderboards/']:
        chosen.extend([p for p in manifest['pages'] if p['route'].startswith(prefix)][:2])
    for p in chosen:check(p['route'],mime='text/html',expected=p['sha256'])
    for kind,param in [('players','fideID'),('events','event')]:
        ident,route=next(iter(manifest['routes'][kind].items()))
        m,b,h=fetch(site+'/?'+param+'='+ident,False)
        if m['http_code'] not in (301,308) or (ORIGIN+route) not in h:raise ValueError('SEO_LEGACY_REDIRECT_MISMATCH')
        check('/?'+param+'='+ident+'&view=interactive',mime='text/html')
    for host in ['4chess.cc','china-chess-player-pgn.pages.dev']:
        m,b,h=fetch('https://'+host+'/',False)
        if m['http_code'] not in (301,308) or ORIGIN+'/' not in h:raise ValueError('SEO_ALIAS_REDIRECT_MISMATCH '+host)
        checks.append({'alias':host,'status':m['http_code'],'ok':True})
    _,h=check('/?q=seo-check',mime='text/html')
    if 'x-robots-tag: noindex' not in h.lower():raise ValueError('SEO_QUERY_NOINDEX_MISSING')
    return {'snapshotId':manifest['snapshotId'],'pages':len(manifest['pages']),'checks':checks}
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=ROOT);p.add_argument('--site',default=ORIGIN);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    result=verify(a.root,a.site);a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False))
