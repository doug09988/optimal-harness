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
        p = self.evaluate(38, 'worse_again')
        self.assertEqual((p['status'], p['repeats']), ('ESCALATED', 3))
        self.assertEqual(p['best_artifact'], best)
        self.assertEqual((self.root / 'wordfreq.py').read_text(), 'original')

    def test_declared_growth_moves_baseline_without_increment_then_clock_resumes(self):
        landed = self.root / 'test_unit.py'
        landed.unlink(missing_ok=True)
        first = self.evaluate(6, 'original')
        self.assertNotIn('test_unit.py', first['best_artifact']['present_deliverables'])
        landed.write_text('new suite')
        grown = self.evaluate(12, 'landed')
        self.assertEqual(grown['cycle'], first['cycle'] + 1)
        self.assertEqual(grown['repeats'], 0)
        self.assertEqual(grown['best_artifact']['failure_count'], 12)
        self.assertIn('test_unit.py', grown['best_artifact']['present_deliverables'])
        self.assertEqual((self.root / 'wordfreq.py').read_text(), 'landed')
        for repeats in (1, 2):
            flat = self.evaluate(12, 'landed')
            self.assertEqual(flat['repeats'], repeats)
            self.assertNotEqual(flat['status'], 'ESCALATED')
        self.assertEqual(self.evaluate(12, 'landed')['status'], 'ESCALATED')

    def assert_streak(self, count, repeats, status='ACTIVE'):
        p = self.evaluate(count, 'stable')
        self.assertEqual(p['repeats'], repeats)
        if status == 'ESCALATED':
            self.assertEqual(p['status'], status)
        else:
            self.assertNotEqual(p['status'], 'ESCALATED')
        with self.store.transaction() as c:
            event = json.loads(c.execute(
                "SELECT payload FROM events WHERE kind='failed_cycle' ORDER BY seq DESC LIMIT 1"
            ).fetchone()[0])
        self.assertEqual(event['repeats'], repeats)

    def test_progress_resets_same_defect_then_three_flat_cycles_escalate(self):
        with patch('optimal_harness.failures.fingerprint', return_value='same-defect'):
            self.assert_streak(8, 1)
            self.assert_streak(7, 0)
            self.assert_streak(7, 1)
            self.assert_streak(7, 2)
            self.assert_streak(7, 3, 'ESCALATED')

    def test_growth_is_neutral_then_progress_resets_against_previous_count(self):
        landed = self.root / 'test_unit.py'
        landed.unlink(missing_ok=True)
        with patch('optimal_harness.failures.fingerprint', return_value='same-defect'):
            self.assert_streak(5, 1)
            landed.write_text('new suite')
            self.assert_streak(8, 1)
            self.assert_streak(7, 0)
            self.assert_streak(7, 1)
            self.assert_streak(7, 2)
            self.assert_streak(7, 3, 'ESCALATED')

    def test_flat_from_start_escalates_on_third_cycle(self):
        with patch('optimal_harness.failures.fingerprint', return_value='same-defect'):
            self.assert_streak(1, 1)
            self.assert_streak(1, 2)
            self.assert_streak(1, 3, 'ESCALATED')

    def test_descent_above_best_resets_streak(self):
        with patch('optimal_harness.failures.fingerprint', return_value='same-defect'):
            self.assert_streak(5, 1)
            self.assert_streak(8, 2)
            self.assert_streak(7, 0)
            self.assert_streak(6, 0)

    def test_restore_older_best_preserves_deliverable_landed_after_best(self):
        landed = self.root / 'test_unit.py'
        landed.write_text('')
        # An empty deliverable can have an old checkpoint, but is absent
        # from the gate's present set and must not be restored later.
        import hashlib
        sha = hashlib.sha256(b'').hexdigest()
        archive = 'vault/checkpoints/' + sha + '.blob'
        (self.root / archive).parent.mkdir(parents=True, exist_ok=True)
        (self.root / archive).write_bytes(b'')
        with self.store.transaction() as c:
            self.store.checkpoint(c, dict(id='empty', generation=1, project_id='wordcounter'),
                                  'test_unit.py', sha, archive)
        best = self.evaluate(6, 'original')['best_artifact']
        self.assertTrue(any(cp['path'] == 'test_unit.py' for cp in best['snapshot']))
        self.assertNotIn('test_unit.py', best['present_deliverables'])
        landed.write_text('new suite')
        sha = hashlib.sha256(landed.read_bytes()).hexdigest()
        archive = 'vault/checkpoints/' + sha + '.blob'
        (self.root / archive).write_bytes(landed.read_bytes())
        with self.store.transaction() as c:
            self.store.checkpoint(c, dict(id='new', generation=1, project_id='wordcounter'),
                                  'test_unit.py', sha, archive)
        self.evaluate(12, 'landed')
        self.engine._restore_artifact('wordcounter', self.root, best)
        self.assertEqual(landed.read_text(), 'new suite')
        self.assertEqual((self.root / 'wordfreq.py').read_text(), 'original')
        self.assertEqual(self.engine.integrity_errors('wordcounter', self.root), [])

    def test_undeclared_file_does_not_exempt_regression(self):
        best = self.evaluate(6, 'original')['best_artifact']
        (self.root / 'stray.py').write_text('stray')
        regressed = self.evaluate(12, 'regressed')
        self.assertEqual(regressed['best_artifact'], best)
        self.assertEqual(regressed['repeats'], 2)
        self.assertEqual((self.root / 'wordfreq.py').read_text(), 'original')

    def test_empty_declared_deliverable_does_not_exempt_regression(self):
        landed = self.root / 'test_unit.py'
        landed.unlink(missing_ok=True)
        best = self.evaluate(6, 'original')['best_artifact']
        landed.write_text('')
        self.assertEqual(self.evaluate(12, 'regressed')['best_artifact'], best)

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
