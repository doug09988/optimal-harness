"""Stage 22 assembly checks; scripted clients only."""
import unittest
from optimal_harness.prompt_budget import LIMIT, bounded, pointer
from optimal_harness.fixer import FixerPrompt
from optimal_harness.coder import DrivenCoder
from optimal_harness.qa import DrivenQA
from optimal_harness.store import canonical


class PromptBudgetTests(unittest.TestCase):
    def test_fixer_context_sheds_files_preserves_target(self):
        target = 'target line\n' * 1000
        sources = {'target.py': target, **{f'other{i}.py': str(i) * 40000 for i in range(3)}}
        events = []
        prompt = bounded('fixer', lambda f, e, a: FixerPrompt(canonical(f)),
                         sources, target=('target.py', target), record=events.append)
        self.assertLessEqual(len(prompt.encode()), LIMIT)
        self.assertIn(canonical(target), prompt)
        for i in range(3):
            path = f'other{i}.py'
            self.assertIn(pointer(path, sources[path]), prompt)
        self.assertEqual([e['category'] for e in events], ['non_target_files'] * 3)

    def test_evidence_truncation_then_prior_pointers(self):
        evidence = {'report': '☃' * 30000}
        prior = {'accepted': 'p' * 60000}
        events = []
        prompt = bounded('qa', lambda f, e, a: canonical([e, a]), {}, evidence, prior,
                         record=events.append)
        self.assertLessEqual(len(prompt.encode()), LIMIT)
        self.assertIn('☃' * 4000, prompt)
        self.assertNotIn('☃' * 4001, prompt)
        self.assertIn('[truncated — full text in report]', prompt)
        self.assertIn(pointer('accepted', prior['accepted']), prompt)
        self.assertEqual([e['category'] for e in events], ['evidence', 'prior'])

    def test_coder_target_byte_limit_and_common_prompt(self):
        coder = DrivenCoder()
        coder.plan = {'main.py': 'implement'}
        sources = {'main.py': 'old code', 'SPEC.md': 'spec'}
        self.assertEqual(coder.prompt(sources, ['main.py'], ['main.py']),
                         coder._prompt(sources, ['main.py'], ['main.py'], {}, None))
        with self.assertRaisesRegex(RuntimeError, 'target_too_large_for_single_call'):
            coder.prompt({'main.py': '☃' * 22000})
        events = []
        prompt = coder.prompt({**sources, 'large.py': 'x' * 80000}, record=events.append)
        self.assertLessEqual(len(prompt.encode()), LIMIT)
        self.assertIn('old code', prompt)
        self.assertTrue(events)

    def test_qa_common_report_and_large_report(self):
        qa = DrivenQA()
        qa.plan = []
        qa.execution = 'command passed'
        sources = {'SPEC.md': 'requirements'}
        prior = {'acceptance tests': qa.assembly(), 'unwritable tests': canonical({})}
        self.assertEqual(qa.prompt(sources), qa._prompt(sources, {'execution': qa.execution}, prior))
        qa.execution = 'e' * 80000
        prompt = qa.prompt(sources)
        self.assertLessEqual(len(prompt.encode()), LIMIT)
        self.assertIn('[truncated — full text in execution]', prompt)

    def test_unshrinkable_context_refuses_dispatch(self):
        with self.assertRaisesRegex(RuntimeError, 'prompt_too_large_for_single_call'):
            bounded('fixer', lambda f, e, a: FixerPrompt('x' * 64001), {})
