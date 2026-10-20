"""A deployed brilliancy list/detail/PGN must all belong to one snapshot."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest
import tempfile
from unittest.mock import patch
from urllib.parse import urlparse

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'Scripts'))
import verify_product_canary as canary
import build_release_snapshot as snapshot_builder


class BrilliancyOnlineCanaryTests(unittest.TestCase):
    def setUp(self):
        self.cid='br-'+'a'*64
        self.pgn='[Event "Synthetic canary"]\n[Result "*"]\n\n1. e4 e5 *\n'
        item={'id':self.cid,'status':'published','snapshotId':'snapshot','qualityTier':'S'}
        self.data={
            '/data/brilliancies/manifest.json':{'snapshotId':'snapshot','total':1,'shardPrefixLength':2,'pgnLayout':'shards'},
            '/data/brilliancies/items.json':[item],
            '/data/brilliancies/shards/aa.json':{'snapshotId':'snapshot','items':{self.cid:item},'pgn':{self.cid:self.pgn}},
            '/api/v1/brilliancies':{'snapshotId':'snapshot','total':1,'items':[item]},
            f'/api/v1/brilliancies/{self.cid}.json':item,
        }
        self.snapshot={'snapshotId':'snapshot','outputs':[{'path':'docs'+path,
            'bytes':len(json.dumps(value).encode()),'sha256':hashlib.sha256(json.dumps(value).encode()).hexdigest()}
            for path,value in self.data.items() if path.startswith('/data/')]}

    def fetch(self,url,**kwargs):
        parsed=urlparse(url)
        if 'snapshot=invalid-snapshot' in parsed.query:
            self.assertEqual(kwargs['expected'],409);return b'{}',{}
        if parsed.path.endswith('.pgn'):
            return self.pgn.encode(),{'content-type':'application/x-chess-pgn'}
        return json.dumps(self.data[parsed.path]).encode(),{'content-type':'application/json'}

    def test_checks_full_packed_public_path(self):
        with patch.object(canary,'fetch',side_effect=self.fetch):
            result=canary.check_brilliancies('https://example.invalid',self.snapshot)
        self.assertEqual((result['published'],result['sampleTiers']),(1,['S']))

    def test_corrupt_static_or_api_body_is_not_accepted_as_deployed(self):
        self.data['/data/brilliancies/items.json'][0]=copy.deepcopy(self.data['/data/brilliancies/items.json'][0])
        self.data['/data/brilliancies/items.json'][0]['title']='changed'
        with patch.object(canary,'fetch',side_effect=self.fetch),self.assertRaisesRegex(ValueError,'BODY'):
            canary.check_brilliancies('https://example.invalid',self.snapshot)

    def test_unified_snapshot_certifies_actual_list_shard_and_pgn_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            payloads={'docs/data/brilliancies/items.json':b'[]\n',
                      'docs/data/brilliancies/lists/index.json':b'{"rows":[]}\n',
                      'docs/data/brilliancies/shards/aa.json':b'{"items":{}}\n',
                      'docs/data/brilliancies/pgn/example.pgn':self.pgn.encode()}
            for name,body in payloads.items():
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(body)
            with patch.object(snapshot_builder,'ROOT',root):
                facts={row['path']:row for row in snapshot_builder.output_facts()}
            for name,body in payloads.items():
                self.assertEqual((facts[name]['bytes'],facts[name]['sha256']),
                                 (len(body),hashlib.sha256(body).hexdigest()))


if __name__=='__main__':unittest.main()
