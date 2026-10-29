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

    def test_candidate_runs_with_current_other_implementation_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'wordfreq.py').write_text('old implementation')
            (root/'helper.py').write_text('OFFSET = 0\n')
            plan = dict(PLAN, implementation_paths=['wordfreq.py', 'helper.py'])
            candidate = ('from helper import OFFSET\n' + CODE).encode()
            events = []
            class Store:
                def event(self, kind, payload):
                    events.append((kind, payload))
            self.assertEqual(run_probes(root, plan, Ledger(), Store(),
                             dict(id='task', project_id='project', cycle=1, stage='CODER'),
                             candidates={'wordfreq.py': candidate}), [])
            self.assertEqual(events, [])
            self.assertEqual((root/'wordfreq.py').read_text(), 'old implementation')
            self.assertEqual(sorted(p.name for p in root.iterdir()), ['helper.py', 'wordfreq.py'])

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

    def test_fixer_regression_rejected_then_correct_write_and_final_report(self):
        self.enable_probes()
        def repair(prompt):
            self.assertIn('aggregation_case_sort', prompt)
            self.assertIn('"count":3', prompt)
            return CODE
        task = self.attempt([BROKEN, repair, '# unit'])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertEqual(len(self.calls), 3)
        reports = self.feedback_events()
        self.assertEqual([r['status'] for r in reports], ['missing'])
        with self.store.transaction() as c:
            events = [json.loads(r[0]) for r in c.execute("SELECT payload FROM events WHERE kind='probe_failed'")]
        self.assertEqual([e['name'] for e in events], ['aggregation_case_sort', 'duplicates_heavy'])
        brief = self.engine._fixer_brief('wordcounter', self.plan, '', 'QA')
        self.assertNotIn('probe_failures', brief)

    def test_probe_rejection_exhausts_existing_retries_without_publication(self):
        self.enable_probes()
        original = (self.root/'wordfreq.py').read_bytes()
        task = self.attempt([BROKEN] * 2 + [''] * 3)
        self.assertEqual(task['status'], 'QUEUED', task['failure'])
        self.assertIn('fixer_action_format_exhausted', task['failure'])
        self.assertEqual(len(self.calls), 5)
        self.assertEqual((self.root/'wordfreq.py').read_bytes(), original)
        with self.store.transaction() as c:
            rejected = [json.loads(r[0]) for r in c.execute(
                "SELECT payload FROM events WHERE kind='write_rejected'")]
            calls = c.execute("SELECT count(*) FROM events WHERE kind='model_call_finished'").fetchone()[0]
        self.assertEqual(calls, 5)
        self.assertEqual(len(rejected), 2)
        self.assertTrue(all(e['reason'] == 'probe_failed:aggregation_case_sort' for e in rejected))
        self.assertFalse(any(cp['path'] == 'wordfreq.py'
                             for cp in self.store.checkpoints('wordcounter')))

    def test_post_write_regression_still_reaches_feedback_and_next_brief(self):
        self.enable_probes()
        def regress_after_implementation_acceptance(prompt):
            self.assertIn('FILE: test_unit.py', prompt)
            (self.root/'wordfreq.py').write_text(BROKEN)
            return '# unit'
        task = self.attempt([CODE, regress_after_implementation_acceptance,
                             CODE, regress_after_implementation_acceptance])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertEqual([r['status'] for r in self.feedback_events()], ['failed', 'failed'])
        brief = self.engine._fixer_brief('wordcounter', self.plan, '', 'QA')
        self.assertEqual(len(brief['probe_failures']), 2)
        self.assertIn('duplicates_heavy', brief['notes_verbatim'])

    def test_chunked_fixer_checks_assembled_candidate(self):
        self.enable_probes()
        original = '# old\n' * 151
        (self.root/'wordfreq.py').write_text(original)
        prefix = '# prefix\n' * 60
        def repair(prompt):
            self.assertIn('probe_failed:aggregation_case_sort', prompt)
            self.assertIn('expected:', prompt)
            self.assertIn('actual:', prompt)
            self.assertEqual((self.root/'wordfreq.py').read_text(), original)
            return CODE + '<<<FIXER_DONE>>>'
        task = self.attempt([prefix + '<<<FIXER_MORE>>>', BROKEN + '<<<FIXER_DONE>>>',
                             repair, '# unit'])
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertEqual((self.root/'wordfreq.py').read_text(), CODE)
        with self.store.transaction() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM events WHERE kind='write_rejected'").fetchone()[0], 1)


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
        def repair(prompt):
            self.assertIn('probe_failed:aggregation_case_sort', prompt)
            self.assertIn('"count":3', prompt)
            self.assertIn('expected:', prompt)
            self.assertIn('actual:', prompt)
            self.assertFalse((self.root/'wordfreq.py').exists())
            return CODE
        import asyncio
        from tests.test_harness import FakeQueue
        self.submit()
        self.store.finish(self.store.claim('setup', 60), 'SUCCEEDED')
        self.store.enqueue('wordcounter', 'CODER', 0, make_brief('CODER', self.plan))
        replies = iter([reply('wordfreq.py :: implementation\ntest_unit.py :: tests'),
                        reply(BROKEN), repair, reply('# tests'), reply('[]')])
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


