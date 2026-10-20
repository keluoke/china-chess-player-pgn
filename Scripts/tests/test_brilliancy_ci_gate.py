import sys
from pathlib import Path
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'Scripts'))
from require_brilliancy_ci import require


class BrilliancyCIGateTests(unittest.TestCase):
    def test_failed_or_different_code_cannot_authorize_publication(self):
        calls=[]
        def api(path):
            calls.append(path)
            if path.startswith('actions/'):
                return {'workflow_runs':[{'conclusion':'failure','head_sha':'failed'},
                                         {'conclusion':'success','head_sha':'older'}]}
            return {'tree':[{'path':'Scripts','sha':'old-code'},{'path':'.github','sha':'workflows'}]}
        self.assertIsNone(require(api,{'Scripts':'new-code','.github':'workflows','policy':'policy'}))
        self.assertNotIn('git/trees/failed',calls)

    def test_data_only_successor_accepts_same_tested_code_and_policy(self):
        def api(path):
            if path.startswith('actions/'):
                return {'workflow_runs':[{'conclusion':'success','head_sha':'tested-code'}]}
            if path.startswith('git/'):
                return {'tree':[{'path':'Scripts','sha':'code'},{'path':'.github','sha':'workflows'}]}
            return {'sha':'policy'}
        self.assertEqual(require(api,{'Scripts':'code','.github':'workflows','policy':'policy'}),'tested-code')
        self.assertIsNone(require(api,{'Scripts':'code','.github':'workflows','policy':'changed-policy'}))


if __name__=='__main__':unittest.main()
