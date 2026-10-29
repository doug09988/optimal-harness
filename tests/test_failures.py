import hashlib
from pathlib import Path
import re
import unittest

from optimal_harness.failures import fingerprint
from optimal_harness.store import canonical


class FingerprintTests(unittest.TestCase):
    root = Path('/example/work')

    def test_different_tests_with_identical_rollups(self):
        meta = ['reports/integration.json: suite is not green',
                'reports/qa.json: suite is not green']
        self.assertNotEqual(
            fingerprint(meta + ['FAIL: test_first (tests.Checks)'], self.root),
            fingerprint(meta + ['FAIL: test_second (tests.Checks)'], self.root))

    def test_same_names_ignore_prose_paths_timings_order_and_duplicates(self):
        first = ['suite is not green\nFAIL: test_alpha (tests.Checks)',
                 'ERROR: test_beta (tests.Other)\nRan 2 tests in 0.01s']
        second = ['different rollup /different/path 0x1234',
                  'test_beta (tests.Other) ... ERROR\nRan 9 tests in 2.34s',
                  'test_alpha (tests.Checks) ... FAIL\nAssertionError: changed',
                  'FAIL: test_alpha (tests.Checks)']
        expected = hashlib.sha256(canonical([
            'test_alpha (tests.Checks)', 'test_beta (tests.Other)'
        ]).encode()).hexdigest()[:16]
        self.assertEqual(fingerprint(first, self.root), expected)
        self.assertEqual(fingerprint(second, self.root), expected)

    def test_multiline_independent_acceptance_output(self):
        blob = '''independent acceptance failed: t 0:
test_ok (pkg.Checks) ... ok
test_alpha (pkg.Checks) ... FAIL
test_beta (pkg.Checks) ... ERROR
======================================================================
FAIL: test_alpha (pkg.Checks)
AssertionError: bad result
ERROR: test_beta (pkg.Checks)
RuntimeError: crashed
Ran 3 tests in 0.002s
FAILED (failures=1, errors=1)
'''
        self.assertEqual(fingerprint([blob], self.root), fingerprint([
            'FAIL: test_alpha (pkg.Checks)',
            'ERROR: test_beta (pkg.Checks)'], self.root))

    def test_class_is_part_of_test_identity(self):
        self.assertNotEqual(
            fingerprint(['FAIL: test_same (pkg.First)'], self.root),
            fingerprint(['FAIL: test_same (pkg.Second)'], self.root))

    def test_no_test_names_matches_legacy_fingerprint(self):
        failures = [
            'crash /example/work/.harness/commands/cmd-abc/output 0xAb12\n'
            'Ran 1 test in 0.123s',
            'suite is not green\nRan 12 tests in 1.234s',
        ]
        for items in (failures, list(reversed(failures)), []):
            with self.subTest(items=items):
                normalized = []
                for failure in items:
                    text = failure.replace(str(self.root), '/job')
                    text = re.sub(r'/job/\.harness/commands/cmd-[^/\s]+', '/job', text)
                    text = re.sub(r'Ran (\d+) tests? in [\d.]+s', r'Ran \1 tests in <time>s', text)
                    text = re.sub(r'0x[0-9a-fA-F]+', '<address>', text)
                    normalized.append(text)
                expected = hashlib.sha256(canonical(normalized).encode()).hexdigest()[:16]
                self.assertEqual(fingerprint(items, self.root), expected)
