import asyncio
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from optimal_harness.engine import Engine
from optimal_harness.model import Completion
from optimal_harness.policy import FileBroker, PolicyError
from optimal_harness.store import Store, LeaseLost
from optimal_harness.failures import fingerprint
from tests.test_harness import FakeQueue

BASE=Path(__file__).resolve().parents[1]


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=BASE/'runs'); self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name); self.root=self.base/'work'; self.grader=self.base/'grader'
        ex=BASE/'examples'/'word-counter'
        shutil.copytree(ex/'work',self.root); shutil.copytree(ex/'grader',self.grader)
        self.plan=json.loads((ex/'plan.json').read_text())
        self.store=Store(self.base/'board.db'); self.engine=Engine(self.store,FakeQueue([]))

    def submit(self): self.engine.submit(self.root,self.plan,self.grader/'acceptance.json')

    def test_speculative_second_action_never_executes(self):
        class Queue(FakeQueue):
            async def request(self,*args):
                self.calls+=1
                if self.calls==1:
                    text='{"action":"read","path":"spec.md"}\n{"action":"write","path":"artifacts/intake.json","data":"invented"}'
                else: text='{"action":"fail","reason":"honest stop"}'
                return Completion(text,100,50,30)
        self.engine.queue=Queue([]); self.submit(); asyncio.run(self.engine.serve(1))
        self.assertFalse((self.root/'artifacts/intake.json').exists())
        with self.store.transaction() as c:
            count=c.execute("SELECT count(*) FROM events WHERE kind='additional_actions_discarded'").fetchone()[0]
        self.assertEqual(count,1)

    def run_coder(self, actions, role='CODER', prior_stage=''):
        from optimal_harness.engine import make_brief
        from unittest.mock import patch
        self.submit()
        task = self.store.claim('worker', 60)
        task['stage'] = role
        task['brief'] = make_brief(role, self.store.project('wordcounter')['plan'], source_stage=prior_stage)
        class Queue(FakeQueue):
            async def request(q, prompt, *args):
                q.calls += 1
                value = q.actions.popleft()
                if callable(value):
                    value = value(prompt)
                return Completion(value if isinstance(value, str) else json.dumps(value), 100, 80, 30)
        self.engine.queue = Queue(actions)
        with patch.object(self.engine, '_advance'):
            asyncio.run(self.engine.run_task(task))
        return self.store.tasks('wordcounter')[0]

    def test_all_writing_roles_bound_invalid_actions(self):
        for role in ('INTAKE', 'INTEGRATOR', 'REVIEW', 'FIXER'):
            with self.subTest(role=role):
                # Each case needs a separate project/store.
                if role != 'INTAKE':
                    self.setUp()
                task = self.run_coder([
                    {'action': 'write', 'path': 'never-created', 'data': 'x'*5000, 'extra': True}
                ] * 3, role=role)
                self.assertIn('review_section_exhausted:reviewed_stage' if role == 'REVIEW' else role.lower() + '_action_format_exhausted', task['failure'])
                self.assertEqual(self.engine.queue.calls, 3)
                self.assertFalse((self.root/'never-created').exists())

    def run_qa(self, replies):
        from unittest.mock import patch
        self.execution = {'exit_code': 0, 'reason': 'exited', 'seconds': 0.01,
                          'stdout': 'test_one ... ok\ntest_two ... ok\n', 'stderr': 'output tail'}
        with patch('optimal_harness.runtime.sandbox_command', return_value=self.execution):
            return self.run_coder(replies, role='QA')

    def test_driven_qa_assembles_exactly_planned_tests_and_reports_execution(self):
        import ast
        from optimal_harness.store import canonical
        def report(prompt):
            self.assertIn(canonical(self.execution) + '\n', prompt)
            self.assertNotIn('Respond with ONE next JSON action', prompt)
            self.assertNotIn('import sys\nprint', prompt)
            return 'PASS\nObserved acceptance output.'
        task = self.run_qa(['test_one\ntest_two\ntest_one',
                            'def test_one(self):\n    self.assertTrue(True)',
                            '```python\ndef test_two():\n    assert True\n```', report])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        tree = ast.parse((self.root/'test_acceptance.py').read_text())
        methods = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        self.assertEqual(methods, ['test_one', 'test_two'])
        self.assertEqual(self.engine.queue.calls, 4)
        with self.store.transaction() as c:
            events = [json.loads(r[0]) for r in c.execute("SELECT payload FROM events WHERE kind='tool_action'")]
        self.assertTrue(any(canonical(self.execution) in e['receipt'] for e in events if e['op'] == 'command'))

    def test_driven_qa_unwritable_does_not_kill_stage(self):
        def report(prompt):
            self.assertIn('test_bad', prompt)
            self.assertIn('UNWRITABLE TESTS', prompt)
            return 'FAIL\nOne test could not be written.'
        task = self.run_qa(['test_bad\ntest_good', '', '',
                            'def test_good(self):\n    self.assertTrue(True)', report])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        code = (self.root/'test_acceptance.py').read_text()
        self.assertNotIn('def test_bad', code)
        self.assertIn('def test_good', code)
        handoff = json.loads(next(self.root.glob('vault/runs/*/a1/handoff.json')).read_text())
        self.assertTrue(any('test_bad: unwritable' in gap for gap in handoff['open_discrepancies']))

    def test_driven_qa_report_reasks_exactly_once(self):
        def corrected(prompt):
            self.assertIn('report first line must be exactly PASS or FAIL', prompt)
            return 'PASS\nAcceptance checked.'
        task = self.run_qa(['test_one', 'def test_one():\n    assert True', 'garbage', corrected])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertEqual(self.engine.queue.calls, 4)

    def test_driven_qa_two_bad_reports_are_model_error(self):
        from optimal_harness.runtime import model_side_failure
        task = self.run_qa(['test_one', 'def test_one():\n    assert True', ' PASS', 'garbage'])
        self.assertIn('qa_action_format_exhausted:report', task['failure'])
        self.assertTrue(model_side_failure(task['failure']))
        self.assertEqual(self.engine.queue.calls, 4)

    def test_driven_qa_plan_retry_and_bound(self):
        from optimal_harness.qa import DrivenQA
        from optimal_harness.runtime import model_side_failure
        qa = DrivenQA()
        qa.accept('\n'.join(['test_' + str(n) for n in range(45)]))
        self.assertEqual(len(qa.plan), 40)
        task = self.run_qa(['', '{"tests": []}'])
        self.assertIn('qa_action_format_exhausted:plan', task['failure'])
        self.assertTrue(model_side_failure(task['failure']))
        self.assertEqual(self.engine.queue.calls, 2)

    def test_driven_qa_invalid_body_is_reasked_with_reason(self):
        def corrected(prompt):
            self.assertIn('VALIDATOR REASON:', prompt)
            self.assertIn('test_one', prompt)
            return 'def test_one(self):\n    self.assertTrue(True)'
        task = self.run_qa(['test_one', 'def test_other():\n    pass', corrected, 'PASS'])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])

    def qa_attempts(self, first, second, change_input=False):
        from optimal_harness.engine import make_brief
        from unittest.mock import patch
        self.submit()
        self.store.finish(self.store.claim('setup', 60), 'SUCCEEDED')
        self.store.enqueue('wordcounter', 'QA', 0,
                           make_brief('QA', self.store.project('wordcounter')['plan']))
        calls = []
        class Queue(FakeQueue):
            async def request(q, prompt, cap, timeout):
                calls.append((str(prompt), cap))
                item = q.actions.popleft()
                if isinstance(item, Exception):
                    raise item
                return Completion(item, 100, 80, 30)
        output = {'exit_code': 0, 'reason': 'exited', 'seconds': 0.01,
                  'stdout': 'acceptance passed', 'stderr': ''}
        with patch('optimal_harness.runtime.sandbox_command', return_value=output) as execute, patch.object(self.engine, '_advance'):
            self.engine.queue = Queue(first)
            asyncio.run(self.engine.run_task(self.store.claim('first', 60)))
            self.assertEqual(self.store.tasks('wordcounter')[-1]['status'], 'QUEUED')
            if change_input:
                (self.root/'wordfreq.py').write_text('# changed artifact under test\n')
            # Reopen the store to verify progress survives runtime reconstruction.
            self.engine.store = Store(self.base/'board.db')
            self.engine.queue = Queue(second)
            asyncio.run(self.engine.run_task(self.store.claim('second', 60)))
            self.assertEqual(self.store.tasks('wordcounter')[-1]['status'], 'SUCCEEDED')
        return calls, execute.call_count

    def test_driven_qa_caps_and_resume_after_two_of_four_tests(self):
        names = ['test_' + str(n) for n in range(4)]
        bodies = ['def ' + name + '():\n    assert True' for name in names]
        calls, executions = self.qa_attempts(
            ['\n'.join(names), *bodies[:2], RuntimeError('model_request_killed_on_deadline')],
            [*bodies[2:], 'PASS'])
        self.assertEqual([cap for _, cap in calls], [512, 2048, 2048, 2048, 2048, 2048, 1024])
        self.assertIn('TEST: test_2', calls[4][0])
        self.assertIn('TEST: test_3', calls[5][0])
        code = (self.root/'test_acceptance.py').read_text()
        for name in names:
            self.assertIn('def ' + name, code)
        self.assertEqual(executions, 1)

    def test_driven_qa_changed_artifact_discards_progress(self):
        body = 'def test_one():\n    assert True'
        calls, executions = self.qa_attempts(
            ['test_one\ntest_two', body, RuntimeError('model_request_killed_on_deadline')],
            ['test_fresh', 'def test_fresh():\n    assert True', 'PASS'], change_input=True)
        self.assertEqual([cap for _, cap in calls], [512, 2048, 2048, 512, 2048, 1024])
        self.assertNotIn('def test_one', (self.root/'test_acceptance.py').read_text())
        self.assertEqual(executions, 1)

    def test_driven_qa_resume_preserves_unwritable_tests(self):
        calls, executions = self.qa_attempts(
            ['test_bad\ntest_good', '', '', RuntimeError('model_request_killed_on_deadline')],
            ['def test_good():\n    assert True', 'PASS'])
        self.assertIn('TEST: test_good', calls[4][0])
        self.assertIn('test_bad', calls[-1][0])
        self.assertNotIn('def test_bad', (self.root/'test_acceptance.py').read_text())
        self.assertEqual(executions, 1)

    def test_driven_qa_resume_with_complete_tests_executes_before_report(self):
        calls, executions = self.qa_attempts(
            ['test_one', 'def test_one():\n    assert True', RuntimeError('model_request_killed_on_deadline')],
            ['PASS'])
        self.assertEqual([cap for _, cap in calls], [512, 2048, 1024, 1024])
        self.assertIn('acceptance passed', calls[-1][0])
        self.assertEqual(executions, 2)

    def test_generated_handoff_shape_and_event_order(self):
        task = self.run_coder([
            {'action': 'write', 'path': 'wordfreq.py', 'data': '# code'},
            {'action': 'write', 'path': 'test_unit.py', 'data': '# tests'},
            {'action': 'finish', 'summary': 'implemented', 'open_discrepancies': []},
        ])
        self.assertEqual(task['status'], 'SUCCEEDED')
        paths = list(self.root.glob('vault/runs/*/a*/handoff.json'))
        data = json.loads(paths[0].read_text())
        self.assertEqual(data, {'status': 'ready', 'summary': 'implemented',
                              'open_discrepancies': [], 'deliverables': ['wordfreq.py', 'test_unit.py'],
                              'obligation_resolutions': []})
        with self.store.transaction() as c:
            kinds = [r[0] for r in c.execute('SELECT kind FROM events ORDER BY rowid')]
        self.assertLess(kinds.index('stage_checks_passed'), kinds.index('handoff_accepted'))
        self.assertNotIn('handoff_error_reported', kinds)

    def test_generated_handoff_missing_fields_feedback(self):
        def corrected(prompt):
            self.assertIn('template: {"action":"finish",', prompt)
            self.assertIn('open_discrepancies', prompt)
            return {'action': 'finish', 'summary': 'done', 'open_discrepancies': []}
        task = self.run_coder([
            {'action': 'write', 'path': 'wordfreq.py', 'data': '# code'},
            {'action': 'write', 'path': 'test_unit.py', 'data': '# tests'},
            {'action': 'finish', 'summary': 'missing fields'}, corrected,
        ])
        self.assertEqual(task['status'], 'SUCCEEDED')

    def test_intake_pointer_chunks_materialized_by_runtime(self):
        from optimal_harness.verifier import LIFECYCLE
        text = (self.root/'spec.md').read_text()
        slots = {key: {'slot': key, 'source': 'spec.md', 'start_line': 1,
                       'end_line': len(text.splitlines()), 'because': 'explicit rules'} for key in LIFECYCLE}
        data = json.dumps({'slots': slots})
        midpoint = len(data)//2
        task = self.run_coder([
            {'action': 'write', 'path': 'artifacts/intake.json', 'data': data[:midpoint], 'more': True},
            {'action': 'append', 'path': 'artifacts/intake.json', 'data': data[midpoint:], 'more': False},
            {'action': 'finish', 'summary': 'source audit', 'open_discrepancies': []},
        ], role='INTAKE')
        self.assertEqual(task['status'], 'SUCCEEDED')
        doc = json.loads((self.root/'artifacts/intake.json').read_text())
        self.assertEqual(doc['slots']['invocation']['quote'], text)

    def test_review_template_uses_literal_prior_stage(self):
        from optimal_harness.review import section_template, validate_section
        value = json.loads(section_template('artifacts/review.json', 'reviewed_stage', 'INTAKE'))
        self.assertEqual(value['data'], {'reviewed_stage': 'INTAKE'})
        validate_section('reviewed_stage', value['data']['reviewed_stage'], 'INTAKE')

    def test_coder_format_denial_has_parseable_small_action(self):
        from optimal_harness.model import parse_action
        def correction(prompt):
            observation = prompt.split('LAST TOOL OBSERVATION', 1)[1]
            template = observation.split('Action template: ', 1)[1]
            value, _ = json.JSONDecoder().raw_decode(template)
            self.assertEqual(value['format'], 'raw')
            parse_action(json.dumps(value) + '\n---CONTENT---\nexample')
            self.assertIn(value['path'], self.plan['stages']['CODER']['deliverables'])
            self.assertLess(len(json.dumps(value).encode()), 4096)
            return {'action': 'fail', 'reason': 'template checked'}
        task = self.run_coder(['{', correction])
        self.assertIn('template checked', task['failure'])

    def test_intake_denial_template_is_complete_and_valid(self):
        from optimal_harness.runtime import intake_document
        from optimal_harness.model import parse_action
        target = self.plan['stages']['INTAKE']['deliverables'][0]
        def correction(prompt):
            observation = prompt.split('LAST TOOL OBSERVATION', 1)[1]
            template = observation.split('Template (fill with actual gaps or citations): ', 1)[1]
            value, _ = json.JSONDecoder().raw_decode(template)
            parse_action(json.dumps(value))
            self.assertEqual(value['path'], target)
            intake_document(value['data'])
            return {'action': 'fail', 'reason': 'template checked'}
        task = self.run_coder([{'action': 'write', 'path': target, 'data': {}}, correction], role='INTAKE')
        self.assertIn('template checked', task['failure'])

    def test_review_system_contract_reaches_client_without_network(self):
        import pickle
        from unittest.mock import MagicMock, patch
        from optimal_harness.model import LocalChatClient, SYSTEM_PREFIX
        from optimal_harness.review import DrivenReview
        prompt = pickle.loads(pickle.dumps(DrivenReview('CODER').prompt({'wordfreq.py': 'source body'})))
        response = MagicMock()
        response.__enter__.return_value = response
        response.headers = {}
        response.read.return_value = json.dumps({
            'choices': [{'message': {'content': 'CODER'}}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 2}}).encode()
        opener = MagicMock()
        opener.open.return_value = response
        with patch('urllib.request.build_opener', return_value=opener):
            client = LocalChatClient('http://127.0.0.1:9999', 'scripted')
            client.complete(prompt, 20, 5)
            payload = json.loads(opener.open.call_args[0][0].data)
            self.assertEqual(payload['messages'][0]['content'], prompt.system_prefix)
            self.assertIn('source body', payload['messages'][1]['content'])
            self.assertNotIn('one JSON object', payload['messages'][0]['content'])
            client.complete('ordinary action prompt', 20, 5)
            payload = json.loads(opener.open.call_args[0][0].data)
            self.assertEqual(payload['messages'][0]['content'], SYSTEM_PREFIX)

    def test_review_driven_plain_text_and_events(self):
        task = self.run_coder(['""', 'ATTEMPTED missing input', 'Observed input checks', '', 'APPROVE'], role='REVIEW')
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        report = json.loads((self.root/'artifacts/review.json').read_text())
        self.assertEqual(report, {'reviewed_stage': '', 'counterexamples': ['ATTEMPTED missing input'],
                                 'observations': ['Observed input checks'], 'unresolved': [], 'verdict': 'APPROVE'})
        self.assertEqual(self.engine.queue.calls, 5)
        with self.store.transaction() as c:
            for kind in ('model_call_started', 'model_call_finished', 'tool_action'):
                self.assertEqual(c.execute('SELECT count(*) FROM events WHERE kind=?', (kind,)).fetchone()[0], 5)

    def test_review_caps_and_prompt_diet(self):
        marker = 'UNIQUE_ACCEPTED_COUNTEREXAMPLE'
        def observation(prompt):
            self.assertNotIn(marker, prompt)
            self.assertNotIn('ACCEPTED SECTIONS', prompt)
            return 'Observed facts'
        def verdict(prompt):
            self.assertIn(marker, prompt)
            self.assertIn('Observed facts', prompt)
            self.assertIn('exactly one line', prompt)
            return 'APPROVE'
        task = self.run_coder(['""', marker, observation, '', verdict], role='REVIEW')
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        with self.store.transaction() as c:
            events = c.execute("SELECT payload FROM events WHERE kind='model_call_started' ORDER BY rowid").fetchall()
        self.assertEqual([json.loads(row[0])['completion_cap'] for row in events],
                         [2048, 2048, 2048, 2048, 128])

    def test_deadline_kill_leaves_queue_available_for_stage_retry(self):
        from optimal_harness.model import ModelQueue, ModelError
        from types import SimpleNamespace
        from unittest.mock import patch
        queue = ModelQueue(SimpleNamespace(url='https://scripted.invalid'), self.base/'model.lock')
        result = Completion('APPROVE', 10, 0, 1)
        async def run():
            await queue.start()
            try:
                with self.assertRaisesRegex(ModelError, 'model_request_killed_on_deadline'):
                    await queue.request('scripted', 128, 1)
                self.assertTrue(queue.healthy)
                self.assertEqual(await queue.request('scripted retry', 128, 1), result)
            finally:
                await queue.stop()
        with patch.object(queue, '_locked_request', side_effect=[
                ModelError('model_request_killed_on_deadline'), result]):
            asyncio.run(run())

    def test_review_source_spans(self):
        from optimal_harness.review import DrivenReview
        review = DrivenReview('INTAKE')
        review.accept('INTAKE')
        artifact = json.dumps({'source': 'spec.md', 'start_line': 2, 'end_line': 2})
        sources = review.section_sources({'intake.json': artifact,
                                          'spec.md': 'unrelated before\nrelevant\nunrelated after'},
                                         ['intake.json'])
        prompt = review.prompt(sources)
        self.assertIn(artifact.replace('"', '\\"'), prompt)
        self.assertIn('2: relevant', prompt)
        self.assertNotIn('unrelated before', prompt)
        self.assertNotIn('unrelated after', prompt)

    def test_review_envelope_extraction(self):
        values = {'reviewed_stage': '', 'counterexamples': ['ATTEMPTED missing input'],
                  'observations': ['fact'], 'unresolved': []}
        actions = [{'action': 'write', 'data': {key: value}} for key, value in values.items()]
        task = self.run_coder(actions + ['REQUEST_CHANGES'], role='REVIEW')
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertEqual(json.loads((self.root/'artifacts/review.json').read_text()),
                         dict(values, verdict='REQUEST_CHANGES'))

    def test_review_identical_text_progresses(self):
        task = self.run_coder(['CODER'] * 4 + ['APPROVE'], role='REVIEW', prior_stage='CODER')
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertEqual(self.engine.queue.calls, 5)
        report = json.loads((self.root/'artifacts/review.json').read_text())
        for section in ('counterexamples', 'observations', 'unresolved'):
            self.assertEqual(report[section], ['CODER'])

    def test_review_invalid_section_reasks_only_that_section(self):
        def correction(prompt):
            self.assertIn('SECTION: counterexamples', prompt)
            self.assertIn('VALIDATOR REASON:', prompt)
            self.assertIn('must contain at least one nonempty ATTEMPTED', prompt)
            self.assertNotIn('SECTION: reviewed_stage', prompt)
            return 'ATTEMPTED missing input'
        task = self.run_coder(['""', '[]', correction, '', '', 'APPROVE'], role='REVIEW')
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertEqual(self.engine.queue.calls, 6)

    def test_review_independent_section_budgets(self):
        task = self.run_coder(['wrong', 'wrong', '""', '[]', '[]', 'attempt',
                               '{}', '{}', '', 'null', 'null', '', 'APPROVE'], role='REVIEW')
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertEqual(self.engine.queue.calls, 13)

    def test_review_section_exhaustion(self):
        task = self.run_coder(['""', '[]', '[]', '[]'], role='REVIEW')
        self.assertIn('review_section_exhausted:counterexamples', task['failure'])
        self.assertEqual(self.engine.queue.calls, 4)
        self.assertFalse((self.root/'artifacts/review.json').exists())

    def test_review_unextractable_envelope(self):
        def correction(prompt):
            self.assertIn('unextractable_response', prompt)
            return '""'
        task = self.run_coder([{'action': 'read', 'path': 'spec.md'}, correction,
                               'attempt', '', '', 'APPROVE'], role='REVIEW')
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])

    def test_review_verdict_reask(self):
        def corrected(prompt):
            self.assertIn('unparseable_verdict', prompt)
            return 'APPROVE\nReview evidence follows.'
        task = self.run_coder(['""', 'attempt', '', '', 'garbage', corrected], role='REVIEW')
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertEqual(self.engine.queue.calls, 6)

    def test_review_two_invalid_verdicts_are_model_failure(self):
        from optimal_harness.runtime import model_side_failure
        task = self.run_coder(['""', 'attempt', '', '', ' APPROVE', 'APPROVE extra'], role='REVIEW')
        self.assertIn('review_action_format_exhausted:verdict', task['failure'])
        self.assertTrue(model_side_failure(task['failure']))
        self.assertEqual(self.engine.queue.calls, 6)
        self.assertFalse((self.root/'artifacts/review.json').exists())

    def test_review_section_schema_feedback(self):
        from optimal_harness.review import validate_section
        for key, value, expected in [('reviewed_stage', 'QA', "must equal 'CODER'"),
                                     ('counterexamples', [], 'ATTEMPTED'),
                                     ('counterexamples', [' '], 'nonempty'),
                                     ('observations', {}, 'must be a list'),
                                     ('unresolved', None, 'must be a list')]:
            with self.subTest(key=key), self.assertRaisesRegex(PolicyError, expected):
                validate_section(key, value, 'CODER')

    def test_generated_handoff_exhaustion_reports_error_without_acceptance(self):
        task = self.run_coder([
            {'action': 'finish', 'summary': 'invalid', 'open_discrepancies': None}
        ] * 3)
        self.assertIn('coder_handoff_exhausted:handoff_not_ready_or_malformed:finish.open_discrepancies', task['failure'])
        with self.store.transaction() as c:
            kinds = [r[0] for r in c.execute('SELECT kind FROM events ORDER BY rowid')]
        self.assertIn('handoff_error_reported', kinds)
        self.assertNotIn('handoff_accepted', kinds)

    def test_coder_chunks_recover_from_truncation(self):
        import re
        def after_bad(prompt):
            self.assertIn('correct the reported problem', prompt)
            self.assertIn('Rejected actions wrote no bytes', prompt)
            return {'action': 'write', 'path': 'wordfreq.py', 'data': 'α\n', 'more': True}
        def continuation(prompt):
            self.assertIn('Continue wordfreq.py using action=append', prompt)
            self.assertIn('"role_work_complete": false', prompt)
            return {'action': 'append', 'path': 'wordfreq.py', 'data': 'β\n', 'more': False}
        def handoff(prompt):
            path = re.search(r'vault/runs/[0-9a-f]{16}/a[0-9]+/handoff.json', prompt).group()
            return {'action': 'write', 'path': path, 'data': {'status': 'ready', 'open_discrepancies': []}}
        task = self.run_coder([
            '{"action":"write","path":"wordfreq.py","data":"truncated',
            after_bad, continuation,
            {'action': 'write', 'path': 'test_unit.py', 'data': '# tests\n'},
            handoff, {'action': 'finish', 'summary': 'done'}])
        self.assertEqual(task['status'], 'SUCCEEDED')
        self.assertEqual((self.root/'wordfreq.py').read_text(), 'α\nβ\n')
        self.assertEqual(self.engine.queue.calls, 6)

    @staticmethod
    def coder_handoff(data):
        def action(prompt):
            import re
            path = re.search(r'vault/runs/[0-9a-f]{16}/a[0-9]+/handoff.json', prompt).group()
            return {'action': 'write', 'path': path, 'data': data}
        return action

    def test_coder_handoff_missing_field_corrects_before_acceptance(self):
        def corrected(prompt):
            self.assertIn('handoff.open_discrepancies', prompt)
            self.assertIn('Rewrite the complete handoff', prompt)
            from optimal_harness.handoff import ready_document
            template = prompt.split('Template: ', 1)[1]
            value, _ = json.JSONDecoder().raw_decode(template)
            self.assertTrue(value['path'].endswith('/handoff.json'))
            from optimal_harness.model import parse_action
            raw = template.split('\n---CONTENT---\n', 1)[1]
            payload, _ = json.JSONDecoder().raw_decode(raw)
            ready_document(parse_action(json.dumps(value) + '\n---CONTENT---\n' + json.dumps(payload))['data'])
            return self.coder_handoff({'status': 'ready', 'open_discrepancies': []})(prompt)
        task = self.run_coder([
            {'action': 'write', 'path': 'wordfreq.py', 'data': '# implementation'},
            {'action': 'write', 'path': 'test_unit.py', 'data': '# tests'},
            self.coder_handoff({'status': 'ready', 'summary': 'same defect as archived run'}),
            corrected, {'action': 'finish', 'summary': 'done'}])
        self.assertEqual(task['status'], 'SUCCEEDED')
        with self.store.transaction() as c:
            kinds = [r[0] for r in c.execute('SELECT kind FROM events ORDER BY rowid')]
        self.assertEqual(kinds.count('tool_denied'), 1)
        self.assertLess(kinds.index('stage_checks_passed'), kinds.index('handoff_accepted'))

    def test_coder_handoff_retries_bounded_and_error_event_distinct(self):
        task = self.run_coder([self.coder_handoff(data) for data in (
            {'status': 'ready'}, {'status': 'ready', 'open_discrepancies': None},
            {'status': 'ready', 'open_discrepancies': 'none'})])
        self.assertIn('coder_handoff_exhausted:handoff.open_discrepancies', task['failure'])
        self.assertEqual(self.engine.queue.calls, 3)
        with self.store.transaction() as c:
            kinds = [r[0] for r in c.execute('SELECT kind FROM events')]
        self.assertNotIn('handoff_accepted', kinds)
        self.assertIn('handoff_error_reported', kinds)
        self.assertFalse(any(cp['path'].endswith('/handoff.json')
                             for cp in self.store.checkpoints('wordcounter')))

    def test_coder_premature_finish_names_missing_deliverables(self):
        def correction(prompt):
            self.assertIn('missing_or_uncheckpointed_deliverables:wordfreq.py,test_unit.py', prompt)
            return {'action': 'write', 'path': 'wordfreq.py', 'data': '# code'}
        task = self.run_coder([
            {'action': 'finish', 'summary': 'premature'}, correction,
            {'action': 'write', 'path': 'test_unit.py', 'data': '# tests'},
            self.coder_handoff({'status': 'ready', 'open_discrepancies': ['known limitation']}),
            {'action': 'finish', 'summary': 'done'}])
        self.assertEqual(task['status'], 'SUCCEEDED')

    def test_coder_finish_revalidates_unwritten_handoff(self):
        def corrected(prompt):
            self.assertIn('handoff.status must equal ready', prompt)
            self.assertIn('Correct readiness failure before finish', prompt)
            return self.coder_handoff({'status': 'ready', 'open_discrepancies': []})(prompt)
        task = self.run_coder([
            {'action': 'write', 'path': 'wordfreq.py', 'data': '# code'},
            {'action': 'write', 'path': 'test_unit.py', 'data': '# tests'},
            {'action': 'finish', 'summary': 'no handoff yet'}, corrected,
            {'action': 'finish', 'summary': 'done'}])
        self.assertEqual(task['status'], 'SUCCEEDED')

    def test_handoff_shape_preserves_gate_predicate(self):
        from optimal_harness.handoff import ready_document
        for value in ('{bad', '[]', {}, {'status': 'done', 'open_discrepancies': []},
                      {'status': 'ready', 'open_discrepancies': {}}):
            with self.assertRaises(ValueError):
                ready_document(value)
        value = {'status': 'ready', 'open_discrepancies': ['unresolved']}
        self.assertEqual(ready_document(json.dumps(value)), value)

    @staticmethod
    def raw_action(path, data, op='write', more=False):
        return json.dumps({'action': op, 'path': path, 'format': 'raw', 'more': more}) + '\n---CONTENT---\n' + data

    def test_raw_payload_round_trip_and_mixed_chunks(self):
        content = '  "quotes" \\path\n---CONTENT---\n☃\r\nlast line\n'
        task = self.run_coder([
            {'action': 'write', 'path': 'wordfreq.py', 'data': 'prefix\n', 'more': True},
            self.raw_action('wordfreq.py', content, 'append', True),
            {'action': 'append', 'path': 'wordfreq.py', 'data': 'suffix', 'more': False},
            self.raw_action('test_unit.py', content),
            {'action': 'finish', 'summary': 'done', 'open_discrepancies': []}])
        self.assertEqual(task['status'], 'SUCCEEDED')
        self.assertEqual((self.root/'wordfreq.py').read_bytes(), ('prefix\n' + content + 'suffix').encode())
        self.assertEqual((self.root/'test_unit.py').read_bytes(), content.encode())

    def test_oversized_raw_and_json_writes_split_byte_exactly(self):
        content = ('  "quotes" \\path\n---CONTENT---\n☃\r\n' * 180) + 'end\n'
        from unittest.mock import patch
        writes = []
        original = FileBroker.write
        def record(broker, path, data, append=False):
            writes.append((path, data, append))
            return original(broker, path, data, append)
        with patch.object(FileBroker, 'write', record):
            task = self.run_coder([
                self.raw_action('wordfreq.py', content),
                {'action': 'write', 'path': 'test_unit.py', 'data': content},
                {'action': 'finish', 'summary': 'done', 'open_discrepancies': []}])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        for path in ('wordfreq.py', 'test_unit.py'):
            self.assertEqual((self.root/path).read_bytes(), content.encode())
        with self.store.transaction() as c:
            events = c.execute("SELECT * FROM events WHERE kind='oversized_accepted_split'").fetchall()
        self.assertEqual(len(events), 2)
        for event in events:
            payload = json.loads(event['payload'])
            units = [(data, append) for path, data, append in writes if path == payload['path']]
            self.assertEqual(payload['chunk_count'], len(units))
            self.assertGreater(len(units), 1)
            self.assertTrue(all(len(data.encode()) <= payload['chunk_limit'] for data, _ in units))
            self.assertEqual([append for _, append in units], [False] + [True] * (len(units)-1))

    def test_oversized_more_true_and_mixed_continuation(self):
        content = '☃' * 2800
        def continuation(prompt):
            self.assertIn('Continue wordfreq.py using action=append', prompt)
            return {'action': 'append', 'path': 'wordfreq.py', 'data': '\nend', 'more': False}
        task = self.run_coder([
            self.raw_action('wordfreq.py', content, more=True), continuation,
            self.raw_action('test_unit.py', '# tests'),
            {'action': 'finish', 'summary': 'done', 'open_discrepancies': []}])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertEqual((self.root/'wordfreq.py').read_bytes(), (content + '\nend').encode())

    def test_total_file_ceiling_denies_before_any_split_is_written(self):
        def corrected(prompt):
            self.assertIn('total file size exceeds file limit', prompt)
            self.assertFalse((self.root/'wordfreq.py').exists())
            return self.raw_action('wordfreq.py', 'accepted')
        def append_denied(prompt):
            self.assertIn('total file size exceeds file limit', prompt)
            self.assertEqual((self.root/'wordfreq.py').read_bytes(), b'accepted')
            return self.raw_action('test_unit.py', '# tests')
        task = self.run_coder([
            self.raw_action('wordfreq.py', '☃' * 43691), corrected,
            {'action': 'append', 'path': 'wordfreq.py', 'data': 'x' * 131072},
            append_denied,
            {'action': 'finish', 'summary': 'done', 'open_discrepancies': []}])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertEqual((self.root/'wordfreq.py').read_bytes(), b'accepted')
        with self.store.transaction() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM events WHERE kind='oversized_accepted_split'").fetchone()[0], 0)

    def test_raw_writes_retain_broker_scope_and_file_byte_caps(self):
        from optimal_harness.model import parse_action
        broker = FileBroker(self.root, [], ['allowed'], max_bytes=5)
        first = parse_action(self.raw_action('allowed', 'é', more=True))
        broker.write(first['path'], first['data'])
        final = parse_action(self.raw_action('allowed', '☃', 'append'))
        broker.write(final['path'], final['data'], append=True)
        self.assertEqual((self.root/'allowed').read_bytes(), 'é☃'.encode())
        for path, text, append in (('allowed', 'x', True), ('forbidden', 'x', False),
                                   ('../escape', 'x', False)):
            action = parse_action(self.raw_action(path, text, 'append' if append else 'write'))
            with self.assertRaises(PolicyError):
                broker.write(action['path'], action['data'], append=append)
        self.assertEqual((self.root/'allowed').read_bytes(), 'é☃'.encode())

    def test_writer_system_instructions_default_to_raw(self):
        from optimal_harness.model import RawWritePrompt, SYSTEM_PREFIX
        self.assertIn('format=raw', RawWritePrompt.system_prefix)
        self.assertNotIn('Your entire reply MUST be one JSON object', RawWritePrompt.system_prefix)
        self.assertNotIn('format=raw', SYSTEM_PREFIX)
        for role in ('coder', 'integrator', 'fixer'):
            text = (BASE/'optimal_harness/charters'/ (role + '.md')).read_text()
            self.assertIn('raw is the DEFAULT', text)
            self.assertIn('---CONTENT---', text)

    def test_raw_parser_rejects_malformed_headers_and_preserves_payload(self):
        from optimal_harness.model import parse_action, ModelError
        content = '\n---CONTENT---\n{"action":"fail","reason":"literal"}\n  '
        self.assertEqual(parse_action(self.raw_action('x', content))['data'], content)
        self.assertEqual(parse_action(self.raw_action('x', ''))['data'], '')
        self.assertEqual(parse_action('{"action":"write","path":"x","data":"legacy"}')['data'], 'legacy')
        for text in (
            '{"action":"write","path":"x","format":"raw","data":"ambiguous"}\n---CONTENT---\nx',
            '{"action":"write","path":"x","format":"raw"}\n ---CONTENT---\nx',
            '{"action":"write","path":"x","format":"raw"}\nmissing',
            '{"action":"read","path":"x","format":"raw"}\n---CONTENT---\nx',
            '{"action":"append","path":"x","format":"raw","more":1}\n---CONTENT---\nx',
            '{"action":"write","path":"x","format":"raw","extra":true}\n---CONTENT---\nx'):
            with self.subTest(text=text), self.assertRaises(ModelError):
                parse_action(text)

    def test_coder_oversized_non_write_retries_are_bounded_without_writes(self):
        task = self.run_coder([{'action': 'read', 'path': 'x'*5000}] * 3)
        self.assertIn('coder_action_format_exhausted', task['failure'])
        self.assertEqual(self.engine.queue.calls, 3)
        self.assertFalse((self.root/'wordfreq.py').exists())

    def test_chunk_marker_requires_boolean_and_append_text(self):
        from optimal_harness.model import parse_action, ModelError
        for value in (0, 1, 'true', None):
            with self.assertRaises(ModelError):
                parse_action(json.dumps({'action': 'write', 'path': 'x', 'data': 'x', 'more': value}))
        with self.assertRaises(ModelError):
            parse_action(json.dumps({'action': 'append', 'path': 'x', 'data': {}, 'more': False}))

    def test_coder_truncated_retries_are_bounded(self):
        task = self.run_coder(['{"action":"write","data":"unfinished'] * 3)
        self.assertIn('coder_action_format_exhausted', task['failure'])
        self.assertEqual(self.engine.queue.calls, 3)
        self.assertFalse((self.root/'wordfreq.py').exists())

    def test_coder_partial_file_cannot_finish(self):
        task = self.run_coder([
            {'action': 'write', 'path': 'wordfreq.py', 'data': 'partial', 'more': True},
            {'action': 'finish', 'summary': 'premature'}])
        self.assertIn('incomplete_chunked_files:wordfreq.py', task['failure'])

    def test_append_preserves_write_scope_and_file_limit(self):
        broker = FileBroker(self.root, [], ['allowed'], max_bytes=5)
        broker.write('allowed', 'é')
        broker.write('allowed', '☃', append=True)
        self.assertEqual((self.root/'allowed').read_text(), 'é☃')
        with self.assertRaises(PolicyError):
            broker.write('allowed', 'x', append=True)
        for path in ('spec.md', '../escape'):
            with self.assertRaises(PolicyError):
                broker.write(path, 'x', append=True)
        self.assertEqual((self.root/'allowed').read_text(), 'é☃')

    def test_utf8_pages_never_replace_or_skip_characters(self):
        text='A☃BéC'; (self.root/'unicode').write_text(text)
        b=FileBroker(self.root,['unicode'],[])
        first=b.read('unicode',0,3)  # Incomplete snowman stays for next page.
        self.assertEqual(first,'A')
        second=b.read('unicode',len(first.encode()),4)
        self.assertEqual(second,'☃B')
        third=b.read('unicode',len((first+second).encode()),3)
        self.assertEqual(first+second+third,text)
        with self.assertRaises(PolicyError): b.read('unicode',2,3)

    def test_charter_tamper_stops_before_model_call(self):
        self.submit(); (self.root/'vault/charters/intake.md').write_text('weaker charter')
        asyncio.run(self.engine.serve(1))
        self.assertEqual(self.engine.queue.calls,0)
        self.assertIn('pinned input or charter changed',self.store.tasks('wordcounter')[0]['failure'])

    def test_stale_worker_cannot_prepare_or_checkpoint(self):
        self.submit(); task=self.store.claim('old',60)
        with self.store.transaction() as c: c.execute('UPDATE tasks SET lease_until=0 WHERE id=?',(task['id'],))
        self.store.claim('new',60)
        with self.assertRaises(LeaseLost): self.engine._prepare(self.root,task)
        (self.root/'data').write_text('content')
        with self.assertRaises(LeaseLost): self.engine._checkpoint(self.root,task,'data',{},'test')
        self.assertFalse((self.root/'vault/checkpoints').exists())

    def test_pin_rechecked_before_coordinator_grade(self):
        self.submit(); self.store.project_status('wordcounter','AWAITING_COORDINATOR')
        (self.root/'fixtures/words.json').write_text('[]')
        with self.assertRaisesRegex(PolicyError,'ground truth changed'):
            self.engine.coordinator_verdict('wordcounter',True,'test only')
        self.assertEqual(self.store.project('wordcounter')['status'],'ESCALATED')

    def test_fingerprint_ignores_ephemeral_paths_and_timings_not_assertions(self):
        root=Path('/example/work')
        a='independent acceptance failed: /example/work/.harness/commands/cmd-a/test.py\nAssertionError: 1 != 2\nRan 5 tests in 0.015s'
        b=a.replace('cmd-a','cmd-b').replace('0.015s','0.947s')
        self.assertEqual(fingerprint([a],root),fingerprint([b],root))
        self.assertNotEqual(fingerprint([a],root),fingerprint([b.replace('1 != 2','1 != 3')],root))

    def test_declared_writable_alias_and_directory_deliverables(self):
        self.plan['stages']['INTAKE']['writes']=[]
        self.plan['stages']['INTAKE']['writable']=['artifacts/**']
        # Other stage outputs within this directory would be mutable. Narrow to a
        # new directory to exercise explicit directory syntax without overlap.
        self.plan['stages']['INTAKE']['writable']=['intake/**']
        self.plan['stages']['INTAKE']['deliverables']=['intake/report.json']
        self.submit()
        task=self.store.claim('worker',60)
        b,_,_,_=self.engine._prepare(self.root,task)
        b.write('intake/report.json','{}')
        self.assertEqual((self.root/'intake/report.json').read_text(),'{}')

    def test_discrepancy_schema_is_code_evaluated(self):
        from optimal_harness.verifier import Verifier
        (self.root/'report.json').write_text('{"unresolved":["real concern"]}')
        report=Verifier(self.root).verify([{'type':'discrepancy-free','path':'report.json','key':'unresolved'}])
        self.assertFalse(report['passed'])
        (self.root/'report.json').write_text('{"unresolved":[]}')
        self.assertTrue(Verifier(self.root).verify([{'type':'discrepancy-free','path':'report.json','key':'unresolved'}])['passed'])

    def test_board_budget_breaker_is_durable_and_handoffable(self):
        from optimal_harness.ledger import BudgetExceeded
        self.plan['stages']['INTAKE']['budget']={'max_fresh_tokens':100}
        self.submit(); task=self.store.claim('worker',60)
        status=self.store.meter(task, prompt=150, cached=100, completion=50)
        self.assertEqual(status['usage']['fresh'],100)
        self.assertTrue(status['stop']); self.assertFalse(status['budget_exceeded'])
        status=self.store.meter(task,prompt=1,calls=1)
        self.assertTrue(status['budget_exceeded'])
        reopened=Store(self.base/'board.db')
        with self.assertRaises(BudgetExceeded):
            reopened.commit(task,lambda c:(self.root/'forbidden').write_text('bad'))
        self.assertFalse((self.root/'forbidden').exists())
        reopened.handoff(task,{'status':'error','budget_exceeded':False})
        with reopened.transaction() as c:
            body=json.loads(c.execute('SELECT body FROM handoffs').fetchone()['body'])
        self.assertTrue(body['budget_exceeded'])
        self.assertEqual(body['trusted_meter']['fresh'],101)
        with reopened.transaction() as c: c.execute('UPDATE tasks SET lease_until=0 WHERE id=?',(task['id'],))
        new=reopened.claim('retry',60)
        self.assertEqual(new['generation'],2)
        status=reopened.meter(new,prompt=5,completion=2)
        self.assertEqual(status['usage']['fresh'],7)
        self.assertTrue(status['stop'])  # Stage envelope survives generation changes.

    def test_fingerprint_repeat_counts_survive_intervening_defects(self):
        self.submit()
        results=[self.store.failure_cycle('wordcounter', defect) for defect in ('A','B','A','B','A')]
        self.assertEqual(results,[(1,1),(2,1),(3,2),(4,2),(5,3)])
        reopened=Store(self.base/'board.db')
        self.assertEqual(reopened.failure_cycle('wordcounter','B'),(6,3))

    def test_object_write_schema_feedback_preserves_source_without_chat_history(self):
        from optimal_harness.verifier import LIFECYCLE
        from optimal_harness.model import parse_action
        import re
        lines=[line for line in (self.root/'spec.md').read_text().splitlines() if ':' in line]
        audit={'slots':{key:{'status':'citation','quote':line,'because':'Explicit rule.'} for key,line in zip(LIFECYCLE,lines)}}
        incomplete=json.loads(json.dumps(audit)); del incomplete['slots']['exit_behavior']
        def after_invalid(prompt):
            self.assertIn('PROTOCOL_ERROR',prompt)
            self.assertIn('Input existence:',prompt)
            self.assertNotIn('earlier malformed assistant response',prompt)
            return {'action':'write','path':'artifacts/intake.json','data':incomplete}
        def after_schema(prompt):
            self.assertIn('missing=exit_behavior',prompt)
            self.assertIn('Input existence:',prompt)
            return {'action':'write','path':'artifacts/intake.json','data':audit['slots']}
        def handoff(prompt):
            path=re.search(r'vault/runs/[0-9a-f]{16}/a[0-9]+/handoff.json',prompt).group()
            return {'action':'write','path':path,'data':{'status':'ready','open_discrepancies':[]}}
        class Queue(FakeQueue):
            async def request(q,prompt,*args):
                q.calls+=1; value=q.actions.popleft()
                if callable(value): value=value(prompt)
                return Completion(value if isinstance(value,str) else json.dumps(value),100,80,30)
        self.engine.queue=Queue([{'action':'read','path':'spec.md'},'{ malformed earlier malformed assistant response',
                                 after_invalid,after_schema,handoff,{'action':'finish','summary':'checkpointed'},
                                 {'action':'fail','reason':'scripted test stops after intake'}])
        self.submit(); asyncio.run(self.engine.serve(1))
        self.assertEqual(self.store.tasks('wordcounter')[0]['status'],'SUCCEEDED')
        self.assertEqual(json.loads((self.root/'artifacts/intake.json').read_text()),audit)
        with self.store.transaction() as c:
            denials=[json.loads(r[0]) for r in c.execute("SELECT payload FROM events WHERE kind='tool_denied'")]
        self.assertEqual(len([d for d in denials if d['task'] == self.store.tasks('wordcounter')[0]['id']]),1)
        self.assertIn('missing=exit_behavior',denials[0]['reason'])
        self.assertEqual(parse_action('{"action":"write","path":"x.json","data":[]}')['data'],[])

    def test_intake_pointer_exact_lines_and_boundaries(self):
        from optimal_harness.runtime import intake_document
        from optimal_harness.verifier import LIFECYCLE
        text = 'α first\nsecond\n\nlast'
        def document(start, end):
            return {'slots': {key: {'slot': key, 'source': 'spec.md',
                    'start_line': start, 'end_line': end, 'because': 'rule'} for key in LIFECYCLE}}
        for start, end, expected in ((1, 2, 'α first\nsecond\n'), (4, 4, 'last')):
            result = intake_document(document(start, end), lambda source: text)
            self.assertEqual(result['slots']['invocation']['quote'], expected)
        for start, end in ((0, 1), (1, 5), (3, 2), (3, 3), (True, 2), (1, '2')):
            with self.subTest(start=start, end=end), self.assertRaises(PolicyError):
                intake_document(document(start, end), lambda source: text)

    def test_intake_span_rejection_feedback(self):
        from optimal_harness.runtime import intake_document
        from optimal_harness.verifier import LIFECYCLE
        text = 'before rule\n  \n\nafter rule\n'
        for start, end, reason in ((2, 3, 'empty line span'), (3, 2, 'invalid inclusive line span'),
                                   (0, 9, 'invalid inclusive line span'),
                                   (None, None, 'invalid inclusive line span')):
            with self.subTest(start=start, end=end):
                doc = {'slots': {key: {'source': 'spec.md', 'start_line': start,
                       'end_line': end, 'because': 'rule'} for key in LIFECYCLE}}
                with self.assertRaises(PolicyError) as caught:
                    intake_document(doc, lambda source: text)
                message = str(caught.exception)
                for part in (LIFECYCLE[0], reason, 'spec total lines=4', 'line 1: before rule'):
                    self.assertIn(part, message)
                if start is not None:
                    self.assertIn('line 4: after rule', message)
        empty = {'slots': {key: {'source': 'spec.md', 'start_line': 1,
                 'end_line': 1, 'because': 'rule'} for key in LIFECYCLE}}
        with self.assertRaisesRegex(PolicyError, 'spec total lines=0.*none'):
            intake_document(empty, lambda source: '')

    def test_intake_valid_identical_rewrite_denied_then_finish(self):
        from optimal_harness.runtime import INTAKE_COMPLETE
        from optimal_harness.verifier import LIFECYCLE
        doc = {'slots': {key: {'status': 'citation', 'quote': 'Input', 'because': 'rule'}
                         for key in LIFECYCLE}}
        write = {'action': 'write', 'path': 'artifacts/intake.json', 'data': doc}
        def repeat(prompt):
            self.assertIn(INTAKE_COMPLETE, prompt)
            return write
        def finish(prompt):
            self.assertIn('TOOL_DENIED: ' + INTAKE_COMPLETE, prompt)
            return {'action': 'finish', 'summary': 'audit complete', 'open_discrepancies': []}
        task = self.run_coder([write, repeat, finish], role='INTAKE')
        self.assertEqual(task['status'], 'SUCCEEDED')
        with self.store.transaction() as c:
            denials = [json.loads(r[0]) for r in c.execute("SELECT payload FROM events WHERE kind='tool_denied'")]
            writes = [json.loads(r[0]) for r in c.execute("SELECT payload FROM events WHERE kind='tool_action'")]
        self.assertEqual([d['reason'] for d in denials], [INTAKE_COMPLETE])
        self.assertEqual(sum(w.get('path_or_name') == 'artifacts/intake.json' for w in writes), 1)

    def test_intake_two_pointer_rejections_allow_free_text_fallback(self):
        from optimal_harness.verifier import LIFECYCLE
        from copy import deepcopy
        text = (self.root/'spec.md').read_text()
        blank = next(n for n, line in enumerate(text.splitlines(), 1) if not line.strip())
        doc = {'slots': {key: {'status': 'citation', 'quote': 'Input', 'because': 'rule'}
                         for key in LIFECYCLE}}
        bad = deepcopy(doc)
        bad['slots']['input_access'] = {'source': 'spec.md', 'start_line': blank,
                                        'end_line': blank, 'because': 'rule'}
        def second(prompt):
            self.assertIn('empty line span: input_access', prompt)
            value = deepcopy(bad)
            value['slots']['input_access']['because'] = 'second attempt'
            return {'action': 'write', 'path': 'artifacts/intake.json', 'data': value}
        def fallback(prompt):
            self.assertIn('empty line span: input_access', prompt)
            self.assertIn('instead of retrying pointers', prompt)
            return {'action': 'write', 'path': 'artifacts/intake.json', 'data': doc}
        task = self.run_coder([
            {'action': 'write', 'path': 'artifacts/intake.json', 'data': bad}, second, fallback,
            {'action': 'finish', 'summary': 'audit complete', 'open_discrepancies': []}], role='INTAKE')
        self.assertEqual(task['status'], 'SUCCEEDED')
        self.assertEqual(json.loads((self.root/'artifacts/intake.json').read_text()), doc)

    def test_intake_format_normalization_does_not_invent_evidence(self):
        from optimal_harness.runtime import intake_document
        from optimal_harness.verifier import LIFECYCLE, Verifier
        flat = {key: {'status': 'citation', 'quote': 'invented evidence',
                      'because': 'Explicit rule.'} for key in LIFECYCLE}
        document = intake_document(flat)
        self.assertEqual(document, {'slots': flat})
        (self.root/'audit.json').write_text(json.dumps(document))
        self.assertEqual(len(Verifier(self.root).intake('audit.json', 'spec.md')), 9)
        self.assertTrue(Verifier(self.root).embedded_quotes('audit.json', ['spec.md'], 'spec.md'))
        for bad in ({key: 'unstructured claim' for key in LIFECYCLE},
                    {'slots': {key: {'status': 'citation', 'quote': 'x'} for key in LIFECYCLE}},
                    [], 'not json'):
            with self.assertRaises(PolicyError):
                intake_document(bad)
        gaps = {'slots': {key: {'status': 'gap', 'detail': 'Undefined'} for key in LIFECYCLE}}
        self.assertEqual(intake_document(gaps), gaps)
        (self.root/'audit.json').write_text(json.dumps(gaps))
        self.assertTrue(all('unresolved spec gap' in error for error in
                            Verifier(self.root).intake('audit.json', 'spec.md')))

    def test_intake_format_retry_bound_never_checkpoints_invalid_document(self):
        from optimal_harness.verifier import LIFECYCLE
        class Queue(FakeQueue):
            async def request(q, prompt, *args):
                q.calls += 1
                if q.calls > 1:
                    self.assertIn('INTAKE schema:', prompt)
                # Different malformed values avoid the generic repeated-action breaker.
                value = {key: 'invalid ' + str(q.calls) for key in LIFECYCLE}
                return Completion(json.dumps({'action': 'write', 'path': 'artifacts/intake.json',
                                              'data': value}), 100, 80, 30)
        self.engine.queue = Queue([])
        self.submit(); asyncio.run(self.engine.serve(1))
        self.assertEqual(self.engine.queue.calls, 9)
        self.assertIn('intake_format_exhausted', self.store.tasks('wordcounter')[0]['failure'])
        self.assertFalse((self.root/'artifacts/intake.json').exists())
        self.assertFalse(any(cp['path'] == 'artifacts/intake.json'
                             for cp in self.store.checkpoints('wordcounter')))

    def test_unhealthy_lane_refuses_further_work_without_explicit_recovery(self):
        from optimal_harness.model import LocalChatClient,ModelQueue,ModelError
        queue=ModelQueue(LocalChatClient('http://127.0.0.1:1/v1','test'),self.base/'model.lock')
        async def run():
            await queue.start()
            queue.healthy=False
            try:
                with self.assertRaisesRegex(ModelError,'explicit recovery'):
                    await queue.request('no request should execute',1,0.1)
            finally: await queue.stop()
        asyncio.run(run())




