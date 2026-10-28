"""Stage 26: deterministic gates and read-only source assembly."""
import json
import unittest

from tests import test_ratchet
from optimal_harness.test_source import BANNER, attach, extract, section, shed
from optimal_harness.prompt_budget import LIMIT, bounded
from optimal_harness.fixer import FixerPrompt


class TestSourceTests(unittest.TestCase):
    evaluate = test_ratchet.RatchetTest.evaluate
    def setUp(self):
        test_ratchet.RatchetTest.setUp(self)
        self.body = '    def test_case(self):\n        self.assertEqual(1, 2)'
        (self.root / 'test_acceptance.py').write_text(
            'import unittest\nclass AcceptanceTests(unittest.TestCase):\n' + self.body + '\n')

    def brief(self):
        return self.store.tasks('wordcounter')[-1]['brief']

    def test_repeat_only_and_missing_source(self):
        self.evaluate(1, 'FAIL: test_case test_missing')
        self.assertNotIn('failing_test_source', self.brief())
        self.evaluate(1, 'FAIL: test_case test_missing')
        brief = self.brief()
        self.assertIn(BANNER, brief['failing_test_source'])
        self.assertIn(self.body, brief['failing_test_source'])
        self.assertIn('test_missing — test_acceptance.py\n(source unavailable)', brief['failing_test_source'])
        self.assertEqual(brief['test_source_evidence'], dict(included=True, attached_tests=['test_case']))

    def test_restore_attaches_best_failures(self):
        self.evaluate(1, 'FAIL: test_case')
        self.evaluate(2, 'FAIL: test_other')
        self.assertIn(self.body, self.brief()['failing_test_source'])
        self.assertNotIn('test_other', self.brief()['failing_test_source'])

    def test_nonconsecutive_fingerprint_does_not_attach(self):
        self.evaluate(1, 'test_case')
        self.evaluate(1, 'test_other')
        self.evaluate(1, 'test_case')
        self.assertNotIn('failing_test_source', self.brief())

    def test_truncation_and_oversized_brief(self):
        names = ['test_big' + str(i) for i in range(40)]
        (self.root / 'test_acceptance.py').write_text('\n\n'.join(
            'def ' + name + '():\n    x = ' + repr('☃' * 2500) for name in names))
        tests = extract(self.plan, self.root, ['FAIL: ' + ' '.join(names)])
        self.assertIn('[truncated — full text in test_acceptance.py]', tests[0]['source'])
        brief = {'notes_verbatim': 'original context'}
        attach(brief, self.plan, self.root, ['FAIL: ' + ' '.join(names)])
        self.assertLessEqual(len(json.dumps(brief, ensure_ascii=False).encode()), LIMIT)
        self.assertTrue(any(t.get('pointer') for t in brief['failing_tests']))
        self.assertEqual(brief['notes_verbatim'], 'original context')
        events = []
        prompt = bounded('fixer', lambda f, e, a: shed(tests, lambda items:
            FixerPrompt(f['implementation'] + '\n' + section(items)))[0],
            {'implementation': 'x' * 50000}, record=events.append)
        self.assertLessEqual(len(prompt.encode()), LIMIT)
        self.assertIn('x' * 50000, prompt)
        self.assertEqual(events, [])
