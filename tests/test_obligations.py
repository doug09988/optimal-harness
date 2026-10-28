import copy
import json
import unittest
from unittest.mock import patch
from optimal_harness.obligations import extract, errors, resolutions
from optimal_harness.verifier import Verifier
import tests.test_runtime as runtime_tests


class ObligationTests(unittest.TestCase):
    def test_reference_extraction_heading_and_anchor(self):
        for anchor in ('# Appendix B — Fees', 'Appendix B: Fees', '<a id="appendix-b"></a>'):
            self.assertEqual(extract('See Appendix B.\n' + anchor), [])
        items = extract('See Appendix B, Section 2.1, Figure 3 and Table 4.')
        self.assertEqual(len(items), 4)
        self.assertEqual({x['kind'] for x in items}, {'missing_reference'})
        self.assertEqual(items, extract('See Appendix B, Section 2.1, Figure 3 and Table 4.'))

    def test_conservative_duties(self):
        self.assertEqual(extract('Record counts. The handoff is a document.\nReports are useful.'), [])
        items = extract('Record missing fees in the handoff. Flag omissions in open_discrepancies.')
        self.assertEqual(len(items), 2)
        self.assertTrue(all(item['kind'] == 'duty' for item in items))

    def test_duty_needs_explicit_resolution_or_discrepancy(self):
        items = extract('Record missing fees in the handoff.')
        item = items[0]
        self.assertEqual(errors(items, [('QA', {'summary': item['label']})]),
                         ['obligation_unaddressed:' + item['id']])
        doc = {'open_discrepancies': ['missing fees']}
        doc['obligation_resolutions'] = resolutions(
            [{'id': item['id'], 'discrepancy': 'missing fees'}], items, doc['open_discrepancies'], lambda p: '')
        self.assertEqual(errors(items, [('QA', doc)]), [])
        with self.assertRaisesRegex(ValueError, 'not verbatim'):
            resolutions([{'id': item['id'], 'evidence': {'source': 'spec.md', 'quote': 'invented', 'because': 'reason'}}],
                        items, [], lambda p: 'actual')

    def test_missing_reference_requires_coder_flag(self):
        items = extract('Use Appendix B.')
        self.assertTrue(errors(items, [('CODER', {'open_discrepancies': []})]))
        self.assertTrue(errors(items, [('QA', {'open_discrepancies': ['Appendix B missing']})]))
        self.assertEqual(errors(items, [('CODER', {'open_discrepancies': ['APPENDIX B missing']})]), [])

    def gate_fixture(self, flagged):
        fixture = runtime_tests.RuntimeTests(methodName='runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        spec = fixture.root/'spec.md'
        spec.write_text(spec.read_text() + '\nFees: use Appendix B.\n')
        task = fixture.run_coder([
            {'action': 'write', 'path': 'wordfreq.py', 'data': '# code'},
            {'action': 'write', 'path': 'test_unit.py', 'data': '# tests'},
            {'action': 'finish', 'summary': 'defined behavior',
             'open_discrepancies': ['Appendix B missing'] if flagged else []},
        ])
        self.assertEqual(task['status'], 'SUCCEEDED')
        with fixture.store.transaction() as c:
            c.execute("UPDATE tasks SET stage='CODER' WHERE id=?", (task['id'],))
        plan = fixture.store.project('wordcounter')['plan']
        persisted = json.loads((fixture.root/'vault/obligations.json').read_text())
        self.assertEqual(persisted, plan['obligations'])
        # Isolate obligation enforcement from the independent application grader.
        report = {'passed': True, 'results': [], 'open_discrepancies': 0}
        with patch.object(Verifier, 'verify', return_value=copy.deepcopy(report)), patch.object(fixture.engine, '_record_verifier_receipts', return_value=[]):
            fixture.engine._gate('wordcounter', plan, fixture.root)
        return fixture, plan

    def test_gate_missing_appendix_fails_without_flag(self):
        fixture, plan = self.gate_fixture(False)
        tasks = fixture.store.tasks('wordcounter')
        self.assertEqual(tasks[-1]['stage'], 'FIXER')
        with fixture.store.transaction() as c:
            brief = json.loads(c.execute('SELECT brief FROM tasks WHERE id=?', (tasks[-1]['id'],)).fetchone()[0])
        self.assertIn('obligation_unaddressed:', brief['notes_verbatim'])

    def test_gate_missing_appendix_passes_with_flag_and_unrelated_gap_fails(self):
        fixture, plan = self.gate_fixture(True)
        self.assertEqual(fixture.store.project('wordcounter')['status'], 'AWAITING_COORDINATOR')
        checks = fixture.engine._discrepancy_checks('wordcounter')
        self.assertTrue(Verifier(fixture.root).verify(checks)['passed'])
        path = fixture.root/'other.json'
        path.write_text(json.dumps({'open_discrepancies': ['unrelated implementation bug']}))
        check = dict(checks[0], path='other.json')
        self.assertFalse(Verifier(fixture.root).verify([check])['passed'])
