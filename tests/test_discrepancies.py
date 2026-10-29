"""Stage 35 deterministic accounting, including the verify-31 species fixture."""
import json
import unittest
from optimal_harness.discrepancies import current, identity, live, record, snapshot, validated
from optimal_harness.verifier import Verifier
from tests import test_runtime as runtime_tests
from tests import test_fixer as fixer_tests


class DiscrepancyTests(unittest.TestCase):
    def setUp(self):
        runtime_tests.RuntimeTests.setUp(self)
        (self.root / 'artifacts').mkdir(parents=True, exist_ok=True)
    submit = runtime_tests.RuntimeTests.submit

    def ready(self, stage, texts):
        task = self.store.claim('fixture', 60)
        with self.store.transaction() as c:
            c.execute('UPDATE tasks SET stage=? WHERE id=?', (stage, task['id']))
        self.store.handoff(task, {'status': 'ready', 'open_discrepancies': texts})
        self.store.finish(task, 'SUCCEEDED')

    def test_verify31_fixture(self):
        # 55 primary-record entries, duplicated generation errors and prose,
        # plus the one live case-sensitivity dispute; no obligations required.
        qa = ['test_empty_input_array: unwritable: reply must contain exactly the planned test function',
              'test_output_file_format: unwritable: invalid Python: closing parenthesis line 35']
        prose = ['count_words increments the count twice', 'Output sorting may be wrong']
        dispute = 'Case-sensitivity claim remains disputed'
        fixture = (qa + prose) * 13 + qa + [dispute]
        self.assertEqual(len(fixture), 55)
        self.submit()
        self.ready('QA', fixture[:28])
        plan = self.store.project('wordcounter')['plan']
        self.store.enqueue('wordcounter', 'REVIEW', 1, {})
        self.ready('REVIEW', fixture[28:])
        (self.root/'artifacts/review.json').write_text(json.dumps({'unresolved': [dispute]}))
        (self.root/'test_acceptance.py').write_text('def test_empty_input_array(): pass\ndef test_output_file_format(): pass\n')
        qa_task = next(t for t in self.store.tasks('wordcounter') if t['stage'] == 'QA')
        with self.store.transaction() as c:
            self.store.checkpoint(c, dict(qa_task, generation=1, project_id='wordcounter'), 'test_acceptance.py', 'fixture', 'fixture-suite')
        opened = snapshot(self.store, 'wordcounter', self.root, plan)
        self.assertEqual(len(opened), 5)
        self.assertEqual(len([e for e in opened if e.get('disposition') == 'STALE']), 2)
        closure = validated([
            dict(identity=identity(prose[0]), disposition='CLOSE', note='wordfreq.py: count_words increments once'),
            dict(identity=identity(dispute), disposition='KEEP', note='case behavior still disputed')],
            {e['identity'] for e in opened})
        record(self.store, 'wordcounter', closure)
        checks = self.engine._discrepancy_checks('wordcounter')
        report = Verifier(self.root).verify(checks)
        self.assertEqual(report['open_discrepancies'], 2)
        self.assertEqual(self.engine._failure_count(report['results']), 2)
        self.assertEqual(len(self.engine._fixer_brief('wordcounter', plan, '', 'QA')['discrepancy_closure']), 2)

    def test_three_handoffs_normalize_once_and_silence_stays_open(self):
        self.assertEqual(len(current({}, [{'open_discrepancies': [s]} for s in
            ['Some gap', ' SOME   GAP ', 'some gap']])), 1)
        self.assertEqual(len(live([{'identity': identity('Some gap'), 'text': 'Some gap'}])), 1)

    def test_close_without_evidence_and_unknown_identity_rejected(self):
        key = identity('gap')
        self.assertEqual(validated([dict(identity=key, disposition='CLOSE', note='  '),
            dict(identity='unknown', disposition='CLOSE', note='file.py')], {key}), [])
        self.assertEqual(validated([dict(identity=key, disposition='STALE', note='superseded by later review')],
                                   {key}, 'REVIEW')[0]['disposition'], 'STALE')

    def test_latest_stage_handoff_supersedes_old_attempt(self):
        self.submit()
        self.ready('QA', ['old gap'])
        self.store.enqueue('wordcounter', 'QA', 2, {})
        self.ready('QA', ['new gap'])
        plan = self.store.project('wordcounter')['plan']
        self.assertEqual([e['text'] for e in snapshot(self.store, 'wordcounter', self.root, plan)], ['new gap'])

    def test_old_suite_does_not_auto_close_current_qa_gap(self):
        self.submit()
        self.ready('QA', ['test_old: unwritable: invalid Python'])
        (self.root/'test_acceptance.py').write_text('def test_old(): pass\n')
        plan = self.store.project('wordcounter')['plan']
        self.assertEqual(len(live(snapshot(self.store, 'wordcounter', self.root, plan))), 1)

    def test_review_stale_response_uses_shared_ledger(self):
        from optimal_harness.review import DrivenReview
        self.submit()
        self.ready('QA', ['superseded claim'])
        review = DrivenReview('QA')
        review.sections = dict(reviewed_stage='QA', counterexamples=['ATTEMPTED: case'],
                               observations=[], unresolved=[])
        action = review.accept(json.dumps(dict(verdict='APPROVE', discrepancy_resolutions=[
            dict(identity=identity('superseded claim'), disposition='STALE', note='later review: test_case passes')])))
        entries = validated(action['discrepancy_resolutions'], {identity('superseded claim')}, 'REVIEW')
        record(self.store, 'wordcounter', entries)
        self.assertEqual(self.engine._discrepancy_checks('wordcounter'), [])


class DrivenClosureTests(unittest.TestCase):
    setUp = runtime_tests.RuntimeTests.setUp
    submit = runtime_tests.RuntimeTests.submit
    setup_task = fixer_tests.FixerTests.setup_task
    attempt = fixer_tests.FixerTests.attempt
    def test_scripted_fixer_closure(self):
        self.setup_task()
        (self.root/'artifacts/review.json').write_text(json.dumps({'unresolved': ['duplicate count']}))
        def close(prompt):
            self.assertIn('DISCREPANCY CLOSURE', prompt)
            return json.dumps([dict(identity=identity('duplicate count'), disposition='CLOSE',
                                    note='wordfreq.py: corrected count implementation')])
        task = self.attempt(['# fixed', '# tests', close])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertEqual(self.engine._discrepancy_checks('wordcounter'), [])
