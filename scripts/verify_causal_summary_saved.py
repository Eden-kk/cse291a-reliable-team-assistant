"""Offline checks for the single saved development pilot; no semantic scoring oracle."""
import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path


def verify(root):
    def read(name):
        return json.loads((root / name).read_text())
    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    summary, usage = read('summary.json'), read('usage.json')
    calls = usage['calls']
    assert len(calls) == 11 and not usage['halted']
    assert all(c['error'] is None for c in calls)
    billed = sum(Decimal(c['headers']['x-litellm-response-cost']) for c in calls)
    delta = Decimal(str(read('account-settled.json')['spend'])) - Decimal(str(read('account-before.json')['spend']))
    assert abs(delta - billed) < Decimal('0.000000001')
    clean, corrupt = read('clean/result.json'), read('corrupt/result.json')
    assert clean['observations'] == corrupt['observations']
    assert not summary['arms']['checks']['wrong_fact_adopted']
    assert read('notification.json')['invalid'] == ['configuration_summary', 'incident_brief']
    metrics = {}
    for arm in ('prefix', 'clean', 'corrupt', 'restart', 'selective'):
        data = summary['arms'][arm]
        assert data['state'] == 'completed'
        records = [c for c in calls if c['call'] in data['calls']]
        metrics[arm] = {'calls': len(records), 'tokens': sum(c['tokens'] for c in records),
                        'billed_usd': str(sum(Decimal(c['headers']['x-litellm-response-cost']) for c in records)),
                        'wall_seconds': data['wall_seconds']}
        if arm == 'prefix':
            continue
        result = read(f'{arm}/result.json')
        assert not result['contract_errors']
        assert result['answer']['adservice_liveness_protocol'] == 'httpGet'
        if arm in ('restart', 'selective'):
            reuse = read(f'{arm}/reuse.json')
            state = read(f'{arm}/state.json')
            for name, value in state.items():
                assert digest(value) == reuse['after_hashes'][name]
            for name in reuse['valid_unchanged_artifacts']:
                assert reuse['before_hashes'][name] == reuse['after_hashes'][name]
            assert not reuse['invalid_artifacts']
            assert read(f'{arm}/ledger.json')[-1]['supersedes'] == 1
            assert state['configuration_summary']['payload']['liveness_protocol'] == 'httpGet'
    assert len(read('selective/reuse.json')['valid_unchanged_artifacts']) == 5
    assert not read('restart/reuse.json')['valid_unchanged_artifacts']
    assert summary['arms']['no-recovery']['stale_republication_rejected']
    return {'calls': len(calls), 'tokens': sum(c['tokens'] for c in calls), 'billed_usd': str(billed),
            'arms': metrics, 'propagated_errors': 0, 'injected_errors': 1,
            'repaired_propagated_diagnosis': False, 'unchanged_valid_artifacts': 5,
            'limitations': 'Checks saved facts/hashes/receipts, not independent semantic correctness or generalization.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.root), indent=2))
