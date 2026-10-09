"""Reconcile a frozen validation batch before applying its promotion gate."""
import argparse
from collections import Counter
import json
import hashlib
import subprocess
from pathlib import Path
from evaluate_learning import load_run, read, ROOT
from run_learning_validation import validate_freeze
from team_assistant.learning_gate import evaluate
from team_assistant.memory import digest


def audit(directory):
    frozen, registry = validate_freeze(ROOT / 'configs/learning/bundle-v1',
                                      ROOT / 'configs/splits.json', ROOT / 'configs/learning-gate.json')
    manifest, summary = read(directory / 'manifest.json'), read(directory / 'summary.json')
    settings_hash = digest(manifest['execution_settings'])
    errors, pairs, calls = [], [], []
    expected_order = [f'pair-{i}/{a}' for i, row in enumerate(frozen['cases'], 1) for a in row['order']]
    if list(summary['outcomes']) != expected_order:
        errors.append('execution_order_mismatch')
    settings = manifest['execution_settings']
    required = {'model': 'gpt-6-astra', 'reasoning_effort': 'low', 'context_window': 131072,
                'call_reservation': 60000, 'concurrency': 2, 'max_tools': 24}
    if any(settings.get(k) != v for k, v in required.items()):
        errors.append('execution_settings_not_predeclared')
    for name, source_hash in settings['source_sha256'].items():
        source = subprocess.check_output(['git', '-C', str(ROOT), 'show',
                  manifest['code_commit'] + ':src/team_assistant/' + name])
        if hashlib.sha256(source).hexdigest() != source_hash:
            errors.append('execution_source_mismatch:' + name)
    expected = {f'pair-{i}/{arm}' for i in range(1, 7) for arm in ('baseline', 'candidate')}
    if set(summary['outcomes']) != expected:
        errors.append('incomplete_outcome_ledger')
    if manifest['frozen'] != frozen or manifest['execution_settings_sha256'] != settings_hash:
        errors.append('changed_freeze_or_settings')
    for i, row in enumerate(frozen['cases'], 1):
        pair = {'case': row['case']}
        for arm in ('baseline', 'candidate'):
            key = f'pair-{i}/{arm}'
            path = directory / key
            outcome = summary['outcomes'].get(key, {})
            if outcome.get('state') != 'completed':
                errors.append(key + ':incomplete_run')
            arm_calls = []
            if not (path / 'usage.json').exists():
                errors.append(key + ':missing_usage')
            else:
                arm_calls = read(path / 'usage.json')['calls']
                calls.extend(arm_calls)
                if not arm_calls or any(type(c.get('tokens')) is not int or c['tokens'] < 0 or c.get('error') for c in arm_calls):
                    errors.append(key + ':unknown_usage_or_provider_error')
                elif sum(c['tokens'] for c in arm_calls) != outcome.get('tokens'):
                    errors.append(key + ':usage_mismatch')
            if not all((path / name).exists() for name in ('manifest.json', 'result.json', 'scores.json')):
                errors.append(key + ':missing_artifacts')
                continue
            run = load_run(path)
            result = read(path / 'result.json')
            if result.get('logical_total_tool_attempts', 25) > settings['max_tools']:
                errors.append(key + ':tool_limit_exceeded')
            if any(run['manifest'].get(k) != v for k, v in
                   {'model': settings['model'], 'reservation': settings['call_reservation'],
                    'token_limit': frozen['logical_token_limit_per_run']}.items()):
                errors.append(key + ':declared_limits_mismatch')
            if result.get('model_calls') != arm_calls:
                errors.append(key + ':divergent_call_records')
            if (run['manifest'].get('execution_settings_sha256') != settings_hash
                    or run['manifest']['code_commit'] != manifest['code_commit']
                    or run['tokens'] != outcome.get('tokens')):
                errors.append(key + ':manifest_or_cost_mismatch')
            pair[arm] = run
        pairs.append(pair)
    completed_costs = [p[a]['tokens'] for p in pairs for a in ('baseline', 'candidate') if a in p]
    if (any(type(t) is not int or t < 0 for t in completed_costs)
            or sum(t for t in completed_costs if type(t) is int) != summary['physical_tokens']):
        errors.append('logical_physical_total_mismatch')
    settlements = summary['physical_settlements']
    if (Counter(c.get('tokens') for c in calls) != Counter(s['actual'] for s in settlements)
            or any(s['actual'] != s['charged'] or s['halted'] for s in settlements)
            or sum(s['charged'] for s in settlements) != summary['physical_tokens']
            or summary['physical_tokens'] > frozen['planned_physical_token_cap']
            or summary['physical_halted']):
        errors.append('physical_ledger_mismatch_or_halt')
    policies = ROOT / 'configs/learning/bundle-v1'
    gate = None
    if all('baseline' in p and 'candidate' in p for p in pairs):
        gate = evaluate(read(policies / 'candidate-bundle.json'), read(policies / 'baseline.json'),
                        pairs, registry, read(ROOT / 'configs/learning-gate.json'), frozen['generation_tokens'])
        # Acquisition and review are not both included by the legacy gate estimator.
        gate['estimated_break_even_future_cases'] = None
        gate['total_learning_tokens'] = None
    return {'accepted': not errors and gate is not None and gate['accepted'],
            'batch_errors': errors, 'gate': gate, 'model_calls': len(calls),
            'validation_tokens': summary['physical_tokens'],
            'source_collection_tokens': frozen['source_collection_tokens'],
            'generation_tokens': frozen['generation_tokens'],
            'known_total_tokens': frozen['known_acquisition_tokens'] + summary['physical_tokens'],
            'external_review_orchestration_tokens': None,
            'economics': 'Known subtotal only; no full net-benefit or payback claim',
            'batch_evidence_sha256': digest({'manifest': manifest, 'summary': summary, 'pairs': pairs, 'calls': calls}),
            'activation': 'none; receipt only'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.directory)
    with args.output.open('x') as out:
        json.dump(result, out, indent=2)
    print(json.dumps(result, indent=2))
