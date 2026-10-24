"""Prepare six label-free diagnosis replays from two explicitly seen archives."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from team_assistant.cloudops import Snapshot
from team_assistant.generic_faults import inject_numeric_port
from team_assistant.propagation import verify_inputs
from team_assistant.state import ExecutionStore, Result
ROOT=Path(__file__).resolve().parents[1]
SOURCES=[('boutique/runtime/1','recovery-boundaries-20261024/handoff-expiry/prefix'),
         ('boutique/performance/1','cross-case-recovery-20261024/performance/prefix')]

def canonical(value): return json.dumps(value,sort_keys=True,separators=(',',':'))
def sha(value): return hashlib.sha256(canonical(value).encode()).hexdigest()

def prepare():
    registry=json.loads((ROOT/'configs/splits.json').read_text()); assert not registry['final_unlocked']
    rows=[]; audits=[]
    for case,path in SOURCES:
        assert case in registry['splits']['development_seen']
        base=ROOT/'runs'/path
        saved=json.loads((base/'calls/003.request.json').read_text())
        archive=json.loads((base/'result.json').read_text())['observation_archive']
        request=copy.deepcopy(saved['request'])
        store=ExecutionStore()
        for role,payload in request['inputs'].items():
            normalized=[]
            for obs in payload['observations']:
                matches=[a for a in archive if a['tool']==obs['tool'] and a['arguments']==obs['arguments']]
                assert len(matches)==1, (case,obs['id'])
                normalized.append(Snapshot.visible(matches[0]))
            payload['observations']=normalized
            store.commit(Result(role,1,copy.deepcopy(payload)),0)
        inv=SimpleNamespace(incident=SimpleNamespace(store=store),snapshot=SimpleNamespace(
            observations=copy.deepcopy(archive),source_version='archived',fingerprint=lambda:'archived'))
        clean=copy.deepcopy(request)
        fault=inject_numeric_port(inv); assert fault['injected']
        request['inputs']['configuration_evidence']=copy.deepcopy(store.results['configuration_evidence'].payload)
        corrupt=copy.deepcopy(request)
        verify_inputs(inv,['configuration_evidence'])
        request['inputs']['configuration_evidence']=copy.deepcopy(store.results['configuration_evidence'].payload)
        repaired=copy.deepcopy(request)
        assert canonical(clean)==canonical(repaired)
        audits.append({'case':case,'source':path,'fault':fault,'clean_hash':sha(clean),'repaired_hash':sha(repaired),
                       'normalization':'all role observations matched by unique tool/arguments to pristine archive'})
        # Order differs across cases, fixed before seeing any response.
        arms=[('clean',clean),('corrupt',corrupt),('repaired',repaired)]
        if case.endswith('performance/1'): arms=[arms[1],arms[2],arms[0]]
        for arm,value in arms:
            messages=[{'role':'system','content':'No external tools. Use supplied observations only. Return a JSON object.\n'+saved['instruction']},
                      {'role':'user','content':canonical(value)}]
            bound=len(canonical(messages).encode())+512
            assert bound<=57952
            rows.append({'case':case,'arm':arm,'request':value,'messages':messages,'input_byte_bound':bound})
    return {'protocol':'triton-diagnosis-calibration-v1-20261024','scope':'seen-data diagnosis-stage replay, not end-to-end recovery or formal evaluation',
            'model':'api-glm-5.3','temperature':0,'max_tokens':2048,'reasoning':'route default; record truncation, do not retry',
            'maximum_calls':6,'reservation_tokens':360000,'maximum_usd':0.46,'automatic_retries':0,'timeout_seconds':180,
            'audits':audits,'rows':rows}
if __name__=='__main__':
    target=ROOT/'runs/triton-calibration-20261024'; target.mkdir(exist_ok=False)
    (target/'input.json').write_text(json.dumps(prepare(),indent=2)+'\n')
