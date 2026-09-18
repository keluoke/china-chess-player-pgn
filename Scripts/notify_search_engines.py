#!/usr/bin/env python3
"""Notify IndexNow only of verified deployed semantic changes; receipt is not indexing proof."""
import argparse, json, subprocess, tempfile
from pathlib import Path
from urllib.parse import urlparse
ORIGIN='https://chessdb.aigclabs.cc'
ROOT=Path(__file__).resolve().parents[1]

def changed_urls(previous,current):
    old={p['route']:p['contentSha256'] for p in previous.get('pages',[])}
    new={p['route']:p['contentSha256'] for p in current['pages']}
    return [ORIGIN+r for r in sorted(set(old)|set(new)) if old.get(r)!=new.get(r)]

def main():
    p=argparse.ArgumentParser();p.add_argument('--previous',type=Path);p.add_argument('--output',type=Path,required=True);p.add_argument('--submit',action='store_true');p.add_argument('--retry-receipt',type=Path);a=p.parse_args()
    current=json.loads((ROOT/'docs/data/seo/manifest.json').read_text())
    previous=json.loads(a.previous.read_text()) if a.previous and a.previous.is_file() else {}
    urls=changed_urls(previous,current)
    if a.retry_receipt:
        saved=json.loads(a.retry_receipt.read_text())
        if saved.get('snapshotId')!=current['snapshotId']:raise ValueError('INDEXNOW_RETRY_SNAPSHOT_MISMATCH')
        urls=saved['changedURLs']
        if any(not url.startswith(ORIGIN+'/') for url in urls):raise ValueError('INDEXNOW_RETRY_ORIGIN_MISMATCH')
    key=(ROOT/'docs/indexnow-key.txt').read_text().strip()
    payload={'host':urlparse(ORIGIN).netloc,'key':key,'keyLocation':ORIGIN+'/indexnow-key.txt','urlList':urls}
    receipt={'snapshotId':current['snapshotId'],'changedURLs':urls,'status':'not-submitted','indexingConfirmed':False}
    if a.submit and urls:
        # Caller is the deploy workflow only AFTER ordinary-URL canaries passed.
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/'payload.json';f.write_text(json.dumps(payload))
            r=subprocess.run(['curl','-sS','--max-time','45','--retry','2','-o',str(Path(d)/'response'),'-w','%{http_code}','-H','Content-Type: application/json','--data-binary','@'+str(f),'https://api.indexnow.org/indexnow'],capture_output=True,text=True)
            receipt.update(httpStatus=r.stdout.strip(),status='received' if r.returncode==0 and r.stdout.strip() in {'200','202'} else 'retry-needed')
    elif not urls:receipt['status']='unchanged'
    a.output.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n');print(json.dumps(receipt,ensure_ascii=False))
    if receipt['status']=='retry-needed':print('::warning::IndexNow discovery notification needs retry; deployed content is unchanged.')
    # Discovery failure cannot roll back a verified production release.
if __name__=='__main__':main()
