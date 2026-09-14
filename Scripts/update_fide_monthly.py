#!/usr/bin/env python3
"""Stage the official current-month FIDE list; publish only its verified projection."""
from __future__ import annotations
import argparse
import calendar
import datetime as dt
import json
import html as html_module
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

from monthly_release import digest, publish
from source_policy import require_fide_monthly_download
from source_http import tls_context
import sync_chinese_players as registry

ROOT = Path(__file__).resolve().parents[1]
PAGE = 'https://ratings.fide.com/download_lists.phtml'
EXPORT = registry.DEFAULT_FIDE_XML_LEGACY_URL
RECEIPT = 'data/generated/fide-monthly-receipt.json'
REGISTRY = 'docs/data/registry'
SNAPSHOTS = 'data/generated/federation-snapshots'
CANDIDATES = 'data/generated/transfer-candidates.json'


def allowed(path):
    return bool(path in {RECEIPT, CANDIDATES, f'{REGISTRY}/manifest.json', f'{REGISTRY}/players.json'}
                or re.fullmatch(r'docs/data/registry/shards/fide-prefix-[0-9]{1,3}\.json', path)
                or re.fullmatch(r'data/generated/federation-snapshots/[0-9]{4}-[0-9]{2}\.json', path))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        require_fide_monthly_download(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def download(url, destination, max_bytes):
    require_fide_monthly_download(url)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                        urllib.request.HTTPSHandler(context=tls_context()))
    request = urllib.request.Request(url, headers={'User-Agent': '4CHESS-monthly-registry/1.0'})
    with opener.open(request, timeout=120) as response, destination.open('wb') as output:
        require_fide_monthly_download(response.url)
        count = 0
        for chunk in iter(lambda: response.read(1024 * 1024), b''):
            count += len(chunk)
            if count > max_bytes:
                raise RuntimeError('FIDE_DOWNLOAD_TOO_LARGE')
            output.write(chunk)
        length = response.headers.get('Content-Length')
        if length and count != int(length):
            raise RuntimeError('FIDE_DOWNLOAD_TRUNCATED')


def list_month(html, today):
    html = html_module.unescape(re.sub(r'<[^>]+>', ' ', html))
    match = re.search(r'Download\s+(' + '|'.join(calendar.month_name[1:]) + r')\s+(\d{4})\s+FRL', html, re.I)
    if not match:
        raise RuntimeError('FIDE_LIST_MONTH_UNVERIFIED')
    month = f'{int(match[2]):04d}-{list(calendar.month_name).index(match[1].title()):02d}'
    if month != today.strftime('%Y-%m'):
        raise RuntimeError(f'FIDE_LIST_NOT_CURRENT: {month}; expected {today:%Y-%m}')
    return month


def validate_archive_date(members, today):
    # Official lists can be packaged on the final day of the preceding month.
    # The download-page effective month remains the authority.
    if len(members) != 1:
        raise RuntimeError('FIDE_ARCHIVE_MEMBERS_INVALID')
    packed = dt.date(*members[0].date_time[:3])
    if not today.replace(day=1) - dt.timedelta(days=2) <= packed <= today + dt.timedelta(days=1):
        raise RuntimeError('FIDE_ARCHIVE_DATE_STALE')


