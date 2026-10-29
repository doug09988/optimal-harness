"""Scripted Stage 16 contracts; no model or network traffic."""
import asyncio
import json
import unittest
from unittest.mock import patch
from optimal_harness.coder import DrivenCoder
from optimal_harness.policy import PolicyError
from optimal_harness.engine import make_brief
from optimal_harness.model import Completion
from optimal_harness.runtime import model_side_failure
from optimal_harness.store import Store
from tests.test_harness import FakeQueue
from tests import test_runtime as runtime_tests


class CoderTests(unittest.TestCase):
    setUp = runtime_tests.RuntimeTests.setUp
    submit = runtime_tests.RuntimeTests.submit
    run_coder = runtime_tests.RuntimeTests.run_coder
    file_plan = 'wordfreq.py :: implementation\ntest_unit.py :: tests'

    def run_driven(self, replies, output=None):
        with patch('optimal_harness.runtime.sandbox_command', return_value=output or {
                'exit_code': 1, 'reason': 'exited', 'stdout': '', 'stderr': 'ERROR unique tail'}) as execute:
            task = self.run_coder(replies, role='CODER')
        self.execute_count = execute.call_count
        return task

    def handoff(self):
        return json.loads(next(self.root.glob('vault/runs/*/a1/handoff.json')).read_text())

    def test_exact_files_split_and_skip_disallowed(self):
        content = '#  "quote" \\path\n# ---CONTENT---\n# ☃\r\n' * 200
        task = self.run_driven([self.file_plan + '\nwordfreq.py :: duplicate', content, '# tests\n', '["sh", "-c", "bad"]'])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertEqual((self.root/'wordfreq.py').read_bytes(), content.encode())
        self.assertEqual((self.root/'test_unit.py').read_bytes(), b'# tests\n')
        self.assertEqual(self.engine.queue.calls, 4)
        self.assertEqual(self.execute_count, 0)
        self.assertIn('skipped', self.handoff()['summary'])
        self.assertIn('existing executor allowlist', self.handoff()['summary'])
        with self.store.transaction() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM events WHERE kind='oversized_accepted_split'").fetchone()[0], 1)

    def test_python_rejection_retries_without_publishing_bad_bytes(self):
        def rewrite(prompt):
            self.assertIn('SyntaxError:', prompt)
            self.assertIn('line 1', prompt)
            self.assertFalse((self.root/'wordfreq.py').exists())
            return 'value = 1\n'
        task = self.run_driven([self.file_plan, '```python\ndef broken(\n```',
                                rewrite, '# tests', '[]'])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertEqual(self.engine.queue.calls, 5)
        with self.store.transaction() as c:
            events = [json.loads(r[0]) for r in c.execute(
                "SELECT payload FROM events WHERE kind='write_rejected'")]
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0]['path'], events[0]['error_line']), ('wordfreq.py', 1))
        compile((self.root/'wordfreq.py').read_bytes(), 'wordfreq.py', 'exec')

    def test_python_fence_repair_preserves_inner_bytes(self):
        inner = '#!/usr/bin/env python3\r\n# ☃  \r\nvalue = 1\r\n'
        for opener in ('```\r\n', '```python\r\n', '```python'):
            with self.subTest(opener=opener):
                self.setUp()
                task = self.run_driven([self.file_plan, opener + inner + '```\r\n\r\n',
                                        '# tests', '[]'])
                self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
                expected = (inner + '\r\n').encode()
                self.assertEqual((self.root/'wordfreq.py').read_bytes(), expected)
                compile(expected, 'wordfreq.py', 'exec')
                with self.store.transaction() as c:
                    events = [json.loads(r[0]) for r in c.execute(
                        "SELECT payload FROM events WHERE kind='write_repaired'")]
                self.assertEqual([event['path'] for event in events], ['wordfreq.py'])

    def test_unterminated_short_strings_are_repaired(self):
        for parts in (['{"first": 1,', '"second": 2}'],
                      ['one', 'two', 'three'],
                      ['one', 'two', 'three', 'four', 'five']):
            with self.subTest(parts=parts):
                self.setUp()
                value = '\n'.join(parts)
                body = "class TestExample:\n    def test_json(self):\n        value = '" + value + "'\n        return value\n"
                task = self.run_driven([self.file_plan, '# impl', body, '[]'])
                self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
                accepted = (self.root/'test_unit.py').read_text()
                namespace = {}
                exec(compile(accepted, 'test_unit.py', 'exec'), namespace)
                self.assertEqual(namespace['TestExample']().test_json(), value)
                self.assertEqual(accepted, body.replace(value, r'\n'.join(parts)))
                with self.store.transaction() as c:
                    events = [json.loads(r[0]) for r in c.execute(
                        "SELECT payload FROM events WHERE kind='write_repaired'")]
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0]['repair_kind'], 'unterminated_string_join')
                self.assertEqual(events[0]['error_line'], 3)
                self.assertEqual(events[0]['path'], 'test_unit.py')

    def test_string_repair_failures_preserve_original_rejection(self):
        for bad in ("value = 'one\ntwo\nthree\nfour\nfive\nsix'\n",
                    "value = 'never closed\nmore\n",
                    "value = " + "'" * 3 + "triple\nmore\n",
                    'value = (]\n',
                    "value = 'one\ntwo'\nvalue = (]\n"):
            with self.subTest(bad=bad):
                self.setUp()
                try:
                    compile(bad, 'test_unit.py', 'exec')
                except SyntaxError as exc:
                    original = f'SyntaxError: test_unit.py: line {exc.lineno}: {exc.msg}'
                def retry(prompt):
                    self.assertIn(original, prompt)
                    self.assertFalse((self.root/'test_unit.py').exists())
                    return '# corrected tests'
                task = self.run_driven([self.file_plan, '# impl', bad, retry, '[]'])
                self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
                with self.store.transaction() as c:
                    events = [json.loads(r[0]) for r in c.execute(
                        "SELECT payload FROM events WHERE kind='write_rejected'")]
                    self.assertEqual(c.execute(
                        "SELECT count(*) FROM events WHERE kind='write_repaired'").fetchone()[0], 0)
                self.assertEqual(len(events), 1)
                self.assertTrue(events[0]['reason'].startswith(original))
                self.assertEqual(events[0]['first_line'], bad.splitlines()[0])

    def test_unfenced_python_is_unchanged_without_repair(self):
        body = '# legitimate ```python later in line\r\nvalue = "```"\r\n'
        task = self.run_driven([self.file_plan, body, '# tests', '[]'])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertEqual((self.root/'wordfreq.py').read_bytes(), body.encode())
        with self.store.transaction() as c:
            self.assertEqual(c.execute(
                "SELECT count(*) FROM events WHERE kind='write_repaired'").fetchone()[0], 0)

    def test_rejected_python_event_bounds_first_line(self):
        bad = '# ' + 'x' * 100 + '\ndef broken('
        task = self.run_driven([self.file_plan, '```python\n' + bad + '\n```',
                                'value = 1', '# tests', '[]'])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        with self.store.transaction() as c:
            events = [json.loads(r[0]) for r in c.execute(
                "SELECT payload FROM events WHERE kind='write_rejected'")]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['first_line'], bad.splitlines()[0][:80])
        self.assertEqual(len(events[0]['first_line']), 80)

    def test_bad_python_exhausts_existing_file_retries(self):
        task = self.run_driven([self.file_plan, 'def broken(', 'if True', 'def broken(', 'if True'])
        self.assertIn('coder_action_format_exhausted:unwritable', task['failure'])
        self.assertEqual(self.engine.queue.calls, 5)
        self.assertFalse((self.root/'wordfreq.py').exists())
        self.assertFalse(any(cp['path'] == 'wordfreq.py'
                             for cp in self.store.checkpoints('wordcounter')))

    def test_partial_failure_seeds_next_fixer_with_final_error_context(self):
        bad = '\n'.join(['# before'] * 5 + ['value = )'] + ['# after'] * 5)
        def retry(prompt):
            self.assertIn('3: # before', prompt)
            self.assertIn('6: value = )', prompt)
            self.assertIn('9: # after', prompt)
            self.assertNotIn('2: # before', prompt)
            self.assertNotIn('10: # after', prompt)
            return bad
        task = self.run_driven([self.file_plan, '# good', bad, retry, '[]'])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertFalse((self.root/'test_unit.py').exists())
        with self.store.transaction() as c:
            failures = [json.loads(r[0]) for r in c.execute(
                "SELECT payload FROM events WHERE kind='deliverable_failed'")]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]['stage'], 'CODER')
        self.assertEqual(failures[0]['path'], 'test_unit.py')
        brief = self.engine._fixer_brief('wordcounter', self.plan, 'gate failure', 'QA')
        from optimal_harness.fixer import structured_fixes
        items = structured_fixes(brief)
        self.assertEqual(items[0][0], 'test_unit.py')
        self.assertIn('SyntaxError:', items[0][1])
        self.assertIn('6: value = )', items[0][1])
        self.assertEqual(self.handoff()['deliverables'], ['wordfreq.py'])
        # The unchanged verifier and gate still count a missing report.
        from optimal_harness.verifier import Verifier
        result = Verifier(self.root).verify([{'type': 'command_receipt', 'path': 'test_unit.py'}])
        self.assertFalse(result['passed'])
        self.assertEqual(self.engine._failure_count(result['results']), 1)

    def test_sanitized_plan_completes_without_retry(self):
        task = self.run_driven(['README.md :: docs\n./wordfreq.py :: implementation\n'
                                'wordfreq.py :: duplicate\ntest_unit.py :: tests',
                                '# impl', '# tests', '["disallowed"]'])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertEqual(task['attempts'], 1)
        self.assertEqual(self.engine.queue.calls, 4)
        self.assertEqual((self.root/'wordfreq.py').read_text(), '# impl')
        with self.store.transaction() as c:
            events = [json.loads(row[0]) for row in c.execute(
                "SELECT payload FROM events WHERE kind='plan_entry_dropped'")]
        self.assertEqual([event['path'] for event in events], ['README.md', 'wordfreq.py'])
        self.assertTrue(all(event['reason'] for event in events))
        self.assertFalse((self.root/'README.md').exists())

    def test_only_off_allowlist_plan_is_model_error(self):
        task = self.run_driven(['README.md :: docs'] * 2)
        self.assertIn('coder_action_format_exhausted:plan', task['failure'])
        self.assertTrue(model_side_failure(task['failure']))
        self.assertEqual(self.engine.queue.calls, 2)

    def test_unwritable_attempts_remaining_files_then_completes(self):
        def remaining(prompt):
            self.assertIn('FILE: test_unit.py', prompt)
            return '# remaining'
        task = self.run_driven([self.file_plan, '', '\x00', remaining, '[]'])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertEqual((self.root/'test_unit.py').read_text(), '# remaining')
        self.assertEqual(self.engine.queue.calls, 5)

    def test_run_on_plan_matches_multiline_and_discloses_repair(self):
        lines = [
            'wordfreq.py :: implementation of the word frequency counter CLI tool',
            'test_unit.py :: unit tests for the word frequency counter implementation',
            'test_acceptance.py :: acceptance tests for the word frequency counter CLI tool',
        ]
        paths = ['wordfreq.py', 'test_unit.py', 'test_acceptance.py']
        strict = DrivenCoder()
        strict.accept_plan('\n'.join(lines), paths, paths)
        coder = DrivenCoder()
        events = []
        coder.accept_plan(' '.join(lines), paths, paths, repaired=events.append)
        self.assertEqual(list(coder.plan.items()), list(strict.plan.items()))
        self.assertEqual(events, [{'repair_kind': 'run_on_segmentation'}])
        task = self.run_driven([' '.join(lines[:2]), '# impl', '# tests', '[]'])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        with self.store.transaction() as c:
            recorded = [json.loads(row[0]) for row in c.execute(
                "SELECT payload FROM events WHERE kind='plan_repaired'")]
        self.assertEqual(recorded, [{'task': task['id'], 'generation': 1,
                                    'repair_kind': 'run_on_segmentation'}])

    def test_strict_plan_does_not_disclose_repair(self):
        task = self.run_driven([self.file_plan, '# impl', '# tests', '[]'])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        with self.store.transaction() as c:
            self.assertEqual(c.execute(
                "SELECT count(*) FROM events WHERE kind='plan_repaired'").fetchone()[0], 0)
        # Even a path-like boundary in a purpose stays untouched if strict parsing succeeds.
        coder = DrivenCoder()
        events = []
        text = 'wordfreq.py :: mention notes.md :: details'
        coder.accept_plan(text, ['wordfreq.py'], repaired=events.append)
        self.assertEqual(coder.plan, {'wordfreq.py': 'mention notes.md :: details'})
        self.assertEqual(events, [])

    def test_failed_plan_resegmentation_preserves_original_error(self):
        for text, error in (
                ('wordfreq.py :: implementation test_unit.py :: ',
                 'plan must include required files: wordfreq.py, test_unit.py'),
                ('wordfreq.py implementation test_unit.py tests',
                 'plan requires path :: purpose, one per line'),
                ('wordfreq.py :: implementation test_unit.py :: tests\nbroken',
                 'plan requires path :: purpose, one per line')):
            with self.subTest(text=text):
                coder = DrivenCoder()
                events = []
                with self.assertRaises(PolicyError) as caught:
                    coder.accept_plan(text, ['wordfreq.py', 'test_unit.py'],
                                      ['wordfreq.py', 'test_unit.py'], repaired=events.append)
                self.assertEqual(str(caught.exception), error)
                self.assertIsNone(coder.plan)
                self.assertEqual(events, [])

    def test_resegmentation_preserves_nonpath_separator_and_purpose_text(self):
        coder = DrivenCoder()
        paths = ['wordfreq.py', 'test_unit.py']
        coder.accept_plan('wordfreq.py :: implementation  label :: detail ☃  '
                          'test_unit.py :: tests', paths, paths)
        self.assertEqual(coder.plan, {
            'wordfreq.py': 'implementation  label :: detail ☃', 'test_unit.py': 'tests'})

    def test_plan_reask_scope_and_cap(self):
        def corrected(prompt):
            self.assertIn('VALIDATOR REASON:', prompt)
            return self.file_plan
        task = self.run_driven(['outside.py :: forbidden', corrected, '# impl', '# tests', '[]'])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        coder = DrivenCoder()
        names = ['file' + str(n) for n in range(30)]
        coder.accept_plan('\n'.join(n + ' :: purpose' for n in names), names)
        self.assertEqual(list(coder.plan), names[:24])

    def test_progress_roundtrip_preserves_plan_order_and_phase_like_paths(self):
        from optimal_harness.store import canonical
        coder = DrivenCoder()
        coder.accept_plan('z :: first\ncheck :: second\na :: last', ['z', 'check', 'a'])
        restored = DrivenCoder()
        restored.restore(json.loads(canonical(coder.progress())))
        self.assertEqual(list(restored.plan), ['z', 'check', 'a'])
        self.assertEqual(restored.current, 'file:z')
        restored.bodies['z'] = 'accepted'
        self.assertEqual(restored.current, 'file:check')
        self.assertIn('FILE: check', restored.prompt({}))

    def test_two_bad_plans_are_model_error(self):
        task = self.run_driven(['', 'no separator'])
        self.assertIn('coder_action_format_exhausted:plan', task['failure'])
        self.assertTrue(model_side_failure(task['failure']))
        self.assertEqual(self.engine.queue.calls, 2)

    def test_self_check_repairs_named_file_once_and_records_second_failure(self):
        self.plan_config_command()
        def repair(prompt):
            self.assertIn('ERROR unique tail', prompt)
            return 'wordfreq.py\n# proposed fix'
        def body(prompt):
            self.assertIn('FILE: wordfreq.py', prompt)
            self.assertIn('ERROR unique tail', prompt)
            return '# actual fix\n'
        task = self.run_driven([self.file_plan, '# impl', '# tests', '["python3", "wordfreq.py"]', repair, body])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertEqual(self.execute_count, 2)
        self.assertEqual(self.engine.queue.calls, 6)
        self.assertEqual((self.root/'wordfreq.py').read_bytes(), b'# actual fix\n')
        self.assertIn('"exit_code":1', self.handoff()['summary'])

    def plan_config_command(self):
        self.plan['stages']['CODER']['deliverables'].append('reports/coder.json')
        self.plan['stages']['CODER']['commands'] = {'selfcheck': {
            'argv': ['python3', 'wordfreq.py'], 'reads': ['wordfreq.py'],
            'capture': 'reports/coder.json', 'timeout_seconds': 20}}

    def test_successful_self_check_needs_no_repair(self):
        self.plan_config_command()
        task = self.run_driven([self.file_plan, '# impl', '# tests', '["python3", "wordfreq.py"]'],
                               {'exit_code': 0, 'reason': 'exited', 'stdout': 'ok', 'stderr': ''})
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertEqual(self.execute_count, 1)
        self.assertEqual(self.engine.queue.calls, 4)

    def attempts(self, change=False):
        self.plan['stages']['CODER']['writes'].append('extra.txt')
        self.submit()
        self.store.finish(self.store.claim('setup', 60), 'SUCCEEDED')
        self.store.enqueue('wordcounter', 'CODER', 0,
                           make_brief('CODER', self.store.project('wordcounter')['plan']))
        calls = []
        class Queue(FakeQueue):
            async def request(q, prompt, cap, timeout):
                calls.append((str(prompt), cap))
                item = q.actions.popleft()
                if isinstance(item, Exception):
                    raise item
                return Completion(item, 100, 80, 30)
        file_plan = self.plan_text()
        with patch.object(self.engine, '_advance'):
            self.engine.queue = Queue([file_plan, '# one', '# two', RuntimeError('model_request_killed_on_deadline')])
            asyncio.run(self.engine.run_task(self.store.claim('first', 60)))
            self.assertEqual(self.store.tasks('wordcounter')[-1]['status'], 'QUEUED')
            if change:
                (self.root/'artifacts').mkdir(exist_ok=True)
                (self.root/'artifacts/intake.json').write_text('{"changed":true}')
            self.engine.store = Store(self.base/'board.db')
            self.engine.queue = Queue(([file_plan, '# fresh one', '# fresh two'] if change else []) + ['extra', '["disallowed"]'])
            asyncio.run(self.engine.run_task(self.store.claim('second', 60)))
        task = self.store.tasks('wordcounter')[-1]
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertEqual(self.engine.integrity_errors('wordcounter', self.root), [])
        return calls

    @staticmethod
    def plan_text():
        return 'README.md :: docs\n./wordfreq.py :: implementation\nwordfreq.py :: duplicate\ntest_unit.py :: tests\nextra.txt :: data'

    def test_resume_after_two_of_three_files(self):
        calls = self.attempts()
        self.assertEqual([cap for _, cap in calls], [512, 4096, 4096, 4096, 4096, 4096])
        self.assertIn('FILE: extra.txt', calls[4][0])
        self.assertEqual((self.root/'wordfreq.py').read_text(), '# one')

    def test_changed_intake_restarts_plan(self):
        calls = self.attempts(change=True)
        self.assertEqual(calls[4][1], 512)
        self.assertEqual((self.root/'wordfreq.py').read_text(), '# fresh one')
