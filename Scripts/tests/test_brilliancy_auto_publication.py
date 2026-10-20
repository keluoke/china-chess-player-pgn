"""Automatic release must bind chess proofs, exact R2 bases and public bytes."""
import base64
import copy
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'Scripts'))
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import brilliancy_auto_publication as auto
import brilliancy_approved_store as approved
import brilliancy_quality as quality
from brilliancy_quality_backup import QualityBackup, record_key
from brilliancy_queue import R2Store, new_state
from monthly_release import validate_release, digest
from Scripts.tests.test_brilliancy_queue import FakeS3
from Scripts.tests.test_brilliancy_publication_gate import proof


class RangedS3(FakeS3):
    def head_object(self, Bucket, Key):
        raw, etag = self.objects[Key]
        return {'ContentLength': len(raw), 'ETag': etag}

    def get_object(self, Bucket, Key, Range=None, IfMatch=None):
        raw, etag = self.objects[Key]
        if IfMatch is not None and etag != IfMatch:
            raise ValueError('PRECONDITION_FAILED')
        if Range:
            start, end = map(int, Range.removeprefix('bytes=').split('-'))
            return {'ETag': etag, 'ContentRange': f'bytes {start}-{end}/{len(raw)}',
                    'Body': io.BytesIO(raw[start:end+1])}
        return super().get_object(Bucket, Key)


class AutoPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.release = self.root / 'release'
        self.client = RangedS3()
        self.key_bytes = bytes(range(32))
        self.key = base64.b64encode(self.key_bytes).decode()
        self.item = copy.deepcopy(json.loads((ROOT/'data/manual/brilliancies/curated.json').read_text())['items'][1])
        self.item['white']['playerId'] = self.item['black']['playerId'] = None
        self.item['event']['id'] = 'event-unknown'
        directory = self.root / auto.policy.PATH.parent
        directory.mkdir(parents=True)
        for name in ('auto-policy.json', 'withdrawals.json'):
            shutil.copyfile(ROOT / auto.policy.PATH.parent / name, directory / name)
        for args in [('init', '-q'), ('config','user.name','test'),
                     ('config','user.email','test@example.invalid'), ('add','data'),
                     ('commit','-qm','base')]:
            subprocess.run(['git', *args], cwd=self.root, check=True, capture_output=True)
        self.initialize()

    def initialize(self):
        gid = 'a'*64
        for lane in ('history', 'incremental'):
            for shard in (0,1):
                state = new_state(shard)
                state['activeVersion'] = 'v'
                state['baselineGames'] = [gid] if lane == 'history' else []
                if lane == 'history':
                    state['records'][gid] = {'status':'complete','version':'v',
                        'candidates':[self.item] if shard == 0 else []}
                source=R2Store(self.client,'chess-data',shard,self.key,lane)
                if source.key in self.client.objects:source.load()
                source.save(state)
                for stage,nodes in (('first',1000000),('deep',5000000)):
                    backup = QualityBackup(self.client,'chess-data',shard,self.key,
                              stage if lane == 'history' else f'incremental-{stage}')
                    state = new_state(shard)
                    if lane == 'history' and shard == 0:
                        profile = auto.preview.PROFILES[lane][stage]
                        outcome = proof(self.item,nodes);outcome['grade']='B'
                        nonce=bytes([nodes//1000000]*12)
                        cid=self.item['id']
                        encrypted=nonce+AESGCM(self.key_bytes).encrypt(nonce,json.dumps(outcome).encode(),(cid+profile).encode())
                        state['records'][record_key(cid,profile)]={'status':'complete','version':profile,'candidates':[{
                            'candidateId':cid,'inputHash':quality.digest(self.item),'profile':profile,
                            'grade':'B','encrypted':base64.b64encode(encrypted).decode()}]}
                    if backup.store.key in self.client.objects:backup.store.load()
                    backup.store.save(state)

    def collect(self, path=None):
        return auto.collect(self.client,'chess-data',self.key,path or self.release,root=self.root)

    def install_release(self):
        shutil.copytree(self.release / auto.DIRECTORY, self.root / auto.DIRECTORY)
        subprocess.run(['git','add','data'],cwd=self.root,check=True,capture_output=True)
        subprocess.run(['git','commit','-qm','approved'],cwd=self.root,check=True,capture_output=True)

    def test_chess_proof_not_pilot_grade_or_metadata_controls_release(self):
        result=self.collect()
        self.assertEqual(result['approvedItems'],1)
        self.assertEqual(result['adjudication']['history']['A'],1)
        shutil.copytree(self.root/auto.policy.PATH.parent,self.release/auto.policy.PATH.parent,dirs_exist_ok=True)
        rows=approved.load(self.release)
        self.assertEqual((rows[0]['qualityTier'],rows[0]['white']['playerId']),('A',None))
        board,target=auto.quality.replay(self.item);board.push(target)
        self.assertEqual(rows[0]['analysisLines'][0]['fenBefore'],board.fen())
        self.assertNotIn('encrypted',json.dumps(rows))

    def test_semantically_identical_reencrypted_inputs_do_not_republish(self):
        self.collect(); self.install_release()
        self.assertTrue(auto.requires_rebuild(self.release))
        previous=(self.root/auto.RECEIPT).read_bytes()
        # Different outer ciphertext/ETags, identical candidate bodies/proofs.
        self.initialize()
        again=self.root/'again';self.collect(again)
        self.assertFalse(auto.requires_rebuild(again))
        self.assertEqual(previous,(again/auto.RECEIPT).read_bytes())
        for path in (self.root/auto.DIRECTORY).glob('??.json'):
            self.assertEqual(path.read_bytes(),(again/auto.DIRECTORY/path.name).read_bytes())

    def test_withdrawal_retains_tombstone(self):
        self.collect();self.install_release()
        (self.root/'data/manual/brilliancies/withdrawals.json').write_text(json.dumps({'schemaVersion':1,'ids':[self.item['id']]}))
        output=self.root/'withdrawn';result=self.collect(output)
        self.assertEqual((result['approvedItems'],result['withdrawnItems']),(0,1))
        bucket=output/auto.DIRECTORY/(self.item['id'][3:5]+'.json')
        self.assertEqual(json.loads(bucket.read_text())['items'][0]['status'],'withdrawn')

    def test_code_policy_change_blocks_collection(self):
        path=self.root/auto.policy.PATH;value=json.loads(path.read_text());value['gateCodeSha256']='0'*64
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,'POLICY_CODE_CHANGED'):self.collect()
        self.assertFalse(self.release.exists())

    def test_changed_r2_baseline_aborts_before_artifact(self):
        original=self.client.head_object
        calls=0
        def head(**kwargs):
            nonlocal calls
            calls+=1
            response=original(**kwargs)
            if calls==25:response['ETag']='changed'
            return response
        self.client.head_object=head
        with self.assertRaisesRegex(ValueError,'BASELINE_CHANGED'):self.collect()
        self.assertFalse(self.release.exists())

    def test_manifest_baseline_conflict_and_public_tampering_are_rejected(self):
        self.collect()
        manifest=json.loads((self.release/'release.json').read_text())
        base=manifest['inputCommit']
        destination=self.root/auto.RECEIPT;destination.parent.mkdir(parents=True);destination.write_text('{}')
        with self.assertRaisesRegex(RuntimeError,'BASE_CONFLICT'):
            validate_release(self.release,base,root=self.root,allowed=auto.allowed,receipt=auto.RECEIPT)
        shutil.copytree(self.root/auto.policy.PATH.parent,self.release/auto.policy.PATH.parent,dirs_exist_ok=True)
        file=self.release/auto.DIRECTORY/(self.item['id'][3:5]+'.json')
        payload=json.loads(file.read_text());payload['items'][0]['title']='tampered';file.write_text(json.dumps(payload))
        index=self.release/auto.RECEIPT;value=json.loads(index.read_text())
        value['files'][0].update(bytes=file.stat().st_size,sha256=digest(file));index.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,'PUBLIC_HASH_MISMATCH'):approved.load(self.release)

    def test_short_r2_range_cannot_be_used_as_checkpoint(self):
        original=self.client.get_object
        def short(**kwargs):
            response=original(**kwargs);response['Body']=io.BytesIO(b'bad');return response
        self.client.get_object=short
        with self.assertRaises(ConnectionError):self.collect()


if __name__=='__main__':unittest.main()
