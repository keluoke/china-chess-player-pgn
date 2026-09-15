import csv
import hashlib
import pathlib
import sys
import tempfile
import unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import result_review as review
from game_quality import inspect_game, coverage_contract
from verify_product_canary import check_pgn

PGN = '[Event "Test"]\n[White "Alpha"]\n[Black "Beta"]\n[Result "1-0"]\n\n1. e4 e5 1-0'

def fixtures():
    fact = {'tournamentID':'1059818', 'round':'1', 'board':'62', 'white':'Alpha', 'black':'Beta',
            'gameSha256':hashlib.sha256(PGN.encode()).hexdigest(), 'result':'1-0', 'quality':inspect_game(PGN)}
    pairing = {'white':{'name':'Alpha','playerNo':'1'}, 'black':{'name':'Beta','playerNo':'2'}, 'result':'0-1'}
    return fact, {'1059818': {'pairings': {('1','62'):pairing}}}

class ResultReviewTest(unittest.TestCase):
    def test_pending_does_not_guess_result_or_modify_original(self):
        fact, contexts = fixtures()
        with tempfile.TemporaryDirectory() as directory:
            issues = review.annotate([fact], contexts, pathlib.Path(directory))
        self.assertEqual(issues[0]['status'], 'pending')
        self.assertEqual(fact['result'], '1-0')
        self.assertFalse(fact['quality']['resultStatsEligible'])
        projected = review.annotate_pgn(PGN, fact['quality'])
        self.assertIn('[Result "1-0"]', projected)
        self.assertIn('[ResultStatus "disputed"]', projected)
        self.assertEqual(check_pgn(projected.encode(), 1), 1)

    def test_reversed_sides_are_not_a_false_dispute(self):
        fact, contexts = fixtures()
        fact.update(white='Beta', black='Alpha')
        self.assertIsNone(review.conflict(fact, contexts['1059818']))

    def test_id_stable_but_binding_changes_with_evidence(self):
        fact, contexts = fixtures()
        first = review.conflict(fact, contexts['1059818'])
        fact['gameSha256'] = 'a'*64
        second = review.conflict(fact, contexts['1059818'])
        self.assertEqual(first['issueID'], second['issueID'])
        self.assertNotEqual(first['bindingSha256'], second['bindingSha256'])

    def test_decision_needs_current_binding_and_real_hashed_evidence(self):
        fact, contexts = fixtures()
        issue = review.conflict(fact, contexts['1059818'])
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            evidence = root/'data/manual/result-evidence/review.txt'; evidence.parent.mkdir(parents=True)
            evidence.write_text('Reviewed offline evidence fixture')
            ledger = root/'data/community/game-result-decisions.csv'; ledger.parent.mkdir(parents=True)
            row = dict(issue_id=issue['issueID'], binding_sha256=issue['bindingSha256'], decision='accept-table',
                       evidence_path=str(evidence.relative_to(root)), evidence_sha256=hashlib.sha256(evidence.read_bytes()).hexdigest(),
                       reviewer='test', reviewed_at='2026-09-16')
            def write():
                with ledger.open('w') as stream:
                    writer=csv.DictWriter(stream,fieldnames=list(row)); writer.writeheader();writer.writerow(row)
            write(); result=review.annotate([fact],contexts,root)
            self.assertEqual(result[0]['status'],'resolved')
            self.assertEqual(fact['quality']['effectiveResult'],'0-1')
            row['binding_sha256']='0'*64; write()
            with self.assertRaisesRegex(ValueError,'RESULT_DECISION_STALE'):review.annotate([fact],contexts,root)
            evidence.write_text('changed')
            with self.assertRaisesRegex(ValueError,'RESULT_DECISION_EVIDENCE_HASH'):review.load_decisions(root)

    def test_unresolved_scope_never_full(self):
        report={'resultsStatus':'results-complete','pgnIngestStatus':'source-published-coverage-unresolved',
                'playableComplete':True,'counts':{'archivedGames':30}}
        self.assertEqual(coverage_contract(report)['code'],'unknown')

    def test_canary_rejects_count_hash_and_illegal_game(self):
        with self.assertRaisesRegex(ValueError,'CANARY_PGN_COUNT'):check_pgn(PGN.encode(),2)
        with self.assertRaisesRegex(ValueError,'CANARY_PGN_HASH'):check_pgn(PGN.encode(),1,'0'*64)
        with self.assertRaisesRegex(ValueError,'CANARY_PGN_NOT_REPLAYABLE'):check_pgn(PGN.replace('e5','Kd5').encode(),1)
