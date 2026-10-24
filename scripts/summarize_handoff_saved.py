"""Offline accounting and integrity checks; semantic judgments are separate."""
import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import statistics


def summarize(root):
    def read(name):
        return json.loads((root / name).read_text())
    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    raw = read('summary.json')
    calls = read('usage.json')['calls']
    assert len(raw['repeats']) == 3 and len(calls) <= 54
    assert all(c['error'] is None and c['status'] == 200 for c in calls)
    assert all(c['input_bytes'] + 512 <= 40000 for c in calls)
    billed = sum(Decimal(c['headers']['x-litellm-response-cost']) for c in calls)
    delta = Decimal(str(read('account-settled.json')['spend'])) - Decimal(str(read('account-before.json')['spend']))
    assert abs(delta - billed) < Decimal('0.000000001')
    rows = []
    for repeat in raw['repeats']:
        n, arms = repeat['repeat'], repeat['arms']
        assert set(arms) == {'prefix', 'clean', 'corrupt', 'raw-visible-corrupt', 'no-recovery', 'restart', 'selective'}
        for arm, data in arms.items():
            records = [c for c in calls if c['call'] in data['calls']]
            row = {'repeat': n, 'arm': arm, 'state': data['state'], 'calls': len(records),
                   'tokens': sum(c['tokens'] for c in records),
                   'billed_usd': str(sum(Decimal(c['headers']['x-litellm-response-cost']) for c in records)),
                   'wall_seconds': data.get('wall_seconds')}
            result = data.get('result')
            if result:
                row.update(protocol=result['answer'].get('adservice_liveness_protocol'),
                           answer_status=result['answer'].get('status'),
                           root_cause=result['answer'].get('root_cause'),
                           contract_errors=result['contract_errors'], retrieved_ids=result['retrieved_ids'])
                prefix_ids = arms['prefix']['calls']
                path_ids = prefix_ids + data['calls']
                if arm in ('restart', 'selective'):
                    path_ids += arms['corrupt']['calls']
                logical = sum(c['tokens'] for c in calls if c['call'] in path_ids)
                assert logical == result['logical_tokens_spent']
                row['logical_total_tokens'] = logical
            if arm in ('restart', 'selective') and data['state'] == 'completed':
                reuse = read(f'repeat-{n}/{arm}/reuse.json')
                state = read(f'repeat-{n}/{arm}/state.json')
                for name, value in state.items():
                    assert digest(value) == reuse['after_hashes'][name]
                for name in reuse['valid_unchanged_artifacts']:
                    assert reuse['before_hashes'][name] == reuse['after_hashes'][name]
                ledger = read(f'repeat-{n}/{arm}/ledger.json')
                assert ledger[-1]['supersedes'] == 1
                row['retained_artifacts'] = reuse['valid_unchanged_artifacts']
                row['invalid_after'] = reuse['invalid_after']
                assert 'incident_brief' not in reuse['invalid_after']
            rows.append(row)
        assert arms['no-recovery'].get('stale_republication_rejected') or arms['no-recovery']['state'] != 'invalid'
    paired = []
    for n in range(1, 4):
        r = next(v for v in rows if v['repeat'] == n and v['arm'] == 'restart')
        s = next(v for v in rows if v['repeat'] == n and v['arm'] == 'selective')
        if r['state'] == s['state'] == 'completed':
            paired.append({'repeat': n, 'token_reduction_fraction': 1-s['tokens']/r['tokens'],
                           'wall_reduction_fraction': 1-s['wall_seconds']/r['wall_seconds']})
    aggregate = {}
    for arm in ('restart', 'selective'):
        completed = [v for v in rows if v['arm'] == arm and v['state'] == 'completed']
        aggregate[arm] = {key: {'mean': statistics.mean(v[key] for v in completed),
                               'min': min(v[key] for v in completed), 'max': max(v[key] for v in completed)}
                          for key in ('calls', 'tokens', 'wall_seconds')} if completed else {}
    return {'physical_calls': len(calls), 'physical_tokens': sum(c['tokens'] for c in calls),
            'billed_usd': str(billed), 'remaining_usd': read('account-settled.json')['max_budget']-read('account-settled.json')['spend'],
            'assigned_arms': len(rows), 'rows': rows, 'paired_descriptive_deltas': paired,
            'descriptive_aggregate': aggregate, 'semantic_scoring': 'Separate blinded AI review; not inferred by this script.',
            'limits': 'One seen case, three repeats; no generalization, significance, or p95 estimate.'}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('root', type=Path)
    args = p.parse_args()
    print(json.dumps(summarize(args.root), indent=2))
