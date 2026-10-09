"""Audit all physical calls and retain unsuccessful groups in live calibration."""
import argparse
import hashlib
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_text())


def summarize(root):
    manifest=read(root/'manifest.json')
    batch=read(root/'summary.json')
    call_paths=[p for p in root.glob('**/calls/*.json') if not p.name.endswith('.request.json')]
    calls=[read(p) for p in call_paths]
    charged=lambda c: c['tokens'] if type(c.get('tokens')) is int else manifest['call_reservation']
    total=sum(charged(c) for c in calls)
    if total != batch['physical_tokens'] or total != sum(s['charged'] for s in batch['physical_settlements']):
        raise ValueError('Physical ledger mismatch')
    prefix=sum(charged(read(p)) for p in call_paths if p.parent.parent.name=='historical-prefix')
    incremental=0
    rows=[]
    for policy in manifest['policies']:
        window=batch['windows'].get(policy,{})
        if 'outcomes' not in window:
            rows.append({'policy':policy,'state':'skipped','details':window or batch.get('stop_reason')})
            continue
        tokens=sum(s['charged'] for s in window['settlements'])
        if (window['prefix_tokens'] != prefix or tokens != window['incremental_physical_tokens']
                or prefix+tokens != window['logical_total_tokens']):
            raise ValueError('Logical window ledger mismatch')
        incremental+=tokens
        group_tokens=0
        for name,outcome in window['outcomes'].items():
            directory=root/policy/name
            usage=read(directory/'usage.json')['calls']
            cost=sum(charged(c) for c in usage)
            group_tokens+=cost
            scores=read(directory/'scores.json')['scores'] if (directory/'scores.json').exists() else None
            result=read(directory/'result.json') if (directory/'result.json').exists() else {}
            success=(outcome['state']=='completed' and scores is not None
                     and scores['JRA']==1 and scores['ECR']==1
                     and not result.get('contract_errors',['missing'])
                     and not window['logical_budget_halted'] and not window['physical_halted'])
            rows.append({'policy':policy,'group':name,**outcome,'model_calls':len(usage),
                         'tokens':cost,'scores':scores,'native_success':success,
                         'within_exploratory_slo':success and outcome['seconds'] <= (60 if name=='recovery' else 30),
                         'semantic_support':'pending independent review',
                         'dispatch_queue_seconds':sum(e['queue_seconds'] for e in window['dispatch_events']
                             if e['event']=='start' and e['group']==name),
                         'admitted_service_seconds':outcome['seconds']-outcome.get('admission_wait_seconds',0)})
        if group_tokens!=tokens:
            raise ValueError('Group ledger mismatch')
    if prefix+incremental!=total:
        raise ValueError('Batch prefix accounting mismatch')
    return {'manifest':manifest,'physical_calls':len(calls),'charged_tokens':total,
            'unknown_usage_calls':sum(c.get('tokens') is None for c in calls),
            'provider_errors':sum(bool(c.get('error')) for c in calls),'rows':rows,
            'source_hashes':{str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
                             for p in sorted(root.rglob('*.json'))},
            'limitations':manifest['limitations']+['Native success does not establish causal evidence support.']}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('root',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    report=summarize(args.root)
    with args.output.open('x') as handle:
        json.dump(report,handle,indent=2)
    print(json.dumps({k:v for k,v in report.items() if k not in ('source_hashes','manifest')},indent=2))
