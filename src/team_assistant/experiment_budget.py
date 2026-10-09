"""Conservative preflight for complete paired investigation batches."""


def paired_budget_plan(pairs, repetitions, reservation, logical_limit, physical_limit):
    values = (pairs, repetitions, reservation, logical_limit, physical_limit)
    if any(type(v) is not int or v <= 0 for v in values):
        raise ValueError('Budget and experiment counts must be positive integers')
    # Investigation.collect has two planners; diagnose permits three model turns.
    calls_per_arm = 5
    arm_bound = calls_per_arm * reservation
    required = pairs * repetitions * 2 * arm_bound
    reasons = []
    if logical_limit < arm_bound:
        reasons.append('logical_limit_cannot_cover_maximum_arm_calls')
    if physical_limit < required:
        reasons.append('physical_limit_cannot_cover_complete_batch')
    return {'admitted': not reasons, 'reasons': reasons, 'pairs': pairs,
            'repetitions': repetitions, 'calls_per_arm_bound': calls_per_arm,
            'reservation_per_call': reservation, 'required_per_arm': arm_bound,
            'required_per_pair': 2 * arm_bound, 'required_batch': required,
            'physical_limit': physical_limit, 'logical_limit': logical_limit,
            'scope': 'Reservation coverage only; unknown usage or per-call overrun still halts. '
                     'Does not guarantee successful diagnosis or authorize spending.'}


def require_complete_batch(**kwargs):
    plan = paired_budget_plan(**kwargs)
    if not plan['admitted']:
        raise ValueError('Batch budget preflight rejected: ' + ', '.join(plan['reasons'])
                         + f"; required={plan['required_batch']}, declared={plan['physical_limit']}")
    return plan