class StageRetryTests(unittest.TestCase):
    setUp = RuntimeTests.setUp
    submit = RuntimeTests.submit
    # Reuse fixture helpers, without rerunning the parent test suite.
    def test_three_attempts_preserve_reasons_and_context(self):
        self.engine.queue = FakeQueue([{'action': 'unknown_' + str(i)} for i in range(9)])
        self.submit()
        asyncio.run(self.engine.serve())
        task = self.store.tasks('wordcounter')[0]
        self.assertEqual(task['attempts'], 3)
        self.assertEqual(len(task['attempt_failures']), 3)
        self.assertEqual(self.store.project('wordcounter')['status'], 'ESCALATED')
        for failure in task['attempt_failures']:
            self.assertIn('intake_action_format_exhausted:', failure['reason'])
            self.assertIn('Action template:', failure['last_context'])
        with self.store.transaction() as c:
            reports = c.execute('SELECT count(*) FROM handoffs').fetchone()[0]
        self.assertEqual(reports, 3)
        self.assertTrue(self.store.audit()['valid'])

    def test_deadline_kill_retries_with_shared_budget(self):
        from optimal_harness.model import ModelError
        from optimal_harness.verifier import LIFECYCLE
        from unittest.mock import patch
        lines = [line for line in (self.root/'spec.md').read_text().splitlines() if ':' in line]
        audit = {'slots': {key: {'status': 'citation', 'quote': line, 'because': 'Explicit requirement'}
                           for key, line in zip(LIFECYCLE, lines)}}
        class Queue(FakeQueue):
            async def request(q, prompt, max_tokens, timeout):
                if q.calls == 0:
                    q.calls += 1
                    raise ModelError('model_request_killed_on_deadline')
                return await super().request(prompt, max_tokens, timeout)
        self.engine.queue = Queue([{'action': 'write', 'path': 'artifacts/intake.json', 'data': audit},
                                   {'action': 'finish', 'summary': 'Complete', 'open_discrepancies': []}])
        self.submit()
        with patch.object(self.engine, '_advance'):
            asyncio.run(self.engine.serve())
        task = self.store.tasks('wordcounter')[0]
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertEqual(task['attempts'], 2)
        self.assertEqual(task['attempt_failures'][0]['reason'], 'model_request_killed_on_deadline')
        self.assertEqual(self.store.stage_usage(task)['calls'], 2)
        with self.store.transaction() as c:
            starts = c.execute('SELECT started FROM attempt_usage WHERE task=?', (task['id'],)).fetchall()
        self.assertEqual(len(starts), 2)
        self.assertLessEqual(starts[0][0], starts[1][0])

    def test_budget_exhaustion_prevents_retry(self):
        self.plan['stages']['INTAKE']['budget'] = {'max_calls': 3}
        self.engine.queue = FakeQueue([{'action': 'unknown_' + str(i)} for i in range(3)])
        self.submit()
        asyncio.run(self.engine.serve())
        task = self.store.tasks('wordcounter')[0]
        self.assertEqual(task['attempts'], 1)
        self.assertEqual(self.engine.queue.calls, 3)
        self.assertEqual(self.store.project('wordcounter')['status'], 'ESCALATED')

    def test_retries_share_remaining_call_budget(self):
        self.plan['stages']['INTAKE']['budget'] = {'max_calls': 4}
        self.engine.queue = FakeQueue([{'action': 'unknown_' + str(i)} for i in range(4)])
        self.submit()
        asyncio.run(self.engine.serve())
        task = self.store.tasks('wordcounter')[0]
        self.assertEqual(task['attempts'], 2)
        self.assertEqual(self.engine.queue.calls, 4)
        self.assertIn('circuit_breaker:model_calls', task['failure'])

    def test_retry_classifier_excludes_external_failures(self):
        from optimal_harness.runtime import model_side_failure
        for reason in ('circuit_breaker:model_calls', 'citation_check_failed:x',
                       'modified-after-checkpoint:x', 'obligation_unaddressed:x',
                       'error:missing_deliverables:x', 'honest_worker_failure:x',
                       'pinned input or charter changed', 'model_request_killed_on_deadline_extra'):
            self.assertFalse(model_side_failure(reason), reason)
        for reason in ('model_request_killed_on_deadline', 'review_section_exhausted:observations', 'coder_handoff_exhausted:x',
                       'error:no_progress:invalid_actions', 'error:no_progress:repeated_action'):
            self.assertTrue(model_side_failure(reason), reason)


    def test_retry_rechecks_and_checkpoints_existing_identical_deliverable(self):
        from optimal_harness.verifier import LIFECYCLE
        from unittest.mock import patch
        lines = [line for line in (self.root/'spec.md').read_text().splitlines() if ':' in line]
        audit = {'slots': {key: {'status': 'citation', 'quote': line, 'because': 'Explicit requirement'}
                           for key, line in zip(LIFECYCLE, lines)}}
        write = {'action': 'write', 'path': 'artifacts/intake.json', 'data': audit}
        self.engine.queue = FakeQueue([write] + [{'action': 'read', 'path': 'spec.md'}]*4 +
                                     [write, {'action': 'finish', 'summary': 'Complete', 'open_discrepancies': []}])
        self.submit()
        with patch.object(self.engine, '_advance'):
            asyncio.run(self.engine.serve())
        task = self.store.tasks('wordcounter')[0]
        self.assertEqual(task['status'], 'SUCCEEDED')
        self.assertEqual(task['attempts'], 2)
        self.assertEqual(self.engine.integrity_errors('wordcounter', self.root), [])
