"""Audit recorded scheduling trials after inference; never discard failed windows."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_text())


def summarize(root):
    batch = read(root / 'summary.json')
    manifest = read(root / 'manifest.json')
    calls = [read(p) for p in root.glob('repeat-*/**/calls/*.json')
             if not p.name.endswith('.request.json')]
    known = sum(c['tokens'] for c in calls if c['tokens'] is not None)
    unknown = sum(c['tokens'] is None for c in calls)
    charged = known + unknown * manifest['call_reservation']
    assert charged == batch['physical_tokens'], 'Physical call ledger mismatch'
    assert sum(s['charged'] for s in batch['physical_settlements']) == charged
    rows = []
    for repeat in batch['repeats']:
        for policy in manifest['policy_orders'][repeat['repeat'] - 1]:
            window = repeat.get('windows', {}).get(policy)
            path = root / f"repeat-{repeat['repeat']}" / policy
            if not window or 'outcomes' not in window:
                rows.append({'repeat': repeat['repeat'], 'policy': policy,
                             'state': 'skipped', 'reason': window or repeat.get('state')})
                continue
            assert window['logical_total_tokens'] == window['prefix_tokens'] + sum(
                s['charged'] for s in window['settlements'])
            assert window['incremental_physical_tokens'] == sum(
                s['charged'] for s in window['settlements'])
            starts = {e['job']: e for e in window['dispatch_events'] if e['event'] == 'start'}
            event_counts = Counter(e['event'] for e in window['dispatch_events'])
            for group, outcome in window['outcomes'].items():
                result_path = path / group / 'result.json'
                result = read(result_path) if result_path.exists() else {}
                score_path = path / group / 'scores.json'
                scores = read(score_path)['scores'] if score_path.exists() else None
                budget_valid = not window['logical_budget_halted'] and not window['physical_halted']
                native_success = (scores is not None and outcome['state'] == 'completed'
                                  and scores['JRA'] == 1 and scores['ECR'] == 1
                                  and not result.get('contract_errors', ['missing']) and budget_valid)
                slo = manifest['response_slo_seconds' if group == 'foreground' else 'recovery_slo_seconds']
                group_calls = read(path / group / 'usage.json')['calls']
                rows.append({'repeat': repeat['repeat'], 'policy': policy, 'group': group,
                             **outcome, 'scores': scores, 'native_success': native_success,
                             'native_success_within_slo': native_success and outcome['seconds'] <= slo,
                             'incremental_tokens': sum(c['tokens'] if c['tokens'] is not None else
                                                       manifest['call_reservation'] for c in group_calls),
                             'model_calls': len(group_calls), 'budget_valid': budget_valid,
                             'queue_seconds': sum(e['queue_seconds'] for e in starts.values() if e['group'] == group),
                             'window_event_counts': dict(event_counts)})
    files = sorted(p for p in root.rglob('*.json') if p.name != 'audit.json')
    return {'manifest': manifest, 'physical_calls': len(calls), 'known_tokens': known,
            'unknown_usage_calls': unknown, 'charged_tokens': charged,
            'physical_halted': batch['physical_halted'], 'rows': rows,
            'input_sha256': {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
            'limitations': ['native success requires later semantic review',
                           'two repeated known cases; policy order counterbalanced, not statistical evidence',
                           'live outputs and provider latency can differ between policies',
                           'adaptive five-call upper bound retains allowance until early workflow exit']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Refuse overwrite')
    report = summarize(args.root)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k not in ('input_sha256', 'manifest')}, indent=2))
