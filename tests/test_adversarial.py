"""Adversarial regression tests; real broker/SQLite/HTTP, fake model where explicit."""
import asyncio
import copy
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from unittest.mock import patch
from optimal_harness.api import BoardAPI
from optimal_harness.engine import Engine
from optimal_harness.ledger import Ledger, BudgetExceeded, DEFAULT_BUDGET
from optimal_harness.model import Completion, LocalChatClient, ModelError, ModelQueue, SYSTEM_PREFIX, parse_action
from optimal_harness.policy import FileBroker, PolicyError, sandbox_command
from optimal_harness.store import Store, LeaseLost
from optimal_harness.verifier import Verifier, digest, validate_plan, LIFECYCLE
from tests.test_harness import FakeQueue

BASE = Path(__file__).resolve().parents[1]


class Adversarial(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=BASE / 'runs')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / 'work'
        self.grader = self.base / 'grader'
        example = BASE / 'examples' / 'word-counter'
        shutil.copytree(example / 'work', self.root)
        shutil.copytree(example / 'grader', self.grader)
        self.plan = json.loads((example / 'plan.json').read_text())
        self.store = Store(self.base / 'board.db')
        self.engine = Engine(self.store, FakeQueue([]))

    def submit(self):
        return self.engine.submit(self.root, self.plan, self.grader / 'acceptance.json')

    def test_undeclared_read_refusal(self):
        with self.assertRaises(PolicyError):
            FileBroker(self.root, ['spec.md'], []).read('fixtures/words.json')

    def test_undeclared_write_refusal(self):
        pin = digest(self.root / 'spec.md')
        with self.assertRaises(PolicyError):
            FileBroker(self.root, [], ['new.txt']).write('spec.md', 'evil')
        self.assertEqual(digest(self.root / 'spec.md'), pin)

    def test_traversal_and_absolute_escape(self):
        for p in ('../secret', '/etc/passwd', 'a/../spec.md', 'a//b', './spec.md', 'a\\b', '.harness/x'):
            with self.subTest(path=p), self.assertRaises(PolicyError):
                FileBroker(self.root, [p], [p]).read(p)

    def test_symlink_parent_and_leaf_escape(self):
        (self.root / 'link').symlink_to(self.grader, target_is_directory=True)
        (self.root / 'leaf').symlink_to(self.root / 'spec.md')
        for p in ('link/acceptance.json', 'leaf'):
            with self.subTest(path=p), self.assertRaises(PolicyError):
                FileBroker(self.root, [p], [p]).write(p, 'evil')

    def test_leaked_grader_plan_rejection(self):
        self.plan['stages']['CODER']['reads'].append('../grader/acceptance.json')
        with self.assertRaises(PolicyError): self.submit()

    def test_hardlinked_grader_plan_rejection(self):
        import os
        os.link(self.grader / 'acceptance.json', self.root / 'leak.json')
        self.plan['stages']['CODER']['reads'].append('leak.json')
        with self.assertRaises(PolicyError): self.submit()

    def test_command_cannot_write_qa_tests_as_coder(self):
        c = self.plan['stages']['CODER']
        c['command_only'] = ['test_acceptance.py']
        c['commands'] = {'evil': {'argv': ['python3', '-c', 'pass'], 'writes': ['test_acceptance.py'], 'capture': 'reports/evil.json'}}
        c['deliverables'].append('reports/evil.json')
        with self.assertRaises(PolicyError): self.submit()

    def test_scope_overlap_cannot_write_spec(self):
        self.plan['stages']['CODER']['writes'].append('fixtures/**')
        with self.assertRaises(PolicyError): self.submit()

    def test_receipts_never_model_writable(self):
        self.plan['stages']['FIXER']['writes'].append('reports/**')
        with self.assertRaises(PolicyError): self.submit()

    def test_post_checkpoint_tamper_detected_at_stage(self):
        self.submit()
        task = self.store.claim('worker', 60)
        broker, evidence, handoff, _ = self.engine._prepare(self.root, task)
        path = 'artifacts/intake.json'
        broker.write(path, '{}')
        checkpoints = {}
        self.engine._checkpoint(self.root, task, path, checkpoints, 'test')
        (self.root / path).write_text('{"altered":true}')
        with self.assertRaisesRegex(RuntimeError, 'modified since checkpoint'):
            self.engine._check_stage(self.root, task, self.plan['stages']['INTAKE'], checkpoints, handoff)

    def test_receipt_tamper_detected_at_gate(self):
        self.submit()
        task = self.store.claim('worker', 60)
        path = 'reports/integration.json'
        (self.root / path).write_text('{"exit_code":0,"reason":"exited"}')
        self.engine._checkpoint(self.root, task, path, {}, 'test')
        (self.root / path).write_text('{"exit_code":0,"reason":"exited","fake":true}')
        self.assertIn('modified-after-checkpoint:'+path, self.engine.integrity_errors('wordcounter', self.root))
        self.engine._gate('wordcounter', self.store.project('wordcounter')['plan'], self.root)
        self.assertEqual(self.store.project('wordcounter')['status'], 'ESCALATED')

    def test_content_addressed_archive_tamper(self):
        self.submit()
        task = self.store.claim('worker', 60)
        (self.root / 'x').write_text('original')
        self.engine._checkpoint(self.root, task, 'x', {}, 'test')
        cp = self.store.checkpoints('wordcounter')[0]
        (self.root / cp['archive']).write_text('forged')
        self.assertTrue(self.engine.integrity_errors('wordcounter', self.root))

    def test_invented_citation_rejected_at_stage_completion(self):
        self.submit()
        task = self.store.claim('worker', 60)
        b, _, handoff, _ = self.engine._prepare(self.root, task)
        path = 'artifacts/intake.json'
        b.write(path, json.dumps({'quote': 'THIS NEVER EXISTED', 'source': 'spec.md', 'because': 'invented'}))
        b.write(handoff, '{"status":"ready","open_discrepancies":[]}')
        cp = {}
        self.engine._checkpoint(self.root, task, path, cp, 'test')
        with self.assertRaisesRegex(RuntimeError, 'citation_check_failed'):
            self.engine._check_stage(self.root, task, self.plan['stages']['INTAKE'], cp, handoff)

    def test_markdown_structured_quote_rejection(self):
        p = self.root / 'artifacts' / 'x.md'
        p.write_text('```json\n{"source":"spec.md","quote":"fake","because":"lie"}\n```')
        self.assertTrue(Verifier(self.root).embedded_quotes('artifacts/x.md', ['spec.md']))

    def test_missing_because_rejection(self):
        (self.root / 'x.json').write_text(json.dumps({'source': 'spec.md', 'quote': 'Invocation'}))
        self.assertTrue(Verifier(self.root).embedded_quotes('x.json', ['spec.md']))

    def test_stale_generation_cannot_commit_or_handoff(self):
        self.submit()
        old = self.store.claim('old', 60)
        with self.store.transaction() as c:
            c.execute('UPDATE tasks SET lease_until=0 WHERE id=?', (old['id'],))
        new = self.store.claim('new', 60)
        with self.assertRaises(LeaseLost):
            self.store.commit(old, lambda c: (self.root / 'stale').write_text('bad'))
        with self.assertRaises(LeaseLost): self.store.handoff(old, {'status': 'ready'})
        with self.assertRaises(LeaseLost): self.store.finish(old, 'SUCCEEDED')
        self.assertFalse((self.root / 'stale').exists())
        self.store.commit(new, lambda c: (self.root / 'current').write_text('ok'))
        self.assertEqual((self.root / 'current').read_text(), 'ok')

    def test_budget_breaker_exactness_fresh_minus_cached(self):
        clock = [0]
        l = Ledger({'max_calls': 2, 'max_fresh_tokens': 1000, 'max_seconds': 10}, clock=lambda: clock[0])
        l.charge(Completion('', 100, 80, 30))
        self.assertEqual((l.fresh, l.cached), (50, 80))
        cap, _ = l.reserve('abc')
        self.assertEqual(cap, 435)
        l.charge(Completion('', 500, 0, 450))
        self.assertEqual(l.fresh, 1000)
        with self.assertRaises(BudgetExceeded): l.reserve('')
        l2 = Ledger({'max_fresh_tokens': 1000})
        with self.assertRaisesRegex(BudgetExceeded, 'after_meter'):
            l2.charge(Completion('', 1000, 0, 1))
        self.assertEqual(l2.fresh, 1001)

    def test_wall_budget_exact_boundary(self):
        clock = [0]
        l = Ledger({'max_seconds': 10}, clock=lambda: clock[0])
        clock[0] = 10
        with self.assertRaisesRegex(BudgetExceeded, 'wall_clock'): l.reserve('')

    def test_default_production_budgets_and_dynamic_completion_cap(self):
        self.assertEqual((DEFAULT_BUDGET['max_calls'], DEFAULT_BUDGET['max_fresh_tokens'], DEFAULT_BUDGET['max_seconds']), (60, 200000, 1800))
        l = Ledger({'max_fresh_tokens': 20000})
        large, _ = l.reserve('abc')
        l.charge(Completion('', 16000, 0, 1000))
        small, _ = l.reserve('abc')
        self.assertGreater(large, 768)
        self.assertEqual(small, 2485)

    def test_budget_failure_handoff_still_recorded(self):
        self.plan['stages']['INTAKE']['budget'] = {'max_fresh_tokens': 1}
        self.submit()
        asyncio.run(self.engine.serve(1))
        with self.store.transaction() as c:
            rows = c.execute('SELECT body FROM handoffs').fetchall()
        self.assertEqual(len(rows), 1)
        body = json.loads(rows[0]['body'])
        self.assertTrue(body['budget_stopped'])
        self.assertFalse(body['budget_exceeded'])
        self.assertEqual(body['status'], 'error')

    def test_missing_deliverable_rejection(self):
        self.engine.queue = FakeQueue([{'action':'finish','summary':'pretend complete'}])
        self.submit()
        asyncio.run(self.engine.serve(1))
        self.assertIn('error:missing_deliverables', self.store.tasks('wordcounter')[0]['failure'])

    def test_paged_read_middle_of_large_file(self):
        (self.root / 'large').write_text('A'*10000+'MIDDLE'+'Z'*10000)
        b = FileBroker(self.root, ['large'], [], max_bytes=1024)
        self.assertEqual(b.read('large', 10000, 6), 'MIDDLE')
        with self.assertRaises(PolicyError): b.read('large', 0, 1025)
        with self.assertRaises(PolicyError): b.read('large', -1, 1)

    def test_exact_scope_and_explicit_directory_scope(self):
        b = FileBroker(self.root, [], ['artifacts/**'])
        b.write('artifacts/sub/x', 'yes')
        self.assertEqual(b.read('artifacts/sub/x'), 'yes')
        with self.assertRaises(PolicyError): b.write('artifacts-other/x', 'no')

    def test_real_command_full_untruncated_capture(self):
        result = sandbox_command(self.root, {'argv': ['python3', '-c', 'print("x"*70000)']})
        self.assertEqual(result['exit_code'], 0)
        self.assertEqual(len(result['stdout']), 70001)
        self.assertIn('containment', result)

    def test_real_command_detects_readonly_mount_mutation(self):
        (self.root / 'protected').write_text('pinned')
        with self.assertRaisesRegex(PolicyError, 'read-only mount'):
            sandbox_command(self.root, {'argv':['python3','-c','open("protected","w").write("evil")'], 'reads':['protected']})
        self.assertEqual((self.root / 'protected').read_text(), 'pinned')

    def test_command_timeout_is_honest(self):
        result = sandbox_command(self.root, {'argv':['python3','-c','import time; print("started",flush=True); time.sleep(10)'], 'timeout_seconds':0.1})
        self.assertIsNone(result['exit_code'])
        self.assertEqual(result['reason'], 'command_timeout')
        self.assertIn('started', result['stdout'])

    def test_isolation_required_fails_closed(self):
        with patch('optimal_harness.commands.shutil.which', return_value='/usr/bin/python3'), patch('optimal_harness.commands.sys.platform', 'darwin'):
            with self.assertRaisesRegex(PolicyError, 'requires_isolation'):
                sandbox_command(self.root, {'argv':['python3','-c','pass'], 'requires_isolation':True})

    def test_all_text_and_csv_verbs(self):
        (self.root / 'rows.csv').write_text('id,group,n\na,yes,3\nb,no,5\nc,yes,7\n')
        (self.root / 'set.json').write_text('["a","c"]')
        (self.root / 'source').write_text('abcdef')
        (self.root / 'target').write_text('bcd')
        (self.root / 'claims.json').write_text(json.dumps({'claims':[{'source':'source','quote':'abc','because':'exact'}]}))
        checks = [
            {'type':'contains','path':'source','text':'abc'},
            {'type':'not-contains','path':'source','text':'fake'},
            {'type':'verbatim','source':'source','source_slice':[1,4],'target':'target'},
            {'type':'csv-count','path':'rows.csv','where':{'group':'yes'},'expected':2},
            {'type':'csv-cell','path':'rows.csv','where':{'id':'b'},'column':'n','expected':'5'},
            {'type':'csv-set','path':'rows.csv','where':{'group':'yes'},'column':'id','target':'set.json'},
            {'type':'unchanged','path':'source','expected':digest(self.root/'source')},
            {'type':'citations-verbatim','path':'claims.json','sources':['source']},
        ]
        report = Verifier(self.root).verify(checks)
        self.assertTrue(report['passed'], report)
        for c in checks:
            bad = dict(c)
            if c['type'] == 'contains': bad['text']='fake'
            elif c['type'] == 'not-contains': bad['text']='abc'
            elif c['type'] == 'verbatim': bad['source_slice']=[0,3]
            elif c['type'] in ('csv-count', 'csv-cell', 'unchanged'): bad['expected']='wrong'
            elif c['type'] == 'csv-set': bad['where']={'group':'no'}
            else: bad['sources']=[]
            self.assertFalse(Verifier(self.root).verify([bad])['passed'], bad)

    def test_review_requires_counterexamples(self):
        self.submit()
        brief = {'role':'REVIEW', 'prior_stage':'CODER', 'permitted_reads':['spec.md']}
        tid = self.store.enqueue('wordcounter', 'REVIEW', 0, brief)
        with self.store.transaction() as c: c.execute("UPDATE tasks SET status='FAILED' WHERE id != ?", (tid,))
        task = self.store.claim('worker', 60)
        b, _, handoff, _ = self.engine._prepare(self.root, task)
        p = 'artifacts/review.json'
        b.write(p, json.dumps({'reviewed_stage':'CODER','counterexamples':[], 'observations':[], 'unresolved':[]}))
        b.write(handoff, '{"status":"ready","open_discrepancies":[]}')
        cp = {}; self.engine._checkpoint(self.root, task, p, cp, 'test')
        with self.assertRaisesRegex(RuntimeError, 'counterexample'):
            self.engine._check_stage(self.root, task, self.plan['stages']['REVIEW'], cp, handoff)

    def test_loopback_only_endpoint_and_no_spawn_schema(self):
        with self.assertRaises(ModelError): LocalChatClient('http://example.com:8080/v1', 'x')
        with self.assertRaises(ModelError): parse_action('{"action":"spawn","name":"helper"}')
        self.assertEqual(LocalChatClient('http://127.0.0.1:8080/v1', 'x').url, 'http://127.0.0.1:8080/v1/chat/completions')

    def test_actual_http_auth_roles_and_stale_lease(self):
        self.submit()
        credentials = {r:{'role':r, 'token':r+'-0123456789abcdef'} for r in ('admin','verifier','worker')}
        app = BoardAPI(self.engine, credentials)
        server = app.server()
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        def call(path, role, body):
            req = Request(f'http://127.0.0.1:{server.server_address[1]}'+path, data=json.dumps(body).encode(),
                          headers={'Authorization':'Bearer '+credentials[role]['token']})
            with urlopen(req, timeout=5) as response: return json.load(response)
        self.assertEqual(len(call('/status','admin',{})['projects']), 1)
        with self.assertRaises(HTTPError) as e: call('/grade','worker',{})
        self.assertEqual(e.exception.code, 403)
        task = call('/claim','worker',{})['task']
        self.assertEqual(task['lease_owner'], 'worker')
        report = call('/handoff','worker',{'task':task,'report':{'budget_exceeded':True,'status':'error'}})
        self.assertTrue(report['accepted'])
        self.assertTrue(report['requires_runtime_verification'])
        with self.store.transaction() as c:
            stored = c.execute('SELECT body FROM handoffs WHERE task=?',(task['id'],)).fetchone()
        self.assertTrue(json.loads(stored['body'])['budget_exceeded'])
        task['generation'] -= 1
        with self.assertRaises(HTTPError) as e: call('/heartbeat','worker',{'task':task})
        self.assertEqual(e.exception.code, 409)
        self.assertEqual(call('/audit','verifier',{})['valid'], True)
