"""Stage 47: archived deliverables and FIXER exhaustion coverage, offline."""
import json
import unittest
from unittest.mock import patch

from optimal_harness.fixer import DrivenFixer
from tests import test_fixer as fixtures
from tests.test_probes import BROKEN, PLAN
from tests.test_real_pipeline import CODE


class CarryoverTests(unittest.TestCase):
    setUp = fixtures.FixerTests.setUp
    submit = fixtures.FixerTests.submit
    setup_task = fixtures.FixerTests.setup_task
    attempt = fixtures.FixerTests.attempt
    def checkpoint_prior(self, paths):
        task = self.store.claim('prior', 60)
        for path in paths:
            self.engine._checkpoint(self.root, task, path, {}, 'test')
        self.store.fail_attempt(task, 'prior generation', '', True)

    def test_rejected_probe_rewrite_carries_prior_checkpoint_to_gate(self):
        self.plan['probes'] = PLAN['probes']
        self.setup_task()
        (self.root/'wordfreq.py').write_text(CODE)
        plan = self.store.project('wordcounter')['plan']
        plan['stages']['FIXER']['deliverables'] = ['wordfreq.py', 'test_unit.py']
        with self.store.transaction() as c:
            c.execute('UPDATE projects SET plan=? WHERE id=?', (json.dumps(plan), 'wordcounter'))
        self.checkpoint_prior(['wordfreq.py'])
        task = self.attempt([BROKEN] * 3 + ['# repaired tests'])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertEqual((self.root/'wordfreq.py').read_text(), CODE)
        doc = json.loads((self.root/f"vault/runs/{task['id']}/a2/handoff.json").read_text())
        self.assertTrue(doc['failed_deliverables'])
        with self.store.transaction() as c:
            events = [json.loads(r[0]) for r in c.execute(
                "SELECT payload FROM events WHERE kind='deliverable_carried_over'")]
        self.assertEqual([e['path'] for e in events], ['wordfreq.py'])
        from optimal_harness.verifier import Verifier
        with patch.object(Verifier, 'verify', return_value={
                'results': [{'passed': False, 'error': 'unfixed repair'}]}):
            self.engine._gate('wordcounter', plan, self.root)
        with self.store.transaction() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM events WHERE kind='satisfiability_gate'").fetchone()[0], 1)

    def test_handoff_exhaustion_degrades_verbatim(self):
        self.setup_task()
        for _ in range(2):
            self.attempt([''] * 6)
        reason = 'fixer_handoff_exhausted:error:missing_deliverables:example.py'
        with patch.object(DrivenFixer, 'run', side_effect=RuntimeError(reason)):
            task = self.attempt([])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        doc = json.loads((self.root/f"vault/runs/{task['id']}/a3/handoff.json").read_text())
        self.assertEqual(doc['summary'], 'FIXER completed degraded: ' + reason)

    def test_runtime_budget_marker_propagates(self):
        self.setup_task()
        for _ in range(2):
            self.attempt([''] * 6)
        reason = 'circuit_breaker:wall_clock_after_call'
        with patch.object(DrivenFixer, 'run', side_effect=RuntimeError(reason)):
            task = self.attempt([])
        self.assertEqual(task['status'], 'FAILED')
        self.assertEqual(task['failure'], reason)

    def test_carryover_restores_archive_for_any_stage_and_never_invents_files(self):
        self.setup_task()
        self.checkpoint_prior(['wordfreq.py'])
        task = self.store.claim('worker', 60)
        (self.root/'wordfreq.py').write_text('unaccepted bytes')
        checkpoints = {}
        self.engine._carry_over(self.root, task, {'deliverables': ['wordfreq.py']}, checkpoints)
        self.assertEqual((self.root/'wordfreq.py').read_text(), 'old implementation\n')
        with self.assertRaisesRegex(RuntimeError, 'missing_or_uncheckpointed_deliverables:never.py'):
            self.engine._check_stage(self.root, task, {'deliverables': ['never.py']}, {}, 'unused')

    def test_finish_validation_exhaustion_degrades(self):
        self.setup_task()
        for _ in range(2):
            self.attempt([''] * 6)
        finish = {'action': 'finish', 'summary': 'attempt', 'open_discrepancies': []}
        from optimal_harness.engine import Engine
        original = Engine._check_stage
        denials = []
        def check(engine, root, task, conf, checkpoints, handoff):
            if len(denials) < 3:
                denials.append(True)
                raise RuntimeError('error:missing_deliverables:missing_or_uncheckpointed_deliverables:never.py')
            return original(engine, root, task, conf, checkpoints, handoff)
        with patch.object(DrivenFixer, 'run', return_value=finish), patch.object(Engine, '_check_stage', check):
            task = self.attempt([])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        doc = json.loads((self.root/f"vault/runs/{task['id']}/a3/handoff.json").read_text())
        self.assertIn('fixer_handoff_exhausted:error:missing_deliverables:', doc['summary'])
