"""Verify a relocated saved-output release without network or inference."""
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import socket
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from score_formal_v2 import inputs
from score_run import score


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    def offline(*args, **kwargs):
        raise RuntimeError('Network disabled during saved-output verification')
    socket.socket.connect = offline
    manifest = read(ROOT / 'RELEASE-MANIFEST.json')
    for name, digest in manifest['files'].items():
        assert sha(ROOT / name) == digest, name
    run = ROOT / 'formal-results'
    sealed = read(run / 'evidence-review-seal.json')
    # Resolve the archived review by content, never by the author's absolute path.
    review = run / 'input-only-review.json'
    assert sha(review) == sealed['review_sha256']
    assert inputs(run) == sealed['input_hashes']
    rows = read(review)['rows']
    scores = read(run / 'formal-scores.json')['rows']
    scored = {row['arm']: row for row in scores if row['state'] == 'produced_answer'}
    assert set(rows) == set(scored)
    with tempfile.TemporaryDirectory() as tmp:
        for i, (name, judgment) in enumerate(rows.items()):
            out = Path(tmp) / f'{i}.json'
            score(run / name, ROOT / 'vendor_offline/Cloud-OpsBench', out)
            native = read(out)['scores']
            assert native == scored[name]['native'], name
            answer = read(run / name / 'result.json')['answer']
            semantic = (answer.get('status') == 'diagnosed' and native['JRA'] == 1
                        and judgment['supported'] == 'yes' and judgment['publication_current']
                        and native['contract_valid'] and native['budget_valid'])
            assert bool(semantic) == scored[name]['semantic_joint'], name
            assert bool(semantic and native['ECR'] == 1) == scored[name]['strict_joint'], name
    assert len(scores) == 54 and len(rows) == 36
    calls = read(run / 'usage.json')['calls']
    assert len(calls) == 92 and sum(c['tokens'] for c in calls) == 519696
    assert all(c.get('error') is None for c in calls)
    billed = sum(Decimal(c['headers']['x-litellm-response-cost']) for c in calls)
    delta = (Decimal(str(read(run / 'account-refreshed.json')['spend']))
             - Decimal(str(read(run / 'account-before.json')['spend'])))
    assert abs(billed - delta) < Decimal('0.000000001')
    assert billed < 20

    result = {'hashes_verified': len(manifest['files']), 'native_answers_rescored': len(rows),
              'assigned_slots_retained': len(scores), 'network_disabled': True,
              'model_calls': 0, 'saved_calls': len(calls), 'billed_usd': str(billed), 'review_hash_preserved': sealed['review_sha256'],
              'limitations': 'Saved judgments verified, not independently re-reviewed; no new inference.'}
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