def collect(release):
    if release.exists():
        raise RuntimeError('RELEASE_OUTPUT_EXISTS')
    base = subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip()
    today = dt.datetime.now(dt.timezone.utc).date()
    with tempfile.TemporaryDirectory(prefix='fide-monthly-') as temporary:
        scratch = Path(temporary)
        page, export = scratch/'page.html', scratch/'players_list_xml_legacy.zip'
        download(PAGE, page, 2 * 1024 * 1024)
        month = list_month(page.read_text(), today)
        download(EXPORT, export, 96 * 1024 * 1024)
        registry.validate_fide_archive(export)
        with zipfile.ZipFile(export) as archive:
            members = [m for m in archive.infolist() if m.filename.lower().endswith('.xml')]
            validate_archive_date(members, today)
        download(PAGE, page, 2 * 1024 * 1024)
        if list_month(page.read_text(), dt.datetime.now(dt.timezone.utc).date()) != month:
            raise RuntimeError('FIDE_LIST_CHANGED_DURING_DOWNLOAD')
        stage = scratch/'stage'
        old_snapshots = ROOT/SNAPSHOTS
        if old_snapshots.exists():
            shutil.copytree(old_snapshots, stage/SNAPSHOTS)
        if (ROOT/CANDIDATES).exists():
            (stage/CANDIDATES).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT/CANDIDATES, stage/CANDIDATES)
        subprocess.run([sys.executable, str(ROOT/'Scripts/sync_chinese_players.py'),
                        '--input', str(export), '--output-root', str(stage/REGISTRY),
                        '--previous-registry', str(ROOT/REGISTRY),
                        '--snapshot-dir', str(stage/SNAPSHOTS),
                        '--transfer-candidates', str(stage/CANDIDATES)], cwd=ROOT, check=True)
        manifest_path = stage/REGISTRY/'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['listDate'] = month + '-01'
        manifest['source']['sha256'] = digest(export)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
        subprocess.run([sys.executable, str(ROOT/'Scripts/validate_registry_release.py'),
                        '--registry', str(stage/REGISTRY), '--corrections',
                        str(ROOT/'data/community/name-corrections.csv')], cwd=ROOT, check=True)
        old = {p['fideID']:p for p in json.loads((ROOT/REGISTRY/'players.json').read_text())}
        new = json.loads((stage/REGISTRY/'players.json').read_text())
        for field in ('standard', 'rapid', 'blitz'):
            previous_count = sum(bool(p.get(field)) for p in old.values())
            current_count = sum(bool(p.get(field)) for p in new)
            if previous_count and current_count < previous_count * 0.8:
                raise RuntimeError(f'FIDE_RATING_COVERAGE_REGRESSION: {field}')
            if any(p.get(field) is not None and not 0 <= p[field] <= 4000 for p in new):
                raise RuntimeError(f'FIDE_RATING_OUT_OF_RANGE: {field}')
        changed = {field: sum(p.get(field) != old.get(p['fideID'],{}).get(field) for p in new)
                   for field in ('standard','rapid','blitz')}
        receipt = {'schemaVersion':1, 'listMonth':month, 'inputCommit':base,
                   'sourceURL':EXPORT, 'sourceSHA256':digest(export), 'sourceBytes':export.stat().st_size,
                   'sourcePublishedMonthVerified':True, 'zipCRCVerified':True,
                   'players':len(new), 'ratingChanges':changed,
                   'registrySHA256':digest(stage/REGISTRY/'players.json')}
        (stage/RECEIPT).parent.mkdir(parents=True, exist_ok=True)
        (stage/RECEIPT).write_text(json.dumps(receipt, indent=2)+'\n')
        files = []
        release.mkdir(parents=True)
        for source in sorted(stage.rglob('*')):
            if not source.is_file():
                continue
            name = source.relative_to(stage).as_posix()
            if not allowed(name):
                raise RuntimeError(f'FIDE_UNEXPECTED_OUTPUT: {name}')
            target = release/name; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            prior = ROOT/name
            files.append({'path':name,'operation':'upsert','sha256':digest(source),
                          'bytes':source.stat().st_size,'baseSha256':digest(prior) if prior.is_file() else None})
        present = {r['path'] for r in files}
        for old_path in (ROOT/REGISTRY/'shards').glob('fide-prefix-*.json'):
            name = old_path.relative_to(ROOT).as_posix()
            if name not in present:
                files.append({'path':name,'operation':'delete','baseSha256':digest(old_path)})
        (release/'release.json').write_text(json.dumps({'schemaVersion':1,'inputCommit':base,'files':files},indent=2)+'\n')
        print(json.dumps(receipt))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['collect','publish'])
    parser.add_argument('--release', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'collect':
        collect(args.release.resolve())
    else:
        publish(args.release.resolve(), root=ROOT, allowed=allowed, receipt=RECEIPT,
                message='Update official FIDE monthly registry')


if __name__ == '__main__':
    main()
