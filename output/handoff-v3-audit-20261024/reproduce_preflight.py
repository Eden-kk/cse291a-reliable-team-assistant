"""Offline reconstruction only. Network transport is mocked to raise if reached."""
import asyncio
import copy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
ROOT=Path('/Users/yvette/26fall/cse291a-team-assistant')
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'src')]
from run_handoff_v3 import COORDINATE
from semantic_claim_contract import SEMANTIC_CLAIM_CONTRACT
from team_assistant.triton_provider import TritonProvider,BASE_URL
RUN=ROOT/'runs/handoff-v3-20261024/handoff-v3-dataready-results'
OUT=ROOT/'output/handoff-v3-audit-20261024'
read=lambda path:json.loads(path.read_text())
call=read(RUN/'calls/011.json');trace=read(RUN/'repeat-2/clean/trace.json')
request_event=next(t for t in trace if t['event']=='request' and t['step']=='incident_brief')
common={k:v for k,v in call['request'].items() if k not in ('incident','step','inputs','policy')}
request={**common,**request_event['request']}
instruction=COORDINATE+'\nRetrieval exhausted. Return final diagnosis or uncertain; do not request more.'+SEMANTIC_CLAIM_CONTRACT
body={'model':'api-glm-5.3','messages':[{'role':'system','content':'No tools or files. Use supplied evidence only. Return one JSON object.\n'+instruction},{'role':'user','content':json.dumps(request,allow_nan=False)}],'max_tokens':8192,'temperature':0,'reasoning_effort':'low','stream':False,'response_format':{'type':'json_object'}}
encoded=json.dumps(body,separators=(',',':'),allow_nan=False).encode()
usage=read(RUN/'usage.json');calls=usage['calls'];summary=read(RUN/'summary.json')
arm_ids=summary['repeats'][1]['arms']['prefix']['calls']+summary['repeats'][1]['arms']['clean']['calls']
logical_spent=sum(c['tokens'] for c in calls if c['call'] in arm_ids)
source_sizes=[{'id':o['id'],'tool':o['tool'],'arguments':o['arguments'],'raw_text_utf8_bytes':len(o['text'].encode()),'record_json_bytes':len(json.dumps(o).encode())} for o in request['inputs']['retrieved_sources']['observations']]
async def verify():
    with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ',{'OPENAI_BASE_URL':BASE_URL,'OPENAI_API_KEY':'OFFLINE_AUDIT_NOT_A_KEY'}):
        p=TritonProvider(Path(tmp)/'calls',max_calls=36,maximum_usd='2.70',input_byte_cap=40000,json_object=True)
        p.next_id=11;p.spent_usd=Decimal(call['spent_usd'])
        with patch('team_assistant.triton_provider._post',side_effect=AssertionError('Offline audit prohibits network')) as network:
            outcome=await p.complete(instruction,request)
        return {'outcome_error':outcome.error,'outcome_tokens':outcome.tokens,'network_calls':network.call_count,'new_provider_records':len(p.records),'provider_halted':p.halted}
report={'failure':'repeat-2/clean final incident_brief preflight input byte cap',
 'reconstructed_body_bytes':len(encoded),'framing_reserve_bytes':512,'required_bytes':len(encoded)+512,'input_byte_cap':40000,'excess_bytes':len(encoded)+512-40000,
 'body_sha256':hashlib.sha256(encoded).hexdigest(),'request_sha256':hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest(),
 'historical_actual_calls':len(calls),'hypothetical_next_call_id':12,'call_gate_passes':11<36,
 'provider_spent_before_attempt_usd':call['spent_usd'],'next_reservation_usd':'0.074624','reserved_total_usd':str(Decimal(call['spent_usd'])+Decimal('0.074624')),'cap_usd':'2.70',
 'logical_spent_tokens':logical_spent,'logical_reservation_tokens':48192,'logical_limit_tokens':12*66144,'logical_budget_gate_passes':logical_spent+48192<=12*66144,
 'retrieved_sources':source_sizes,'local_reproduction':asyncio.run(verify()),
 'no_rerun_or_repair':True,'historical_batch_unchanged':True,
 'source_file_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [RUN/'calls/011.json',RUN/'repeat-2/clean/trace.json',ROOT/'scripts/run_handoff_v3.py',ROOT/'scripts/semantic_claim_contract.py',ROOT/'src/team_assistant/triton_provider.py']}}
(OUT/'preflight-audit.json').write_text(json.dumps(report,indent=2)+'\n')
(OUT/'reconstructed-final-request.json').write_text(json.dumps({'instruction':instruction,'request':request,'body':body},indent=2)+'\n')
print(json.dumps(report,indent=2))
