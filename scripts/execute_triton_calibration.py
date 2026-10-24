"""Run the frozen six-call development replay inside course DataHub; never export keys."""
import hashlib
import json
import os
from pathlib import Path
import time
import urllib.request
import urllib.parse


def main():
    source=Path('input.json'); config=json.loads(source.read_text()); out=Path('results')
    out.mkdir(exist_ok=False)
    assert config['maximum_calls']==len(config['rows'])==6 and config['maximum_usd']<=0.58
    assert {r['case'] for r in config['rows']}=={'boutique/runtime/1','boutique/performance/1'}
    base=os.environ['OPENAI_BASE_URL'].rstrip('/'); key=os.environ['OPENAI_API_KEY']
    assert base=='https://tritonai-api.ucsd.edu/v1'
    headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'}
    def get(path):
        req=urllib.request.Request('https://tritonai-api.ucsd.edu'+path,headers=headers)
        with urllib.request.urlopen(req,timeout=20) as response: return json.load(response)
    def usage():
        d=get('/key/info')['info']
        return {k:d.get(k) for k in ['spend','max_budget','expires','rpm_limit','tpm_limit']}
    initial=usage(); accessible=[x['id'] for x in get('/v1/models')['data']]
    assert config['model'] in accessible
    assert initial['max_budget']-initial['spend']>=config['maximum_usd']
    receipt={'input_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'initial_usage':initial,'model':config['model'],
             'calls':[],'halted':False,'formal_evaluation':False}
    def save(): (out/'summary.json').write_text(json.dumps(receipt,indent=2))
    save()
    for number,row in enumerate(config['rows'],1):
        if receipt['halted']: break
        body={'model':config['model'],'messages':row['messages'],'max_tokens':config['max_tokens'],'temperature':0,'stream':False}
        if config.get('reasoning_effort'): body['reasoning_effort']=config['reasoning_effort']
        assert body['max_tokens'] in (2048,8192)
        encoded=json.dumps(body,separators=(',',':')).encode()
        assert len(encoded)+512<=57952
        record={'call':number,'case':row['case'],'arm':row['arm'],'request_sha256':hashlib.sha256(encoded).hexdigest()}
        started=time.monotonic()
        try:
            req=urllib.request.Request(base+'/chat/completions',data=encoded,headers=headers,method='POST')
            with urllib.request.urlopen(req,timeout=180) as response:
                raw=response.read(); data=json.loads(raw)
                record['response']=data
                record['response_cost_header']=response.headers.get('x-litellm-response-cost')
            u=data.get('usage',{}); pt=u.get('prompt_tokens'); ct=u.get('completion_tokens')
            if type(pt)!=int or type(ct)!=int or pt<0 or ct<0: raise ValueError('unknown_usage')
            record['tokens']=pt+ct; record['modeled_usd']=(pt*1.2+ct*3.25)/1e6
            if pt>57952 or ct>config['max_tokens']: raise ValueError('token_cap_exceeded')
            record['finish_reason']=data['choices'][0].get('finish_reason')
            content=data['choices'][0]['message'].get('content') or ''
            try: record['answer']=json.loads(content)
            except ValueError: record['parse_error']='not_json_object'
            if not isinstance(record.get('answer'),dict): record['parse_error']='not_json_object'
        except Exception as exc:
            # No exception strings/bodies: they may contain server authentication context.
            record['error_type']=type(exc).__name__
            record['unknown_usage_reserved_tokens']=57952+config['max_tokens']
            receipt['halted']=True
        record['seconds']=time.monotonic()-started
        (out/f'{number:03d}.json').write_text(json.dumps(record,indent=2))
        receipt['calls'].append(record); save()
        print(json.dumps({k:record.get(k) for k in ['call','case','arm','tokens','seconds','finish_reason','parse_error','error_type']}),flush=True)
    try: receipt['final_usage']=usage()
    except Exception: receipt['final_usage_unavailable']=True
    receipt['not_run']=[{'case':r['case'],'arm':r['arm']} for r in config['rows'][len(receipt['calls']):]]
    save()
if __name__=='__main__': main()
