"""SPEC-derived probes and within-cycle regression feedback, without model calls."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from optimal_harness.engine import make_brief
from optimal_harness.ledger import Ledger
from optimal_harness.probes import run_probes
from tests.test_real_pipeline import CODE
from tests import test_fixer as fixer_tests

PLAN = json.loads((Path(__file__).resolve().parents[1] / 'examples/word-counter/plan.json').read_text())
BROKEN = CODE.replace("counts[w]}", "counts[w] + (1 if counts[w] > 1 else 0)}")


class ProbeRunnerTests(unittest.TestCase):
    def run_artifact(self, code):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'wordfreq.py').write_text(code)
            events = []
            class Store:
                def event(self, kind, payload):
                    events.append((kind, payload))
            failures = run_probes(root, PLAN, Ledger(), Store(),
                                  dict(id='task', project_id='project', cycle=1, stage='FIXER'))
            self.assertEqual(sorted(p.name for p in root.iterdir()), ['wordfreq.py'])
            self.assertEqual([kind for kind, _ in events], ['probe_failed'] * len(failures))
            return failures

    def test_spec_correct_all_shipped_probes(self):
        self.assertEqual(self.run_artifact(CODE), [])

    def test_verify41_repeated_word_regression(self):
        failures = self.run_artifact(BROKEN)
        self.assertEqual([f['name'] for f in failures], ['aggregation_case_sort', 'duplicates_heavy'])
        self.assertEqual(failures[0]['expected']['output'][0], {'word': 'A', 'count': 2})
        self.assertEqual(failures[0]['actual']['output'][0], {'word': 'A', 'count': 3})
        self.assertEqual(failures[1]['actual']['output'][0], {'word': 'x', 'count': 4})

    def test_generation_briefs_exclude_declarations(self):
        for role in ('QA', 'CODER'):
            brief = json.dumps(make_brief(role, PLAN))
            for probe in PLAN['probes']:
                self.assertNotIn(probe['name'], brief)
            self.assertNotIn('expected_output', brief)

    def test_no_probes_executes_nothing(self):
        with patch('optimal_harness.probes.sandbox_command') as command:
            self.assertEqual(run_probes(None, {}, None, None, None), [])
        command.assert_not_called()


class ProbeFeedbackTests(unittest.TestCase):
    setUp = fixer_tests.FixerTests.setUp
    submit = fixer_tests.FixerTests.submit
    setup_task = fixer_tests.FixerTests.setup_task
    attempt = fixer_tests.FixerTests.attempt
    feedback_events = fixer_tests.FixerTests.feedback_events

    def enable_probes(self):
        self.plan['probes'] = PLAN['probes']
        self.setup_task()

    def test_fixer_regression_gets_extra_pass_and_final_report(self):
        self.enable_probes()
        def repair(prompt):
            self.assertIn('aggregation_case_sort', prompt)
            self.assertIn('"count":3', prompt)
            return CODE
        task = self.attempt([BROKEN, '# unit', repair, '# unit'])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertEqual(len(self.calls), 4)
        reports = self.feedback_events()
        self.assertEqual([r['status'] for r in reports], ['failed', 'passed'])
        with self.store.transaction() as c:
            events = [json.loads(r[0]) for r in c.execute("SELECT payload FROM events WHERE kind='probe_failed'")]
        self.assertEqual([e['name'] for e in events], ['aggregation_case_sort', 'duplicates_heavy'])
        brief = self.engine._fixer_brief('wordcounter', self.plan, '', 'QA')
        self.assertNotIn('probe_failures', brief)

    def test_final_failure_reaches_next_fixer_brief(self):
        self.enable_probes()
        task = self.attempt([BROKEN, '# unit', BROKEN, '# unit'])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertEqual([r['status'] for r in self.feedback_events()], ['failed', 'failed'])
        brief = self.engine._fixer_brief('wordcounter', self.plan, '', 'QA')
        self.assertEqual(len(brief['probe_failures']), 2)
        self.assertIn('duplicates_heavy', brief['notes_verbatim'])


class CoderProbeTests(unittest.TestCase):
    setUp = fixer_tests.runtime_tests.RuntimeTests.setUp
    submit = fixer_tests.runtime_tests.RuntimeTests.submit
    run_coder = fixer_tests.runtime_tests.RuntimeTests.run_coder

    def test_coder_completion_runs_probes_without_generation_leak(self):
        self.plan['probes'] = PLAN['probes']
        def reply(body):
            def checked(prompt):
                for probe in PLAN['probes']:
                    self.assertNotIn(json.dumps(probe['name']), prompt)
                self.assertNotIn('expected_output', prompt)
                return body
            return checked
        import asyncio
        from tests.test_harness import FakeQueue
        self.submit()
        self.store.finish(self.store.claim('setup', 60), 'SUCCEEDED')
        self.store.enqueue('wordcounter', 'CODER', 0, make_brief('CODER', self.plan))
        replies = iter([reply('wordfreq.py :: implementation\ntest_unit.py :: tests'),
                        reply(BROKEN), reply('# tests'), reply('[]')])
        class Queue(FakeQueue):
            async def request(q, prompt, cap, timeout):
                return fixer_tests.Completion(next(replies)(prompt), 100, 80, 30)
        self.engine.queue = Queue([])
        with patch.object(self.engine, '_advance'):
            asyncio.run(self.engine.run_task(self.store.claim('worker', 60)))
        task = self.store.tasks('wordcounter')[-1]
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        with self.store.transaction() as c:
            events = [json.loads(r[0]) for r in c.execute("SELECT payload FROM events WHERE kind='probe_failed'")]
        self.assertEqual([e['name'] for e in events], ['aggregation_case_sort', 'duplicates_heavy'])
        brief = self.engine._fixer_brief('wordcounter', self.plan, '', 'QA')
        self.assertEqual(len(brief['probe_failures']), 2)
