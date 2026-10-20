"""A data-only successor may publish only CI-tested code and the same policy."""
import json
import os
import subprocess
import time


def require(api, expected):
    for run in api('actions/workflows/ci.yml/runs?branch=main&status=completed&per_page=20')['workflow_runs']:
        if run.get('conclusion') != 'success':
            continue
        sha = run['head_sha']
        tree = {row['path']: row['sha'] for row in api(f'git/trees/{sha}')['tree']}
        if any(tree.get(name) != expected[name] for name in ('Scripts', '.github')):
            continue
        path = 'data/manual/brilliancies/auto-policy.json'
        if api(f'contents/{path}?ref={sha}').get('sha') == expected['policy']:
            return sha
    return None


def main():
    repo = os.environ['GITHUB_REPOSITORY']
    cache = {}
    def api(path):
        if not path.startswith('actions/') and path in cache:
            return cache[path]
        value = json.loads(subprocess.check_output(['gh', 'api', f'repos/{repo}/{path}']))
        if not path.startswith('actions/'):
            cache[path] = value
        return value
    def oid(path):
        return subprocess.check_output(['git', 'rev-parse', f'HEAD:{path}'], text=True).strip()
    expected = {'Scripts': oid('Scripts'), '.github': oid('.github'),
                'policy': oid('data/manual/brilliancies/auto-policy.json')}
    for attempt in range(40):
        sha = require(api, expected)
        if sha:
            print(json.dumps({'ciVerifiedCodeCommit': sha}))
            return
        if attempt < 39:
            time.sleep(15)
    raise RuntimeError('BRILLIANCY_PUBLICATION_REQUIRES_PASSING_CODE_CI')


if __name__ == '__main__':
    main()
