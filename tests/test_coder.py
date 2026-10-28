"""Scripted Stage 16 contracts; no model or network traffic."""
import asyncio
import json
import unittest
from unittest.mock import patch
from optimal_harness.coder import DrivenCoder
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
        content = '  "quote" \\path\n---CONTENT---\n☃\r\n' * 200
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

    def test_unwritable_attempts_remaining_files_then_model_error(self):
        def remaining(prompt):
            self.assertIn('FILE: test_unit.py', prompt)
            return '# remaining'
        task = self.run_driven([self.file_plan, '', '\x00', remaining])
        self.assertIn('coder_action_format_exhausted:unwritable', task['failure'])
        self.assertTrue(model_side_failure(task['failure']))
        self.assertEqual((self.root/'test_unit.py').read_text(), '# remaining')
        self.assertEqual(self.engine.queue.calls, 4)

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
        return 'wordfreq.py :: implementation\ntest_unit.py :: tests\nextra.txt :: data'

    def test_resume_after_two_of_three_files(self):
        calls = self.attempts()
        self.assertEqual([cap for _, cap in calls], [512, 4096, 4096, 4096, 4096, 4096])
        self.assertIn('FILE: extra.txt', calls[4][0])
        self.assertEqual((self.root/'wordfreq.py').read_text(), '# one')

    def test_changed_intake_restarts_plan(self):
        calls = self.attempts(change=True)
        self.assertEqual(calls[4][1], 512)
        self.assertEqual((self.root/'wordfreq.py').read_text(), '# fresh one')
