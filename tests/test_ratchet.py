"""Scripted deterministic gate evaluations; no model or sandbox calls."""
import json
import unittest
from unittest.mock import patch

from tests import test_harness as harness
from optimal_harness.engine import Engine
from optimal_harness.verifier import Verifier


class RatchetTest(unittest.TestCase):
    def setUp(self):
        self.fixture = harness.HarnessTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.store = self.fixture.root, self.fixture.store
        self.engine = Engine(self.store, harness.FakeQueue([]))
        self.engine.submit(self.root, self.fixture.plan, self.fixture.grader_dir / 'acceptance.json')
        self.plan = self.store.project('wordcounter')['plan']

    def evaluate(self, count, label):
        artifact = self.root / 'wordfreq.py'
        artifact.write_text(label)
        # Script the same checkpoints that a successful worker produces.
        sha = __import__('hashlib').sha256(artifact.read_bytes()).hexdigest()
        archive = 'vault/checkpoints/' + sha + '.blob'
        (self.root / archive).parent.mkdir(parents=True, exist_ok=True)
        (self.root / archive).write_bytes(artifact.read_bytes())
        with self.store.transaction() as c:
            self.store.checkpoint(c, dict(id=label, generation=1, project_id='wordcounter'),
                                  'wordfreq.py', sha, archive)
        report = dict(passed=count == 0, open_discrepancies=count,
                      results=[dict(passed=False, error=f'{label}: failure {i}') for i in range(count)])
        with patch.object(Verifier, 'verify', return_value=report), \
             patch.object(self.engine, '_apply_obligations'), \
             patch.object(self.engine, '_discrepancy_checks', return_value=[]):
            self.engine._gate('wordcounter', self.plan, self.root)
        self.assertEqual(self.engine.integrity_errors('wordcounter', self.root), [])
        return self.store.project('wordcounter')

    def test_regression_restores_and_brief_has_counts_and_best_failures(self):
        self.evaluate(9, 'original')
        p = self.evaluate(38, 'regressed')
        self.assertEqual((self.root / 'wordfreq.py').read_text(), 'original')
        notes = self.store.tasks('wordcounter')[-1]['brief']['notes_verbatim']
        for text in ('REGRESSION', 'best failure count: 9', 'regressed failure count: 38', 'original: failure 0'):
            self.assertIn(text, notes)
        self.assertNotIn('regressed: failure', notes)
        self.assertEqual(p['best_artifact']['failure_count'], 9)
        with self.store.transaction() as c:
            events = [json.loads(r['payload']) for r in c.execute("SELECT payload FROM events WHERE kind='artifact_evaluated'")]
        self.assertEqual(events[-1]['status'], 'REGRESSION')

    def test_equal_different_fingerprint_keeps_best_without_restore(self):
        first = self.evaluate(9, 'original')['best_artifact']
        p = self.evaluate(9, 'different')
        self.assertEqual((self.root / 'wordfreq.py').read_text(), 'different')
        self.assertEqual(p['best_artifact'], first)
        self.assertEqual(p['repeats'], 1)

    def test_improvement_updates_best(self):
        self.evaluate(9, 'original')
        p = self.evaluate(5, 'better')
        self.assertEqual(p['best_artifact']['failure_count'], 5)
        self.assertEqual((self.root / 'wordfreq.py').read_text(), 'better')

    def test_regressions_and_repeated_best_escalate_with_best_preserved(self):
        best = self.evaluate(9, 'original')['best_artifact']
        self.evaluate(38, 'regressed')
        p = self.evaluate(24, 'worse_again')
        self.assertEqual((p['status'], p['repeats']), ('ESCALATED', 3))
        self.assertEqual(p['best_artifact'], best)
        self.assertEqual((self.root / 'wordfreq.py').read_text(), 'original')

    def test_bundled_acceptance_failures_count_individual_failures_and_errors(self):
        self.assertEqual(Engine._failure_count([
            dict(passed=False, error='independent acceptance failed: FAILED (failures=9, errors=2)'),
            dict(passed=False, error='other gate failure'), dict(passed=True, error=None)]), 12)

    def test_exact_duplicates_only_collapse_before_gate_evaluation(self):
        errors = ['same', 'same', 'same!', 'same ',
                  'acceptance FAILED (failures=2)', 'acceptance FAILED (failures=2)']
        report = dict(passed=False, open_discrepancies=6,
                      results=[dict(passed=False, error=e) for e in errors])
        with patch.object(Verifier, 'verify', return_value=report), \
             patch.object(self.engine, '_apply_obligations'), \
             patch.object(self.engine, '_discrepancy_checks', return_value=[]):
            self.engine._gate('wordcounter', self.plan, self.root)
        best = self.store.project('wordcounter')['best_artifact']
        self.assertEqual(best['failures'], list(dict.fromkeys(errors)))
        self.assertEqual(best['failure_count'], 5)
        self.assertEqual(report['failure_count'], 5)
        from optimal_harness.failures import fingerprint
        self.assertEqual(best['fingerprint'], fingerprint(list(dict.fromkeys(errors)), self.root))
        with self.store.transaction() as c:
            payload = json.loads(c.execute(
                "SELECT payload FROM events WHERE kind='satisfiability_gate' LIMIT 1"
            ).fetchone()[0])
        self.assertEqual(payload['report']['failures'], best['failures'])
        self.assertEqual(payload['report']['failure_count'], 5)
