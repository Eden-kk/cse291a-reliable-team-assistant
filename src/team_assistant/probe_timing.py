"""Conservative lexical gate for Kubernetes liveness timing claims.

This development-only gate does not certify diagnosis or arbitrary prose. It
checks a small, explicit claim vocabulary against returned kubectl describe
records; uncertain phrasing is held for review, never silently rewritten.
"""
import re

LINE = re.compile(r'^\s*Liveness:\s+.*?delay=(\d+)s\s+timeout=(\d+)s\s+period=(\d+)s\s+#success=(\d+)\s+#failure=(\d+)\s*$', re.M)
NUMBER = r'(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)'
RATE = re.compile(rf'(?:fail\w*\s+({NUMBER})(?:\s+consecutive)?\s+times|({NUMBER})(?:\s+consecutive)?\s+(?:failures|failed\s+(?:checks|probes|attempts)))\s+(?:per|in\s+(?:each|every|a|one|single)|(?:during\s+)?(?:each|every))\s+(?:(?:single|probe)\s+)?(?:period|interval)', re.I)
WORDS = dict(zip(('one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten'), range(1, 11)))
TIMING = re.compile(r'\b(?:period|interval|cycle|seconds?|delay|timeout|timing|threshold)\b|\b\d+\s*s\b', re.I)


def validate_probe_timing(text: str, records: list[dict]) -> dict:
    """Return block/review/no_detected_conflict, with limited scope explicit.

    Caller must supply only raw records actually visible to the author and from
    the intended resource. Multiple distinct configurations force abstention.
    Negation, quotation and hypothetical scopes are deliberately not inferred.
    """
    evidence = []
    for record in records:
        if record.get('availability') != 'returned' or record.get('truncated'):
            continue
        if record.get('tool') != 'DescribeResource':
            continue
        for match in LINE.finditer(record.get('text', '')):
            evidence.append({'source_id': record['id'], 'quote': match.group(0).strip(),
                             'values': tuple(map(int, match.groups()))})
    values = {e['values'] for e in evidence}
    findings = []
    for sentence in re.split(r'(?<=[.!?])\s+|\n', text):
        if not TIMING.search(sentence):
            continue
        rate = RATE.search(sentence)
        # A question, negation, quote or conditional needs semantic review.
        ambiguous = re.search(r'\b(?:not|never|no|if|might|may|could|possibly|claim(?:s|ed)?|says|said)\b|[?"“”]', sentence, re.I)
        count_word = next((g.lower() for g in rate.groups() if g), '') if rate else ''
        count = int(count_word) if count_word.isdigit() else WORDS.get(count_word, 0)
        if rate and count > 1 and len(values) == 1 and not ambiguous:
            verdict = 'block'
            reason = 'Conflates the consecutive-failure threshold with failures per probe period.'
        else:
            verdict = 'review'
            reason = 'Timing wording or source scope is outside this narrow validator.'
        findings.append({'text': sentence, 'verdict': verdict, 'reason': reason})
    decision = ('block' if any(f['verdict'] == 'block' for f in findings)
                else 'review' if findings else 'no_detected_conflict')
    wording = None
    if len(values) == 1:
        delay, timeout, period, success, failure = next(iter(values))
        wording = (f'The observed liveness configuration is initial delay {delay}s, timeout {timeout}s, '
                   f'period {period}s, success threshold {success}, and failure threshold {failure}. '
                   f'The failure threshold counts consecutive failed probes; it is not a count of failures '
                   f'within one period. These settings alone do not establish exact restart timing.')
    return {'decision': decision, 'findings': findings, 'evidence': evidence,
            'suggested_wording_for_review': wording,
            'scope': 'Lexical timing gate only; no_detected_conflict is not a correctness certificate.'}
