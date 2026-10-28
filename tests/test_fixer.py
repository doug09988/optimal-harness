"""Scripted Stage 19 verification; no network or model server."""
import asyncio
import json
import unittest
from unittest.mock import patch
from optimal_harness.engine import make_brief
from optimal_harness.model import Completion
from optimal_harness.runtime import model_side_failure
from optimal_harness.store import Store
from tests.test_harness import FakeQueue
from tests import test_runtime as runtime_tests


class FixerTests(unittest.TestCase):
    setUp = runtime_tests.RuntimeTests.setUp
    submit = runtime_tests.RuntimeTests.submit

    def setup_task(self, structured=True, protected=False):
        self.plan['stages']['FIXER']['writes'].append('test_unit.py')
        self.plan['stages']['FIXER']['deliverables'] = []
        (self.root/'wordfreq.py').write_text('old implementation\n')
        (self.root/'test_unit.py').write_text('old unit tests\n')
        self.submit()
        self.store.finish(self.store.claim('setup', 60), 'SUCCEEDED')
        plan = self.store.project('wordcounter')['plan']
        brief = make_brief('FIXER', plan, 'QA failed: repair implementation and unit tests', 'QA')
        if structured:
            brief['requested_changes'] = [
                {'path': 'test_acceptance.py' if protected else 'wordfreq.py', 'change': 'fix counts'},
                {'path': 'test_unit.py', 'change': 'fix regression'}]
        self.store.enqueue('wordcounter', 'FIXER', 1, brief)
        self.calls = []

    def attempt(self, replies):
        calls = self.calls
        class Queue(FakeQueue):
            async def request(q, prompt, cap, timeout):
                calls.append((str(prompt), cap))
                value = q.actions.popleft()
                if isinstance(value, Exception):
                    raise value
                if callable(value):
                    value = value(prompt)
                return Completion(value, 100, 80, 30)
        self.engine.queue = Queue(replies)
        with patch.object(self.engine, '_advance'):
            asyncio.run(self.engine.run_task(self.store.claim('worker', 60)))
        return self.store.tasks('wordcounter')[-1]

    def test_two_findings_exact_files_and_caps(self):
        self.setup_task()
        body = '  "quote" \\path\n---CONTENT---\n☃\r\n' * 200
        def first(prompt):
            self.assertIn('fix counts', prompt)
            self.assertIn('old implementation\n', prompt)
            self.assertNotIn('Respond with ONE next', prompt)
            return body
        task = self.attempt([first, '# corrected unit tests\n'])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertEqual((self.root/'wordfreq.py').read_bytes(), body.encode())
        self.assertEqual((self.root/'test_unit.py').read_bytes(), b'# corrected unit tests\n')
        self.assertEqual([cap for _, cap in self.calls], [4096, 4096])
        self.assertEqual(self.engine.integrity_errors('wordcounter', self.root), [])

    def test_unfixed_continues_then_retries_only_unfixed(self):
        self.setup_task()
        task = self.attempt(['', '\x00', '# fixed second'])
        self.assertIn('fixer_action_format_exhausted:unfixed', task['failure'])
        self.assertTrue(model_side_failure(task['failure']))
        self.assertEqual(task['status'], 'QUEUED')
        self.assertEqual((self.root/'test_unit.py').read_text(), '# fixed second')
        task = self.attempt(['# fixed first'])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertEqual(len(self.calls), 4)

    def test_resume_after_first_item(self):
        self.setup_task()
        task = self.attempt(['# first', RuntimeError('model_request_killed_on_deadline')])
        self.assertEqual(task['status'], 'QUEUED')
        self.engine.store = Store(self.base/'board.db')
        task = self.attempt(['# second'])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertIn('FILE: test_unit.py', self.calls[-1][0])
        self.assertEqual((self.root/'wordfreq.py').read_text(), '# first')
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.engine.integrity_errors('wordcounter', self.root), [])

    def test_changed_target_discards_progress(self):
        self.setup_task()
        self.attempt(['# first', RuntimeError('model_request_killed_on_deadline')])
        (self.root/'wordfreq.py').write_text('# external change')
        task = self.attempt(['# fresh first', '# fresh second'])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertIn('# external change', self.calls[2][0])
        self.assertEqual((self.root/'wordfreq.py').read_text(), '# fresh first')

    def test_protected_file_refused_before_calls(self):
        self.setup_task(protected=True)
        task = self.attempt([])
        self.assertIn('QA acceptance file is uneditable', task['failure'])
        self.assertEqual(self.calls, [])
        self.assertFalse((self.root/'test_acceptance.py').exists())

    def test_fallback_plan(self):
        self.setup_task(structured=False)
        task = self.attempt(['wordfreq.py :: fix counts\ntest_unit.py :: fix regression', '# first', '# second'])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertEqual([cap for _, cap in self.calls], [512, 4096, 4096])

    def test_fallback_sanitizes_plan_without_retry(self):
        self.setup_task(structured=False)
        task = self.attempt(['README.md :: docs\n./wordfreq.py :: fix counts\n'
                             'wordfreq.py :: duplicate', '# fixed'])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertEqual(task['attempts'], 1)
        self.assertEqual([cap for _, cap in self.calls], [512, 4096])
        self.assertEqual((self.root/'wordfreq.py').read_text(), '# fixed')
        with self.store.transaction() as c:
            events = [json.loads(row[0]) for row in c.execute(
                "SELECT payload FROM events WHERE kind='plan_entry_dropped'")]
        self.assertEqual([event['path'] for event in events], ['README.md', 'wordfreq.py'])
        self.assertTrue(all(event['reason'] for event in events))

    def test_fallback_resume_skips_plan_and_applied_item(self):
        self.setup_task(structured=False)
        self.attempt(['README.md :: docs\n./wordfreq.py :: fix counts\nwordfreq.py :: duplicate\ntest_unit.py :: fix regression',
                      '# first', RuntimeError('model_request_killed_on_deadline')])
        task = self.attempt(['# second'])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertEqual([cap for _, cap in self.calls], [512, 4096, 4096, 4096])

    def test_structured_qa_record(self):
        self.setup_task(structured=False)
        (self.root/'reports').mkdir(exist_ok=True)
        (self.root/'reports/qa.json').write_text(json.dumps({'findings': [
            {'path': 'wordfreq.py', 'description': 'repair counts', 'passed': False}]}))
        task = self.attempt(['# fixed'])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertEqual([cap for _, cap in self.calls], [4096])

    def test_fallback_refuses_qa_target_and_reasks_once(self):
        self.setup_task(structured=False)
        task = self.attempt(['test_acceptance.py :: weaken test'] * 2)
        self.assertIn('fixer_action_format_exhausted:plan', task['failure'])
        self.assertIn('empty fix list', task['failure'])
        self.assertEqual([cap for _, cap in self.calls], [512, 512])
        self.assertFalse((self.root/'test_acceptance.py').exists())

    def test_fallback_rejects_runtime_metadata_target(self):
        self.setup_task(structured=False)
        def bad_plan(prompt):
            task = self.store.tasks('wordcounter')[-1]
            return 'vault/runs/' + task['id'] + '/a1/handoff.json :: override handoff'
        task = self.attempt([bad_plan, bad_plan])
        self.assertIn('fixer_action_format_exhausted:plan', task['failure'])
        self.assertTrue(model_side_failure(task['failure']))
        self.assertIn('empty fix list', task['failure'])

    def test_item_envelope_reasked_with_current_content(self):
        self.setup_task()
        def corrected(prompt):
            self.assertIn('VALIDATOR REASON:', prompt)
            self.assertIn('old implementation\n', prompt)
            return '# corrected'
        task = self.attempt(['{"action":"finish"}', corrected, '# unit'])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertEqual(len(self.calls), 3)
