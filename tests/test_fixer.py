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
        body = '#  "quote" \\path\n# ---CONTENT---\n# ☃\r\n' * 200
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

    def evidence(self, task):
        path = self.root / f"vault/runs/{task['id']}/a1/evidence.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()]

    def test_unfixed_continues_with_partial_success(self):
        self.setup_task()
        task = self.attempt(['', '\x00', '```python\ndef broken(\n```', '# fixed second'])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertEqual((self.root/'wordfreq.py').read_text(), 'old implementation\n')
        self.assertEqual((self.root/'test_unit.py').read_bytes(), b'# fixed second')
        handoff = json.loads((self.root / f"vault/runs/{task['id']}/a1/handoff.json").read_text())
        self.assertEqual(len(handoff['open_discrepancies']), 1)
        self.assertIn('Item 0 (wordfreq.py)', handoff['open_discrepancies'][0])
        self.assertIn('SyntaxError: wordfreq.py: line 1', handoff['open_discrepancies'][0])
        self.assertEqual(len(self.calls), 4)
        brief = self.engine._fixer_brief('wordcounter', self.plan, 'still failing', 'QA')
        self.assertEqual(brief['requested_changes'][0]['path'], 'wordfreq.py')
        self.assertIn('1: def broken(', brief['requested_changes'][0]['change'])
        with self.store.transaction() as c:
            events = [json.loads(r[0]) for r in c.execute(
                "SELECT payload FROM events WHERE kind='deliverable_failed'")]
        self.assertEqual([(e['stage'], e['path']) for e in events], [('FIXER', 'wordfreq.py')])

    def test_python_syntax_rejection_keeps_previous_file_and_retries(self):
        self.setup_task()
        def rewrite(prompt):
            self.assertIn('VALIDATOR REASON: SyntaxError:', prompt)
            self.assertIn('line 2', prompt)
            self.assertEqual((self.root/'wordfreq.py').read_text(), 'old implementation\n')
            return '# valid rewrite'
        task = self.attempt(['# first line\ndef broken(', rewrite, '# tests'])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        with self.store.transaction() as c:
            events = [json.loads(r[0]) for r in c.execute(
                "SELECT payload FROM events WHERE kind='write_rejected'")]
        self.assertEqual(events[0]['error_line'], 2)
        self.assertEqual(len(self.calls), 3)

    def test_empty_twice_then_nudge_recovers(self):
        self.setup_task()
        task = self.attempt(['', '  ', '# recovered', '# unit'])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        for prompt, cap in self.calls[1:3]:
            self.assertIn('empty file content', prompt)
            self.assertIn('Reply with ONLY the complete corrected file content, no commentary, no code fences.', prompt)
            self.assertEqual(cap, 4096)
        self.assertEqual((self.root/'wordfreq.py').read_bytes(), b'# recovered')
        with self.store.transaction() as c:
            usage = c.execute("SELECT count(*) FROM events WHERE kind='model_call_finished'").fetchone()[0]
        self.assertEqual(usage, 4)

    def test_all_unusable_fail_with_reasons(self):
        self.setup_task()
        task = self.attempt([''] * 3 + ['\x00'] * 3)
        self.assertEqual(task['status'], 'QUEUED')
        self.assertTrue(model_side_failure(task['failure']))
        reasons = json.loads(task['failure'].split('; ', 1)[1])
        self.assertEqual(reasons, {'0': 'empty file content', '1': 'file content contains NUL'})
        self.assertEqual(len(self.calls), 6)
        self.assertEqual(len([e for e in self.evidence(task) if 'raw_response' in e]), 6)

    def test_raw_response_evidence_is_bounded(self):
        self.setup_task()
        body = '# snow ☃\n' * 1000
        task = self.attempt(['', body, '# unit'])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        records = [e for e in self.evidence(task) if 'raw_response' in e]
        self.assertEqual([e['call'] for e in records], [1, 2, 3])
        self.assertEqual([e['raw_response'] for e in records],
                         ['', body[:4000] + '\n[truncated]', '# unit'])

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

    def test_oversized_target_fails_without_model_call(self):
        self.setup_task()
        (self.root/'wordfreq.py').write_text('☃' * 22000)
        task = self.attempt([])
        self.assertIn('target_too_large_for_single_call', task['failure'])
        self.assertTrue(model_side_failure(task['failure']))
        self.assertEqual(self.calls, [])

    def test_under_budget_prompt_unchanged(self):
        self.setup_task()
        self.attempt(['# fixed', '# unit'])
        self.assertEqual(self.calls[0][0],
            'FINDING:\nfix counts\nFILE: wordfreq.py\nCURRENT CONTENT:\nold implementation\n'
            '\nReply with the complete corrected content of this file. No JSON, no envelope.')

    def feedback_events(self):
        with self.store.transaction() as c:
            return [json.loads(r[0]) for r in c.execute(
                "SELECT payload FROM events WHERE kind='fixer_feedback'")]

    def acceptance(self):
        body = ('import unittest\nimport wordfreq\n'
                'class Acceptance(unittest.TestCase):\n'
                '    def test_counts(self):\n'
                '        self.assertEqual(wordfreq.count, 2, "correct the count")\n')
        (self.root/'test_acceptance.py').write_text(body)
        return body

    def test_feedback_once_with_names_details_same_targets_and_ledger(self):
        self.setup_task()
        original = self.acceptance()
        def repair(prompt):
            self.assertIn('test_counts', prompt)
            self.assertIn('AssertionError: 1 != 2 : correct the count', prompt)
            self.assertIn('same target set', prompt)
            return 'count = 1\n'  # Still fails: no third pass.
        task = self.attempt(['count = 1\n', '# unit', repair, '# repaired unit'])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertEqual(len(self.calls), 4)
        events = self.feedback_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['status'], 'failed')
        self.assertEqual(events[0]['failure_count'], 1)
        self.assertEqual((self.root/'test_acceptance.py').read_text(), original)
        self.assertEqual(self.engine.integrity_errors('wordcounter', self.root), [])
        with self.store.transaction() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM events WHERE kind='model_call_finished'").fetchone()[0], 4)

    def test_passing_acceptance_skips_feedback_pass(self):
        self.setup_task()
        self.acceptance()
        task = self.attempt(['count = 2\n', '# unit'])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.feedback_events()[0]['failure_count'], 0)
        self.assertEqual(self.feedback_events()[0]['status'], 'passed')

    def test_missing_acceptance_records_event(self):
        self.setup_task()
        task = self.attempt(['# fixed', '# unit'])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertEqual(self.feedback_events()[0]['status'], 'missing')
        self.assertFalse(self.feedback_events()[0]['ran'])

    def test_collection_error_skips_feedback_pass(self):
        self.setup_task()
        (self.root/'test_acceptance.py').write_text('invalid syntax !\n')
        task = self.attempt(['count = 2\n', '# unit'])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.feedback_events()[0]['status'], 'collection_error')

    def test_feedback_resumes_without_extra_check_or_pass(self):
        self.setup_task()
        self.acceptance()
        task = self.attempt(['count = 1\n', '# unit', 'count = 2\n',
                             RuntimeError('model_request_killed_on_deadline')])
        self.assertEqual(task['status'], 'QUEUED')
        task = self.attempt(['# repaired unit'])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertEqual(len(self.feedback_events()), 1)
        self.assertIn('FILE: test_unit.py', self.calls[-1][0])

    def test_feedback_does_not_consult_private_grader(self):
        self.setup_task()
        self.acceptance()
        from pathlib import Path
        original = Path.read_text
        def read(path, *args, **kwargs):
            if path.is_relative_to(self.grader):
                raise AssertionError('fixer read private grader')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'read_text', read):
            task = self.attempt(['count = 1\n', '# unit', 'count = 2\n', '# unit'])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])

    def test_feedback_rejects_acceptance_edit_envelope(self):
        self.setup_task()
        original = self.acceptance()
        task = self.attempt(['count = 1\n', '# unit',
                             '{"action":"write","path":"test_acceptance.py","data":"pass"}',
                             'count = 2\n', '# unit'])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertEqual((self.root/'test_acceptance.py').read_text(), original)
        self.assertEqual(len(self.feedback_events()), 1)
        self.assertEqual(len(self.calls), 5)
