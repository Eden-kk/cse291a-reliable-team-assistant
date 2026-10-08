"""Deterministic offline promotion decision over paired, audited validation runs."""
import math
from .memory import digest


def evaluate(candidate, baseline, pairs, registry, thresholds, generation_tokens):
    if (not 0 < thresholds["min_token_saving"] <= 1
            or not 0 < thresholds["max_token_ratio"] <= 1):
        raise ValueError("Require positive savings threshold and no cost regression")
    reasons = []
    expected = set(registry['splits']['gate_validation'])
    if not expected or len(pairs) != len(expected) or {p['case'] for p in pairs} != expected:
        reasons.append('incomplete_or_duplicate_validation_cases')
    if not isinstance(generation_tokens, int) or isinstance(generation_tokens, bool) or generation_tokens < 0:
        reasons.append('unknown_candidate_generation_cost')
    costs_known = True
    old_tokens = new_tokens = old_success = new_success = 0
    reference = None
    for pair in pairs:
        case = pair['case']
        left, right = pair['baseline'], pair['candidate']
        for label, run, policy in (('baseline', left, baseline), ('candidate', right, candidate)):
            manifest, scores = run['manifest'], run['scores']
            identity = tuple(manifest.get(k) for k in
                             ('code_commit', 'dataset_commit', 'model', 'reservation', 'token_limit'))
            if reference is None:
                reference = identity
            if (identity != reference or any(v is None for v in identity) or manifest.get('dirty') is not False
                    or manifest.get('dataset_commit') != registry['dataset_commit']):
                reasons.append(f'{case}:{label}:incomparable_execution')
            if (manifest.get('case') != case or manifest.get('split') != 'gate_validation'
                    or digest(manifest.get('policy')) != digest(policy)):
                reasons.append(f'{case}:{label}:wrong_case_split_or_policy')
            if run.get('semantic_supported') is not True:
                reasons.append(f'{case}:{label}:missing_or_failed_semantic_audit')
            if scores.get('contract_valid') is not True or scores.get('budget_valid') is not True:
                reasons.append(f'{case}:{label}:invalid_contract_or_budget')
            for metric in ('JRA', 'ECR'):
                value = scores.get(metric)
                if not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
                    reasons.append(f'{case}:{label}:invalid_{metric}')
            tokens = run.get('tokens')
            if not isinstance(tokens, int) or isinstance(tokens, bool) or tokens <= 0:
                reasons.append(f'{case}:{label}:unknown_cost')
                costs_known = False
            elif not isinstance(manifest.get('token_limit'), int) or tokens > manifest['token_limit']:
                reasons.append(f'{case}:{label}:exceeded_declared_budget')
        a, b = left['scores'], right['scores']
        if all(isinstance(s.get(k), (int, float)) for s in (a, b) for k in ('JRA', 'ECR')):
            if any(b[k] < a[k] for k in ('JRA', 'ECR')):
                reasons.append(f'{case}:quality_regression')
            old_success += a['JRA'] == 1 and a['ECR'] == 1
            new_success += b['JRA'] == 1 and b['ECR'] == 1
        old_tokens += left['tokens'] if isinstance(left.get('tokens'), int) else 0
        new_tokens += right['tokens'] if isinstance(right.get('tokens'), int) else 0
    saving = (old_tokens - new_tokens) / old_tokens if costs_known and old_tokens > 0 else None
    if new_tokens > old_tokens * thresholds['max_token_ratio']:
        reasons.append('cost_regression')
    if not (new_success > old_success or (saving is not None and saving >= thresholds['min_token_saving'])):
        reasons.append('no_measurable_benefit')
    validation_cost = old_tokens + new_tokens if costs_known else None
    overhead = validation_cost + generation_tokens if (validation_cost is not None
                and isinstance(generation_tokens, int) and not isinstance(generation_tokens, bool)
                and generation_tokens >= 0) else None
    per_case_saving = (old_tokens - new_tokens) / len(pairs) if pairs else 0
    return {'accepted': not reasons, 'sha256': digest(candidate), 'baseline_sha256': digest(baseline),
            'reasons': sorted(set(reasons)), 'registry_sha256': digest(registry),
            'thresholds': thresholds, 'evidence_sha256': digest(pairs),
            'validation_tokens': validation_cost, 'generation_tokens': generation_tokens,
            'total_learning_tokens': overhead, 'relative_token_saving': saving,
            'baseline_success': old_success, 'candidate_success': new_success,
            'estimated_break_even_future_cases': math.ceil(overhead / per_case_saving)
            if overhead is not None and per_case_saving > 0 else None,
            'scope': 'validation decision only; extrapolated break-even is not measured net benefit'}
