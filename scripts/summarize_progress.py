"""Derived experiment ledger; raw run directories remain immutable."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
runs = ROOT / 'runs'
records = []
for path in runs.glob('**/calls/*.json'):
    if path.stem.isdigit():
        record = json.loads(path.read_text())
        records.append(record)
summary = {'scope': 'development/shakedown and integration, not final evaluation',
           'all_recorded_inference': {'calls': len(records),
                                     'tokens': sum(r['tokens'] or 0 for r in records),
                                     'errors': sum(bool(r['error']) for r in records),
                                     'unknown_usage': sum(r['tokens'] is None for r in records),
                                     'includes': 'all attempts, including superseded timing/protocol v1 runs'},
           'shakedown': [], 'recovery': {}, 'multigroup': {}}
for case in ('runtime-39', 'runtime-42', 'service-46', 'performance-28'):
    for policy in ('empty', 'candidate'):
        directory = runs / f'shakedown-{case}-{policy}-v1'
        entry = {'case': case, 'policy': policy, 'state': 'missing'}
        if (directory / 'result.json').exists():
            result = json.loads((directory / 'result.json').read_text())
            entry.update(state='failed' if result.get('error') else 'completed',
                         tokens=result.get('budget', {}).get('spent'), answer_status=result.get('answer', {}).get('status'))
        if (directory / 'scores.json').exists():
            entry['scores'] = json.loads((directory / 'scores.json').read_text())['scores']
        summary['shakedown'].append(entry)
path = runs / 'wrong-claim-recovery-v2/summary.json'
if path.exists():
    result = json.loads(path.read_text())
    summary['recovery'] = {k: v for k, v in result.items() if not k.endswith('answer')}
for name in ('fifo-v2', 'fair-v2', 'fair-v3'):
    directory = runs / f'multigroup-{name}'
    if not (directory / 'summary.json').exists():
        summary['multigroup'][name] = {'state': 'pending_or_failed'}
        continue
    result = json.loads((directory / 'summary.json').read_text())
    entry = {k: v for k, v in result.items() if k not in ('dispatch_events', 'settlements')}
    entry['native_success_groups'] = []
    entry['native_quality_slo_groups'] = []
    for group in ('A', 'B', 'C'):
        score = directory / group / 'scores.json'
        if score.exists():
            scores = json.loads(score.read_text())['scores']
            successful = scores['JRA'] == 1 and scores['ECR'] == 1 and scores['contract_valid'] and scores['budget_valid']
            if successful:
                entry['native_success_groups'].append(group)
                outcome = next(o for o in result['outcomes'] if o['group'] == group)
                if outcome['slo_met_contract_only']:
                    entry['native_quality_slo_groups'].append(group)
    summary['multigroup'][name] = entry
(ROOT / 'docs/development-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
print(json.dumps(summary['all_recorded_inference']))
