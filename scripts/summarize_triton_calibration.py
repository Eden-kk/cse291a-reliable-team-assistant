"""Verify frozen replay receipts and summarize provider accounting, without inference."""
import hashlib
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def summarize():
    batches=[]
    for name in ('triton-calibration-20261024','triton-calibration-v2-20261024'):
        base=ROOT/'runs'/name
        config=json.loads((base/'input.json').read_text())
        receipt=json.loads((base/'remote/results/summary.json').read_text())
        assert hashlib.sha256((base/'input.json').read_bytes()).hexdigest()==receipt['input_sha256']
        assert (base/'input.json').read_bytes()==(base/'remote/input.json').read_bytes()
        assert not receipt['formal_evaluation']
        rows=[]
        for i,c in enumerate(receipt['calls']):
            assert (c['case'],c['arm'])==(config['rows'][i]['case'],config['rows'][i]['arm'])
            body={'model':config['model'],'messages':config['rows'][i]['messages'],'max_tokens':config['max_tokens'],'temperature':0,'stream':False}
            if config.get('reasoning_effort'):body['reasoning_effort']=config['reasoning_effort']
            assert hashlib.sha256(json.dumps(body,separators=(',',':')).encode()).hexdigest()==c['request_sha256']
            assert c==json.loads((base/'remote/results'/f'{i+1:03d}.json').read_text())
            u=c.get('response',{}).get('usage',{})
            rows.append({k:c.get(k) for k in ('call','case','arm','tokens','modeled_usd','seconds','finish_reason','parse_error','error_type','unknown_usage_reserved_tokens') }|{'usage':u,'response_cost_header':c.get('response_cost_header'),'answer_status':c.get('answer',{}).get('status')})
        batches.append({'batch':name,'scheduled':6,'completed_transport':len(rows),'not_run':receipt['not_run'],'rows':rows,
                        'tokens':sum(r['tokens'] or 0 for r in rows),'public_uncached_usd':sum(r['modeled_usd'] or 0 for r in rows),
                        'response_header_usd':sum(float(r['response_cost_header'] or 0) for r in rows),
                        'initial_usage':receipt['initial_usage'],'final_usage':receipt.get('final_usage'),
                        'valid_json_objects':sum(not r['parse_error'] and not r['error_type'] for r in rows)})
    return {'scope':'two seen cases, diagnosis replay only; not formal evaluation','batches':batches,
            'known_api_tokens':sum(b['tokens'] for b in batches),'response_header_usd':sum(b['response_header_usd'] for b in batches),
            'public_uncached_usd':sum(b['public_uncached_usd'] for b in batches),'formal_started':False}
if __name__=='__main__':
    result=summarize();print(json.dumps(result,indent=2))
