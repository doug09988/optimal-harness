"""Stage 36: one REVIEW verdict parser for JSON and legacy responses."""
import json
import unittest

from optimal_harness.policy import PolicyError
from optimal_harness.review import parse_verdict


class ReviewVerdictTests(unittest.TestCase):
    def test_json_formats_and_exact_values(self):
        entries = [{'identity': 'claim', 'disposition': 'STALE', 'note': 'superseded'}]
        for verdict in ('APPROVE', 'REQUEST_CHANGES'):
            for indent in (None, 2):
                with self.subTest(verdict=verdict, indent=indent):
                    text = ' \n' + json.dumps(dict(verdict=verdict,
                        discrepancy_resolutions=entries), indent=indent) + '\n '
                    self.assertEqual(parse_verdict(text), (verdict, entries))

    def test_legacy_first_line_unchanged(self):
        for verdict in ('APPROVE', 'REQUEST_CHANGES'):
            for suffix in ('', '\nEvidence follows.'):
                with self.subTest(verdict=verdict, suffix=suffix):
                    self.assertEqual(parse_verdict(verdict + suffix), (verdict, []))

    def test_invalid_json_verdicts(self):
        for value in ('INVALID', 'APPROVE\nextra', ' APPROVE', None, [], {}):
            with self.subTest(value=value), self.assertRaisesRegex(PolicyError, 'unparseable_verdict'):
                parse_verdict(json.dumps({'verdict': value}))
        with self.assertRaisesRegex(PolicyError, 'unparseable_verdict'):
            parse_verdict('{}')

    def test_prose_first_and_other_formats_rejected(self):
        for text in ('I approve.\nAPPROVE',
                     ' APPROVE', 'APPROVE extra', '["APPROVE"]', '"APPROVE"',
                     'garbage', ''):
            with self.subTest(text=text), self.assertRaisesRegex(PolicyError, 'unparseable_verdict'):
                parse_verdict(text)

    def test_verify49_trailing_period(self):
        text = ('{"verdict": "REQUEST_CHANGES", "discrepancy_resolutions": '
                '[{"identity": "6e0768d0eab2d47eb9a53aaff2b812e6d59f850a00c743d4970d3c1865befd30", '
                '"disposition": "STALE", "note": "The test suite syntax error and runtime '
                'environment issues are not addressed in the current review. The test suite '
                'must be fixed to ensure correct execution and verification of the implementation."}]}.')
        expected = json.loads(text[:-1])
        self.assertEqual(parse_verdict(text),
                         ('REQUEST_CHANGES', expected['discrepancy_resolutions']))

    def test_balanced_object_wrappers_and_string_escapes(self):
        entries = [{'identity': 'claim', 'disposition': 'STALE',
                    'note': 'Braces } {, quoted "} braces", and backslash \\',
                    'details': {'nested': {'evidence': 'checked'}}}]
        for verdict in ('APPROVE', 'REQUEST_CHANGES'):
            payload = json.dumps(dict(verdict=verdict, discrepancy_resolutions=entries))
            for text in (payload + '\nEvidence follows.', '```json\n' + payload + '\n```',
                         'Here is the verdict:\n' + payload):
                with self.subTest(text=text):
                    self.assertEqual(parse_verdict(text), (verdict, entries))

    def test_extraction_uses_only_first_object(self):
        self.assertEqual(parse_verdict('{"verdict":"APPROVE"}{"verdict":"REQUEST_CHANGES"}'),
                         ('APPROVE', []))
        for first in ('{invalid}', '{}', '{"verdict":"INVALID"}'):
            with self.subTest(first=first), self.assertRaisesRegex(PolicyError, 'unparseable_verdict'):
                parse_verdict(first + '\n{"verdict":"APPROVE"}')

    def test_extraction_does_not_salvage_truncated_outer_object(self):
        for text in ('{"verdict":"APPROVE", "details":{"verdict":"APPROVE"}',
                     '```json\n{"verdict":"APPROVE", "note":"unfinished }',
                     'Preamble {"verdict":"APPROVE"'):
            with self.subTest(text=text), self.assertRaisesRegex(PolicyError, 'unparseable_verdict'):
                parse_verdict(text)

    def test_truncated_json_is_rejected(self):
        payload = json.dumps({'verdict': 'APPROVE', 'discrepancy_resolutions': [
            {'identity': 'claim', 'disposition': 'STALE', 'note': 'superseded evidence'}]})
        with self.assertRaisesRegex(PolicyError, 'unparseable_verdict'):
            parse_verdict(payload[:payload.index('evidence') + 3])

    def test_driven_verdict_completion_cap(self):
        from optimal_harness.review import DrivenReview
        review = DrivenReview('QA')
        for reply in ('QA', 'ATTEMPTED missing input', '', ''):
            self.assertEqual(review.completion_cap, 2048)
            review.accept(reply)
        self.assertEqual(review.current, 'verdict')
        self.assertEqual(review.completion_cap, 1024)
