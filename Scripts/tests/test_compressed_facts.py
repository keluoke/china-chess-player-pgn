import gzip
import hashlib
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from stable_json import write_json_gzip
from canonical_player_facts import load_fact_dataset
import build_player_facts as builder

class CompressedFactsTest(unittest.TestCase):
    def test_gzip_bytes_ignore_time_and_preserve_generated_at(self):
        with tempfile.TemporaryDirectory() as directory:
            path=pathlib.Path(directory)/'facts.json.gz'
            first={'generatedAt':'2026-09-01', 'facts':[{'id':'棋局'}]}
            write_json_gzip(path,first)
            original=path.read_bytes()
            self.assertFalse(write_json_gzip(path,{**first,'generatedAt':'2026-09-02'}))
            self.assertEqual(path.read_bytes(),original)
            self.assertEqual(json.loads(gzip.decompress(original)),first)
            self.assertEqual(original[4:8],b'\0'*4)

    def test_writer_removes_obsolete_large_json_and_reader_checks_compressed_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory)
            (root/'facts.json').write_text('{}')
            with mock.patch.object(builder,'snapshot_id',return_value='compressed-fixture'), \
                 mock.patch.dict('os.environ', {'SNAPSHOT_ID':'compressed-fixture'}):
                manifest=builder.write_dataset(root,'player-game-facts',[{'id':'1'}],[],{})
                self.assertEqual(manifest['dataFile'],'facts.json.gz')
                self.assertFalse((root/'facts.json').exists())
                facts,_=load_fact_dataset(root/'manifest.json','player-game-facts')
                self.assertEqual(facts,[{'id':'1'}])
                (root/'facts.json.gz').write_bytes(b'corrupt')
                with self.assertRaisesRegex(RuntimeError,'FACT_DATA_HASH_MISMATCH'):
                    load_fact_dataset(root/'manifest.json','player-game-facts')
