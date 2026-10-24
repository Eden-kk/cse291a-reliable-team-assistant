"""Post-review native and contract scoring for six seen diagnosis replays."""
import hashlib
import importlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from team_assistant.cloudops import load_vendor
from team_assistant.contracts import check_diagnosis
ROOT=Path(__file__).resolve().parents[1]

def score():
    audit=ROOT/'docs/triton-calibration-20261024-semantic-audit.txt'
    assert audit.exists()
    vendor=ROOT.parent/'preparation/291a-team-agent/vendor/Cloud-OpsBench'
    if not vendor.exists():vendor=ROOT/'vendor_offline/Cloud-OpsBench'
    package=load_vendor(vendor)
    native=importlib.import_module(package+'.evaluation')
    contracts=importlib.import_module(package+'.runtime.contracts')
    objects=({'app/'+n for n in contracts.SYSTEM_VALID_SERVICES['boutique']}|{'node/'+n for n in contracts.VALID_NODES}|{'namespace/boutique'})
    base=ROOT/'runs/triton-calibration-v2-20261024'
    config=json.loads((base/'input.json').read_text());receipt=json.loads((base/'remote/results/summary.json').read_text())
    rows=[]
    for row,call in zip(config['rows'],receipt['calls']):
        answer=call.get('answer',{});request=row['request'];observations={o['id']:o for p in request['inputs'].values() for o in p['observations']}
        errors=check_diagnosis(answer,list(observations.values()),request['allowed_causes'],objects)
        system,category,case=row['case'].split('/');label=vendor/'process-label'/system/category
        truth=native.load_ground_truth(label,case)
        scores=native.prediction_scores([answer] if answer.get('status')=='diagnosed' else [],truth)
        trace=[{'action_type':'tool','action_name':o['tool'],'action_input':o['arguments'],'observation':o['text']} for o in observations.values()]
        scores.update(native.process_scores(label,case,{'steps':trace},disable_getalerts_credit=category=='performance' and scores['CA']<1))
        fault=next(a['fault'] for a in config['audits'] if a['case']==row['case'])
        rows.append({'case':row['case'],'arm':row['arm'],'root_cause':answer.get('root_cause'),'scores':scores,'contract_errors':errors,
                     'changed_observation_cited':any(e.get('observation_id')==fault['observation_id'] for e in answer.get('evidence',[])),
                     'injected_value_in_final_answer':fault['after'] in json.dumps(answer),
                     'source_support_review':'supported decisive facts with minor overstatement' if category=='runtime' else 'causal sufficiency uncertain; broad absence claims',
                     'publication_executed':False})
    return {'review_sha256':hashlib.sha256(audit.read_bytes()).hexdigest(),'review_not_blind':True,'rows':rows,'formal_evaluation':False}
if __name__=='__main__':print(json.dumps(score(),indent=2))
