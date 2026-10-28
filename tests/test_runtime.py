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

    def run_coder(self, actions, role='CODER'):
        from optimal_harness.engine import make_brief
        from unittest.mock import patch
        self.submit()
        task = self.store.claim('worker', 60)
        task['stage'] = role
        task['brief'] = make_brief(role, self.store.project('wordcounter')['plan'])
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
        for role in ('INTAKE', 'INTEGRATOR', 'QA', 'REVIEW', 'FIXER'):
            with self.subTest(role=role):
                # Each case needs a separate project/store.
                if role != 'INTAKE':
                    self.setUp()
                task = self.run_coder([
                    {'action': 'write', 'path': 'never-created', 'data': 'x'*5000}
                ] * 3, role=role)
                self.assertIn('review_section_exhausted:reviewed_stage' if role == 'REVIEW' else role.lower() + '_action_format_exhausted', task['failure'])
                self.assertEqual(self.engine.queue.calls, 3)
                self.assertFalse((self.root/'never-created').exists())

    def test_qa_chunked_writes(self):
        target = self.plan['stages']['QA']['deliverables'][0]
        task = self.run_coder([
            {'action': 'write', 'path': target, 'data': 'first', 'more': True},
            {'action': 'append', 'path': target, 'data': 'last', 'more': False},
            {'action': 'fail', 'reason': 'test stops after writes'},
        ], role='QA')
        self.assertEqual((self.root/target).read_text(), 'firstlast')
        self.assertIn('test stops after writes', task['failure'])

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

    def test_review_sections_rewrite_feedback_and_full_assembly(self):
        target = self.plan['stages']['REVIEW']['deliverables'][0]
        def write(key, value):
            return {'action': 'write', 'path': target, 'data': {key: value}}
        def correction(prompt):
            self.assertIn('ATTEMPTED test case even when no defect is found', prompt)
            self.assertIn('accepted_sections', prompt)
            self.assertFalse((self.root/target).exists())
            return write('counterexamples', ['attempt missing input'])
        def rewrite(prompt):
            self.assertIn('accepted_section', prompt)
            return write('observations', ['revised observation'])
        task = self.run_coder([
            write('reviewed_stage', ''), write('observations', ['original']),
            write('counterexamples', []), correction, rewrite,
            write('unresolved', []),
            {'action': 'finish', 'summary': 'reviewed', 'open_discrepancies': []},
        ], role='REVIEW')
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        report = json.loads((self.root/target).read_text())
        self.assertEqual(report, {'reviewed_stage': '', 'counterexamples': ['attempt missing input'],
                                 'observations': ['revised observation'], 'unresolved': []})
        from optimal_harness.review import validate_document
        validate_document(report, '')

    def test_review_independent_budgets_and_rewrite_after_assembly(self):
        target = self.plan['stages']['REVIEW']['deliverables'][0]
        def write(key, value):
            return {'action': 'write', 'path': target, 'data': {key: value}}
        def rejected_rewrite(prompt):
            self.assertIn('REVIEW section counterexamples', prompt)
            self.assertEqual(json.loads((self.root/target).read_text())['counterexamples'], ['attempt'])
            return write('counterexamples', ['revised attempt'])
        task = self.run_coder([
            write('observations', {}), write('observations', None), write('observations', []),
            write('unresolved', {}), write('unresolved', None), write('unresolved', []),
            write('reviewed_stage', ''), write('counterexamples', ['attempt']),
            write('counterexamples', []), rejected_rewrite,
            {'action': 'finish', 'summary': 'reviewed', 'open_discrepancies': []},
        ], role='REVIEW')
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertEqual(json.loads((self.root/target).read_text())['counterexamples'], ['revised attempt'])

    def test_review_oversized_section_has_own_budget(self):
        target = self.plan['stages']['REVIEW']['deliverables'][0]
        task = self.run_coder([
            {'action': 'write', 'path': target, 'data': {'counterexamples': ['x' * 5000]}}
        ] * 3, role='REVIEW')
        self.assertIn('review_section_exhausted:counterexamples', task['failure'])
        self.assertFalse((self.root/target).exists())

    def test_review_section_schema_feedback(self):
        from optimal_harness.review import validate_section
        for key, value, expected in [('reviewed_stage', 'QA', "must equal 'CODER'"),
                                     ('counterexamples', [], 'ATTEMPTED'),
                                     ('counterexamples', [' '], 'nonempty'),
                                     ('observations', {}, 'must be a list'),
                                     ('unresolved', None, 'must be a list')]:
            with self.subTest(key=key), self.assertRaisesRegex(PolicyError, expected):
                validate_section(key, value, 'CODER')

    def test_review_per_section_exhaustion(self):
        target = self.plan['stages']['REVIEW']['deliverables'][0]
        def write(key, value):
            return {'action': 'write', 'path': target, 'data': {key: value}}
        task = self.run_coder([write('counterexamples', []), write('observations', {}),
                               write('counterexamples', ['']), write('observations', []),
                               write('counterexamples', [' '])], role='REVIEW')
        self.assertIn('review_section_exhausted:counterexamples', task['failure'])
        self.assertFalse((self.root/target).exists())

    def test_review_rejects_whole_document_and_chunks(self):
        target = self.plan['stages']['REVIEW']['deliverables'][0]
        task = self.run_coder([
            {'action': 'write', 'path': target, 'data': {'observations': [], 'unresolved': []}},
            {'action': 'write', 'path': target, 'data': '{', 'more': True},
            {'action': 'append', 'path': target, 'data': '}'},
        ], role='REVIEW')
        self.assertIn('review_section_exhausted:reviewed_stage', task['failure'])
        self.assertFalse((self.root/target).exists())

    def test_generated_handoff_exhaustion_reports_error_without_acceptance(self):
        task = self.run_coder([
            {'action': 'finish', 'summary': 'invalid', 'open_discrepancies': None}
        ] * 3)
        self.assertIn('coder_handoff_exhausted:handoff_not_ready_or_malformed:finish.open_discrepancies', task['failure'])
        with self.store.transaction() as c:
            kinds = [r[0] for r in c.execute('SELECT kind FROM events ORDER BY rowid')]
        self.assertIn('handoff_error_reported', kinds)
        self.assertNotIn('handoff_accepted', kinds)

    def test_coder_chunks_recover_from_truncation_and_oversize(self):
        import re
        def after_bad(prompt):
            self.assertIn('Use a smaller chunk', prompt)
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
            {'action': 'write', 'path': 'wordfreq.py', 'data': 'x' * 5000},
            after_bad, continuation,
            {'action': 'write', 'path': 'test_unit.py', 'data': '# tests\n'},
            handoff, {'action': 'finish', 'summary': 'done'}])
        self.assertEqual(task['status'], 'SUCCEEDED')
        self.assertEqual((self.root/'wordfreq.py').read_text(), 'α\nβ\n')
        self.assertEqual(self.engine.queue.calls, 7)

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

    def test_coder_invalid_size_retries_are_bounded_without_writes(self):
        task = self.run_coder([{'action': 'write', 'path': 'wordfreq.py', 'data': 'x'*5000}] * 3)
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
        self.assertFalse(status['stop'])

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
        self.assertEqual(len(denials),1)
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
        self.assertEqual(self.engine.queue.calls, 3)
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


