"""Summarize recorded E5 smoke without reinterpreting provisional publications as approval."""
import argparse
import json
from pathlib import Path
from collections import Counter


def read(path): return json.loads(path.read_text())


def summarize(root):
    summary, manifest = read(root/'summary.json'), read(root/'manifest.json')
    stages, tokens, calls = {}, [], []
    for name in ('prefix','recovery','foreground'):
        path = root/name
        outcome = summary['outcomes'].get(name, {'state':'not_started'})
        usage = read(path/'usage.json')['calls'] if (path/'usage.json').exists() else []
        result = read(path/'result.json') if (path/'result.json').exists() else {}
        score = read(path/'scores.json')['scores'] if (path/'scores.json').exists() else None
        amounts = [c.get('tokens') for c in usage]
        calls.extend(usage); tokens.extend(amounts)
        stages[name] = {'outcome':outcome, 'model_calls':len(usage), 'incremental_tokens':
                        sum(amounts) if all(type(t) is int and t >= 0 for t in amounts) else None,
                        'answer_status':result.get('answer',{}).get('status'),
                        'contract_errors':result.get('contract_errors'), 'native_scores':score,
                        'semantic_audit':read(path/'semantic-audit.json') if (path/'semantic-audit.json').exists() else None}
    settlements = summary['settlements']
    checks = {'known_usage':all(type(t) is int and t >= 0 for t in tokens),
              'call_settlement_match':Counter(tokens)==Counter(s['actual'] for s in settlements),
              'charged_match':all(s['charged']==s['actual'] and not s['halted'] for s in settlements),
              'no_provider_errors':all(not c.get('error') for c in calls),
              'candidate_rejected':summary['rejected_candidate_not_activated'],
              'formal_within_cap':summary['physical_tokens']+manifest['preparation_tokens_already_spent']<=manifest['physical_token_cap']}
    if checks['known_usage']: checks['physical_total_match'] = sum(tokens)==summary['physical_tokens']
    if not all(checks.values()): raise ValueError(checks)
    return {'kind':'Seen-development real smoke; no paired efficacy comparison', 'manifest':manifest,
            'stages':stages, 'accounting_checks':checks, 'formal_model_calls':len(calls),
            'formal_tokens':summary['physical_tokens'],
            'preparation_tokens_terminal_only':manifest['preparation_tokens_already_spent'],
            'known_total_tokens':summary['physical_tokens_including_preparation'],
            'review_orchestration_cost':None, 'ledgers':summary['ledgers'],
            'publication_scope':'Local provisional status and correction only; no external posting/approval',
            'limitations':['Constructed query history, not real evolving cluster',
                           'Rejected learning receipt, not successful learning promotion/revocation',
                           'No in-flight revocation tested in this smoke',
                           'Single run; two diagnosis rounds; no guard/restart counterfactual']}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('root',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=summarize(args.root)
    with args.output.open('x') as out: json.dump(result,out,indent=2)
    print(json.dumps({k:result[k] for k in ('formal_model_calls','formal_tokens','known_total_tokens','accounting_checks')}))
