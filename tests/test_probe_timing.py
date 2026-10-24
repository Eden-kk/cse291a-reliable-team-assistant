import unittest
from team_assistant.probe_timing import validate_probe_timing

RAW = {'id': 'r1', 'availability': 'returned', 'truncated': False,
       'tool': 'DescribeResource',
       'text': 'Liveness: http-get http://:9555/ delay=5s timeout=1s period=10s #success=1 #failure=3'}


class ProbeTimingTests(unittest.TestCase):
    def check(self, text, expected, records=None):
        result = validate_probe_timing(text, [RAW] if records is None else records)
        self.assertEqual(result['decision'], expected, text)
        return result

    def test_explicit_wrong_rate_paraphrases(self):
        for text in ['The probe fails 3 times per period.',
                     'Three failures in each interval cause a restart.',
                     'The probe fails three consecutive times every probe period.']:
            self.check(text, 'block')

    def test_supported_or_nontiming_claims_not_blocked(self):
        self.check('Three consecutive probe failures trigger restart.', 'no_detected_conflict')
        self.check('The probe fails one time per period.', 'review')
        self.check('The period is 10 seconds and failure threshold is 3.', 'review')
        self.check('HTTP probing is inconsistent with gRPC.', 'no_detected_conflict')

    def test_ambiguous_negated_and_quoted_go_to_review(self):
        for text in ['The probe does not fail 3 times per period.',
                     'Does the probe fail 3 times per period?',
                     'The summary says the probe fails 3 times per period.',
                     'If the probe fails 3 times per period, inspect it.',
                     'The probe fails 3 times each cycle.']:
            self.check(text, 'review')

    def test_missing_or_conflicting_evidence_abstains(self):
        claim = 'The probe fails 3 times per period.'
        self.check(claim, 'review', [])
        self.check(claim, 'review', [dict(RAW, truncated=True)])
        self.check(claim, 'review', [RAW, dict(RAW, id='r2', text=RAW['text'].replace('period=10', 'period=5'))])

    def test_suggestion_quotes_values_without_exact_restart_time(self):
        result = self.check('The probe fails 3 times per period.', 'block')
        self.assertIn('period 10s', result['suggested_wording_for_review'])
        self.assertIn('do not establish exact restart timing', result['suggested_wording_for_review'])
        self.assertEqual(result['evidence'][0]['source_id'], 'r1')


if __name__ == '__main__':
    unittest.main()