class ProbeWriteScopeTests(unittest.TestCase):
    setUp = fixer_tests.runtime_tests.RuntimeTests.setUp
    submit = fixer_tests.runtime_tests.RuntimeTests.submit
    run_coder = fixer_tests.runtime_tests.RuntimeTests.run_coder
    setup_task = fixer_tests.FixerTests.setup_task
    attempt = fixer_tests.FixerTests.attempt

    def test_both_overcount_variants_rejected_for_both_writers(self):
        all_overcount = CODE.replace('counts[w]}', 'counts[w] + 1}')
        for role in ('CODER', 'FIXER'):
            for body in (BROKEN, all_overcount):
                with self.subTest(role=role, all_words=body == all_overcount):
                    self.setUp()
                    self.plan['probes'] = PLAN['probes']
                    if role == 'FIXER':
                        self.setup_task()
                        original = (self.root/'wordfreq.py').read_bytes()
                    else:
                        original = None
                    def repair(prompt):
                        self.assertIn('probe_failed:aggregation_case_sort', prompt)
                        self.assertIn('expected:', prompt)
                        self.assertIn('actual:', prompt)
                        path = self.root/'wordfreq.py'
                        self.assertEqual(path.read_bytes() if path.exists() else None, original)
                        # Only the discriminating failure diff enters the retry context.
                        self.assertNotIn('duplicates_heavy', prompt)
                        self.assertNotIn('expected_output', prompt)
                        return CODE
                    if role == 'FIXER':
                        task = self.attempt([body, repair, '# unit'])
                        self.assertEqual(len(self.calls), 3)
                    else:
                        task = self.run_coder(['wordfreq.py :: implementation\ntest_unit.py :: tests',
                                               body, repair, '# tests', '[]'], role='CODER')
                        self.assertEqual(self.engine.queue.calls, 5)
                    self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
                    self.assertEqual((self.root/'wordfreq.py').read_text(), CODE)
                    with self.store.transaction() as c:
                        rejected = [json.loads(r[0]) for r in c.execute(
                            "SELECT payload FROM events WHERE kind='write_rejected'")]
                    self.assertEqual(len(rejected), 1)
                    self.assertEqual(rejected[0]['reason'], 'probe_failed:aggregation_case_sort')
                    self.assertEqual(rejected[0]['generation'], 1)
                    self.assertEqual(rejected[0]['path'], 'wordfreq.py')

    def test_nonimplementation_and_test_writes_do_not_validate(self):
        self.plan['probes'] = PLAN['probes']
        self.plan['stages']['CODER']['writes'].append('notes.txt')
        from optimal_harness.probes import run_probes as runner
        validations = []
        def record(*args, **kwargs):
            if 'candidates' in kwargs:
                validations.append(kwargs['candidates'])
            return runner(*args, **kwargs)
        with patch('optimal_harness.probes.run_probes', side_effect=record):
            task = self.run_coder(['wordfreq.py :: impl\ntest_unit.py :: tests\nnotes.txt :: notes',
                                   CODE, '# tests', 'notes', '[]'], role='CODER')
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertEqual(validations, [{'wordfreq.py': CODE.encode()}])

    def test_probe_free_project_never_validates_candidates_or_executes_probes(self):
        with patch('optimal_harness.probes.run_probes', wraps=run_probes) as runner, \
                patch('optimal_harness.probes.sandbox_command') as command:
            task = self.run_coder(['wordfreq.py :: impl\ntest_unit.py :: tests', '# impl', '# tests', '[]'], role='CODER')
        self.assertEqual(task['status'], 'SUCCEEDED', task['failure'])
        self.assertTrue(all(not call.kwargs for call in runner.call_args_list))
        command.assert_not_called()
