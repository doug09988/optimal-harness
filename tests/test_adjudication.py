import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from optimal_harness import adjudication as a
from optimal_harness.ledger import Ledger
from optimal_harness.model import Completion


class Store:
    def __init__(self):
        self.events = []
        self.charges = 0

    def event(self, kind, payload):
        self.events.append((kind, payload))

    def project(self, project):
        return {'cycle': 2}

    def assert_lease(self, task):
        pass

    def meter(self, *args):
        self.charges += 1
        return {'budget_exceeded': False}


class AdjudicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'SPEC').write_text('Sort in ascending Unicode code-point order.')
        (self.root / 'test_public.py').write_text('def test_order():\n    assert False\n')
        self.store = Store()
        self.patch = patch.object(a, 'history', lambda store, project, kind:
                                  [p for k, p in store.events if k == kind and p['project'] == project])
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.plan = {'spec': 'SPEC', 'stages': {'QA': {'commands': {'qa': {
            'capture': 'suite.json', 'reads': ['test_public.py']}}}, 'INTEGRATOR': {'commands': {}}}}
        self.task = {'project_id': 'p', 'cycle': 3}
        self.responses = []
        async def request(prompt, cap, timeout):
            self.responses.append(json.loads(prompt))
            return Completion(self.reply.pop(0) if isinstance(self.reply, list) else self.reply, 10, 0, 5)
        self.engine = SimpleNamespace(store=self.store, queue=SimpleNamespace(request=request))
        self.reply = json.dumps(dict(violator='test', clause='ascending Unicode code-point order',
                                    derivation='Latin z precedes Greek alpha by code point.'))

    def gate(self, cycle, names=('test_order (test_public.Tests.test_order)',)):
        failed = {n: 'AssertionError: wrong expectation' for n in names}
        suite = dict(suite='suite.json', failed=failed, gate_failures=failed,
                     tests={n: {'source': 'def ' + n.split()[0] + '(): assert False',
                                'path': 'test_public.py'} for n in names})
        self.store.event('test_gate_observed', dict(project='p', cycle=cycle, suites=[suite]))

    def run_review(self):
        ledger = Ledger()
        asyncio.run(a.run(self.engine, self.task, self.plan, self.root, ledger))
        return ledger

    def test_consecutive_and_once_with_shared_meter(self):
        self.gate(1)
        self.gate(2)
        ledger = self.run_review()
        self.assertEqual((ledger.calls, self.store.charges), (1, 1))
        self.assertEqual(len(a.quarantines(self.store, 'p')), 1)
        self.assertIn('failure', self.responses[0])
        self.run_review()
        self.assertEqual(len(self.responses), 1)
        self.assertIn('do not chase', a.notes(self.store, 'p'))

    def test_qa_docstring_failure_reaches_tracker_and_eligibility(self):
        from optimal_harness.qa import DrivenQA
        qa = DrivenQA()
        qa.accept('test_order')
        qa.accept('def test_order(self):\n    """Check the specified ordering."""\n    self.assertTrue(False)')
        (self.root / 'test_public.py').write_text(qa.assembly())
        name = 'test_order (test_public.AcceptanceTests)'
        text = ('=' * 70 + '\nFAIL: ' + name + '\nCheck the specified ordering.\n' +
                '-' * 70 + '\nAssertionError: False is not true\n\n' + '-' * 70 +
                '\nRan 1 test in 0.001s\n\nFAILED (failures=1)\n')
        receipt = dict(stdout='', stderr=text, exit_code=1, reason='exited')
        (self.root / 'suite.json').write_text(json.dumps(receipt))
        for cycle in (1, 2):
            with patch.object(self.store, 'project', return_value={'cycle': cycle}):
                result = dict(passed=False, open_discrepancies=1,
                              results=[dict(passed=False, error='suite is not green')])
                a.account(self.engine, 'p', self.plan, self.root,
                          [dict(type='command_receipt', path='suite.json')], result)
            self.assertEqual(list(result['suite_reports'][0]['failed']), [name])
        self.run_review()
        self.assertEqual(len(self.responses), 1)
        self.assertEqual(self.responses[0]['test'], name)
        self.assertIn('AssertionError', self.responses[0]['failure'])

    def test_coherent_artifact_verdict_consumes_eligibility(self):
        self.gate(1)
        self.gate(2)
        self.reply = json.dumps(dict(verdict='ARTIFACT_WRONG', violator='artifact'))
        self.run_review()
        self.run_review()
        self.assertEqual(len(self.responses), 1)
        self.assertFalse(a.quarantines(self.store, 'p'))
        self.assertEqual(a.history(self.store, 'p', 'test_adjudicated')[-1]['verdict'], 'ARTIFACT_WRONG')

    def test_single_field_precedence_and_legacy_fallback(self):
        cases = [
            ({'violator': v}, d) for v, d in
            (('test', 'TEST_WRONG'), ('artifact', 'ARTIFACT_WRONG'), ('uncertain', 'UNCERTAIN'))]
        cases += [({'violator': v, 'verdict': 'TEST_WRONG' if v != 'test' else 'ARTIFACT_WRONG',
                    'violation': 'implementation'}, d) for v, d in
                  (('test', 'TEST_WRONG'), ('artifact', 'ARTIFACT_WRONG'), ('uncertain', 'UNCERTAIN'))]
        cases += [({'violation': v, 'verdict': 'UNCERTAIN'}, d) for v, d in
                  (('test', 'TEST_WRONG'), ('test_order', 'TEST_WRONG'),
                   ('artifact', 'ARTIFACT_WRONG'), ('implementation', 'ARTIFACT_WRONG'),
                   ('uncertain', 'UNCERTAIN'))]
        cases += [({'verdict': d}, d) for d in ('TEST_WRONG', 'ARTIFACT_WRONG', 'UNCERTAIN')]
        cases += [({'violator': [], 'violation': 'test_order'}, 'TEST_WRONG'),
                  ({'violator': 'invalid', 'violation': {}, 'verdict': 'ARTIFACT_WRONG'}, 'ARTIFACT_WRONG')]
        for fields, decision in cases:
            with self.subTest(fields=fields):
                self.store.events.clear()
                self.responses.clear()
                self.gate(1)
                self.gate(2)
                self.reply = json.dumps(dict(clause='ascending Unicode code-point order',
                                            derivation='Wrong order.', **fields))
                ledger = self.run_review()
                self.assertEqual(ledger.calls, 1)
                self.assertEqual(len(self.responses), 1)
                self.assertEqual(a.history(self.store, 'p', 'test_adjudicated')[-1]['verdict'], decision)
                self.assertEqual(bool(a.quarantines(self.store, 'p')), decision == 'TEST_WRONG')
                self.assertFalse(a.history(self.store, 'p', 'test_adjudication_incoherent'))

    def test_malformed_and_uncertain_preserve_later_eligibility(self):
        for reply in ('not JSON', '[]', 'null', '{}', '{"violator": []}',
                      '{"violation": {}}', '{"verdict": "invalid"}',
                      '{"violator": "uncertain"}', '{"violator": "test"}',
                      '{"violator":"test","clause":"invented","derivation":"wrong"}',
                      '{"violator":"test","clause":"Sort","derivation":"two\\nlines"}'):
            with self.subTest(reply=reply):
                self.store.events.clear()
                self.responses.clear()
                self.task['cycle'] = 3
                self.gate(1)
                self.gate(2)
                self.reply = reply
                self.run_review()
                self.run_review()
                self.assertEqual(len(self.responses), 1)
                self.assertEqual(a.history(self.store, 'p', 'test_adjudicated')[-1]['verdict'], 'UNCERTAIN')
                self.gate(3)
                self.task['cycle'] = 4
                self.reply = json.dumps(dict(violator='artifact'))
                self.run_review()
                self.assertEqual(len(self.responses), 2)
                self.gate(4)
                self.task['cycle'] = 5
                self.run_review()
                self.assertEqual(len(self.responses), 2)

    def test_uncertain_keeps_three_candidate_cycle_cap(self):
        names = tuple('test_%d (test_public.Tests.test_%d)' % (i, i) for i in range(5))
        self.gate(1, names)
        self.gate(2, names)
        self.reply = '{}'
        ledger = self.run_review()
        self.assertEqual((ledger.calls, self.store.charges), (3, 3))
        self.run_review()
        self.assertEqual(len(self.responses), 3)

    def test_prompt_requests_only_single_judgment(self):
        self.assertIn('violator (test, artifact, or uncertain', a.AdjudicationPrompt.system_prefix)
        self.assertNotIn('verdict', a.AdjudicationPrompt.system_prefix)

    def test_nonconsecutive_and_wrong_next_cycle(self):
        for cycles in ((0, 2), (0, 1)):
            self.store.events.clear()
            for cycle in cycles:
                self.gate(cycle)
            self.run_review()
        self.assertEqual(self.responses, [])

    def test_intervening_pass_resets_eligibility(self):
        self.gate(0)
        self.gate(1, ())
        self.gate(2)
        self.run_review()
        self.assertEqual(self.responses, [])

    def test_cap_and_deferred_candidates(self):
        names = tuple('test_%d (test_public.Tests.test_%d)' % (i, i) for i in range(5))
        self.gate(1, names)
        self.gate(2, names)
        self.run_review()
        self.run_review()
        self.assertEqual(len(self.responses), 3)
        self.gate(3, names)
        self.task['cycle'] = 4
        self.run_review()
        self.assertEqual(len(self.responses), 5)

    def test_other_and_malformed_verdicts(self):
        for reply in ('ARTIFACT_WRONG', 'UNCERTAIN', 'bad', '[]',
                      '{"verdict":"TEST_WRONG"}',
                      '{"verdict":"TEST_WRONG","clause":"invented","derivation":"wrong"}'):
            with self.subTest(reply=reply):
                self.store.events.clear()
                self.gate(1)
                self.gate(2)
                self.reply = json.dumps({'verdict': reply}) if reply in ('ARTIFACT_WRONG', 'UNCERTAIN') else reply
                self.run_review()
                self.assertFalse(a.quarantines(self.store, 'p'))

    def test_public_gate_accounting_preserves_raw_report_and_private_check(self):
        name = 'test_order (test_public.Tests.test_order)'
        text = ('======================================================================\nFAIL: ' + name +
                '\n----------------------------------------------------------------------\nAssertionError: diff\n'
                '----------------------------------------------------------------------\nRan 1 test in 0s\n\nFAILED (failures=1)\n')
        receipt = dict(stdout='', stderr=text, exit_code=1, reason='exited')
        (self.root / 'suite.json').write_text(json.dumps(receipt))
        checks = [{'type': 'sandbox_acceptance', 'test_file': 'private.py'},
                  {'type': 'command_receipt', 'path': 'suite.json'}]
        def report():
            return dict(passed=False, open_discrepancies=2, results=[
                dict(type=c['type'], passed=False, error='private' if i == 0 else 'suite failed')
                for i, c in enumerate(checks)])
        result = report()
        a.account(self.engine, 'p', self.plan, self.root, checks, result)
        self.assertEqual(list(result['suite_reports'][0]['failed']), [name])
        self.assertEqual(result['suite_reports'][0]['tests'][name]['path'], 'test_public.py')
        self.assertEqual(len(self.store.events[-1][1]['suites']), 1)
        self.store.event('test_quarantined', dict(project='p', suite='suite.json', test=name,
                         clause='ascending', derivation='wrong order'))
        result = report()
        a.account(self.engine, 'p', self.plan, self.root, checks, result)
        self.assertTrue(result['results'][1]['passed'])
        self.assertFalse(result['results'][0]['passed'])
        self.assertEqual(result['open_discrepancies'], 1)
        suite = result['suite_reports'][0]
        self.assertEqual(suite['failed'][name], 'AssertionError: diff')
        self.assertEqual(len(suite['quarantined']), 1)
        self.assertEqual(json.loads((self.root / 'suite.json').read_text()), receipt)
        self.assertIn('prospective', suite['quarantine_policy'])

    def test_hidden_grader_and_incomplete_output_never_excluded(self):
        (self.root / 'suite.json').write_text(json.dumps(dict(stdout='FAIL: test_order', stderr='',
                                                           reason='command_timeout', exit_code=None)))
        result = dict(passed=False, open_discrepancies=1, results=[dict(passed=False, error='timeout')])
        a.account(self.engine, 'p', self.plan, self.root,
                  [{'type': 'command_receipt', 'path': 'suite.json'}], result)
        self.assertFalse(result['results'][0]['passed'])
        self.gate(1, ())
        self.gate(2, ())
        self.run_review()
        self.assertFalse(self.responses)

    def test_partial_quarantine_counts_only_remaining_failures(self):
        names = ['test_order (test_public.Tests.test_order)', 'test_other (test_public.Tests.test_other)']
        text = ''
        for name in names:
            text += ('=' * 70 + '\nFAIL: ' + name + '\n' + '-' * 70 +
                     '\nAssertionError: wrong\n')
        text += '-' * 70 + '\nRan 2 tests in 0s\n\nFAILED (failures=2)\n'
        (self.root / 'suite.json').write_text(json.dumps(dict(stdout='', stderr=text,
                                                           exit_code=1, reason='exited')))
        self.store.event('test_quarantined', dict(project='p', suite='suite.json', test=names[0],
                         clause='ascending', derivation='wrong order'))
        result = dict(passed=False, open_discrepancies=1,
                      results=[dict(passed=False, error='suite failed')])
        a.account(self.engine, 'p', self.plan, self.root,
                  [{'type': 'command_receipt', 'path': 'suite.json'}], result)
        from optimal_harness.engine import Engine
        self.assertEqual(Engine._failure_count(result['results']), 1)
        self.assertNotIn(names[0], result['results'][0]['error'])
        self.assertEqual(len(result['suite_reports'][0]['failed']), 2)

    def test_cited_clause_is_provided(self):
        self.store.event('qa_test_citations', dict(project='p', citations={'test_order': 'ascending'}))
        self.gate(1)
        self.gate(2)
        self.run_review()
        self.assertEqual(self.responses[0]['cited_spec_clause'], 'ascending')

    def test_source_distinguishes_classes(self):
        (self.root / 'test_public.py').write_text(
            'class First:\n    def test_order(self): assert True\n'
            'class Second:\n    def test_order(self): assert False\n')
        body = a.source(self.root, {'reads': ['test_public.py']},
                        'test_order (test_public.Second.test_order)')
        self.assertIn('assert False', body['source'])
        short = a.source(self.root, {'reads': ['test_public.py']},
                         'test_order (test_public.Second)')
        self.assertEqual(body, short)

    def test_durable_history_is_scoped_to_project(self):
        from optimal_harness.store import Store as RealStore
        self.patch.stop()
        store = RealStore(self.root / 'board.sqlite3')
        store.event('test_quarantined', dict(project='other', suite='suite.json', test='test_order'))
        store.event('test_quarantined', dict(project='p', suite='suite.json', test='test_other'))
        self.assertEqual([q['test'] for q in a.quarantines(store, 'p')], ['test_other'])

    def test_verify40_live_payloads_quarantine(self):
        spec = ('Output shape/order: Write UTF-8 JSON containing an array of objects with exactly '
                '`word` and `count` keys, one object per distinct word, in the specified sort order.')
        (self.root / 'SPEC').write_text(spec)
        payloads = [
            '{\n  "verdict": "TEST_WRONG",\n  "violation": "test_single_word_case_sensitive_output",\n  "clause": "Output shape/order: Write UTF-8 JSON containing an array of objects with exactly `word` and `count` keys, one object per distinct word, in the specified sort order.",\n  "derivation": "The test incorrectly attempts to access output_data[0] with a tuple (\'count\', 1) instead of a string key, indicating the test expects a tuple as a key rather than a string key, which contradicts the SPEC\'s requirement for `word` and `count` keys to be strings."\n}',
            '{\n  "verdict": "TEST_WRONG",\n  "violation": "test_single_word_case_sensitive_output",\n  "clause": "Output shape/order: Write UTF-8 JSON containing an array of objects with exactly `word` and `count` keys, one object per distinct word, in the specified sort order.",\n  "derivation": "The test incorrectly attempts to access output_data[0] as a tuple instead of a dictionary, which contradicts the SPEC\'s requirement that output should be an array of objects with `word` and `count` keys."\n}',
            '{\n  "verdict": "TEST_WRONG",\n  "violation": "test_single_word_case_sensitive_output",\n  "clause": "Output shape/order: Write UTF-8 JSON containing an array of objects with exactly `word` and `count` keys, one object per distinct word, in the specified sort order.",\n "derivation": "The test incorrectly attempts to access output_data[0] with a tuple (\'count\', 1) instead of using the correct key access syntax output_data[0][\'count\']"\n}',
        ]
        for reply in payloads:
            with self.subTest(reply=reply):
                self.store.events.clear()
                self.responses.clear()
                name = 'test_single_word_case_sensitive_output (test_acceptance.AcceptanceTests)'
                self.gate(1, (name,))
                self.gate(2, (name,))
                self.reply = reply
                ledger = self.run_review()
                self.assertEqual(ledger.calls, 1)
                self.assertEqual(a.history(self.store, 'p', 'test_adjudicated')[-1]['verdict'], 'TEST_WRONG')
                self.assertEqual(len(a.quarantines(self.store, 'p')), 1)
