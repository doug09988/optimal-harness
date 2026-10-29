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
        for text in ('I approve.\nAPPROVE', 'Here is the verdict:\n{"verdict":"APPROVE"}',
                     ' APPROVE', 'APPROVE extra', '["APPROVE"]', '"APPROVE"',
                     '{"verdict":"APPROVE"}\nextra', ''):
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
