from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from optimal_harness.failures import _FAILING_TEST, fingerprint
from optimal_harness.verifier import Verifier


class AcceptanceFailureTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / 'acceptance.py').write_text('')
        self.verifier = Verifier(self.root, self.root)

    def failure(self, stderr='', stdout=''):
        receipt = {'exit_code': 1, 'stderr': stderr, 'stdout': stdout}
        with patch('optimal_harness.verifier.sandbox_command', return_value=receipt):
            result = self.verifier.verify([{
                'type': 'sandbox_acceptance', 'test_file': 'acceptance.py',
                'argv': ['python3', 'grader_hidden_test.py'], 'reads': [],
            }])
        self.assertFalse(result['passed'])
        self.assertEqual(result['open_discrepancies'], 1)
        self.assertEqual(self.verifier.receipts[-1], receipt)
        return result['results'][0]['error']

    @staticmethod
    def output(names=('alpha', 'beta', 'gamma'), prose='traceback'):
        return ''.join(
            f'FAIL: test_{name} (pkg.Checks)\n' + (prose + '\n') * 200
            for name in names
        )

    def test_all_headers_survive_long_tracebacks(self):
        output = self.output()
        entry = self.failure(stderr=output)
        expected = [f'FAIL: test_{name} (pkg.Checks)' for name in ('alpha', 'beta', 'gamma')]
        for header in expected:
            self.assertNotIn(header, output[-600:])
            self.assertIn(header, entry)
        names = {match.group('header') or match.group('result')
                 for match in _FAILING_TEST.finditer(entry)}
        self.assertEqual(len(names), 3)
        self.assertEqual(fingerprint([entry], self.root), fingerprint(expected, self.root))
        self.assertTrue(entry.endswith(output[-600:]))
        self.assertLessEqual(len(entry), 600 + len('\n'.join(expected)) + 32)

    def test_identity_changes_fingerprint_but_traceback_does_not(self):
        first = fingerprint([self.failure(stderr=self.output())], self.root)
        changed_prose = self.failure(stderr=self.output(prose='different traceback'))
        changed_test = self.failure(stderr=self.output(names=('alpha', 'beta', 'delta')))
        self.assertEqual(first, fingerprint([changed_prose], self.root))
        self.assertNotEqual(first, fingerprint([changed_test], self.root))

    def test_both_streams_and_result_line_forms(self):
        stderr = 'ERROR: test_alpha (pkg.Checks)\n' + 'x' * 1000
        stdout = ('test_beta (pkg.Checks) ... FAIL\n'
                  'test_gamma (pkg.Checks) ... ERROR\n' + 'y' * 1000)
        entry = self.failure(stderr=stderr, stdout=stdout)
        expected = [f'FAIL: test_{name} (pkg.Checks)' for name in ('alpha', 'beta', 'gamma')]
        self.assertEqual(fingerprint([entry], self.root), fingerprint(expected, self.root))
        self.assertTrue(entry.endswith(stderr[-600:]))

    def test_headerless_output_preserves_exact_tail_behavior(self):
        for stderr, stdout in [('', ''), ('crash\n' * 300, 'ignored'),
                               ('', 'import error\n' * 200), ('short error', '')]:
            with self.subTest(stderr=stderr[:20], stdout=stdout[:20]):
                self.assertEqual(self.failure(stderr=stderr, stdout=stdout),
                                 'independent acceptance failed: ' + (stderr or stdout)[-600:])
