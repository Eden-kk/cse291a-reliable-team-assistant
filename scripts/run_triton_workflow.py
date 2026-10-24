"""One frozen, seen-case full recovery workflow; never unlocks formal evaluation."""
import argparse
import asyncio
import copy
from dataclasses import asdict
import json
from pathlib import Path
import sys
import urllib.request
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from team_assistant.cloudops import Snapshot
from team_assistant.dispatch import Budget, Dispatcher
from team_assistant.generic_faults import inject_numeric_port
from team_assistant.investigation import Investigation
from team_assistant.memory import PolicyMemory, digest
from team_assistant.propagation import fork, verify_inputs
from team_assistant.runtime import Runtime
from team_assistant.triton_provider import TritonProvider
ROOT=Path(__file__).resolve().parents[1]
CASE='boutique/runtime/1'

def write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,indent=2,default=list)+'\n')

def account():
    import os
    req=urllib.request.Request('https://tritonai-api.ucsd.edu/key/info',headers={'Authorization':'Bearer '+os.environ['OPENAI_API_KEY']})
    with urllib.request.urlopen(req,timeout=20) as r:d=json.load(r)['info']
    return {k:d.get(k) for k in ('spend','max_budget','expires')}

async def matrix(out,vendor,registry,commit,backend, *, case=CASE, formal=False,
                 order=('restart','selective'), freeze=None, artifact_root=None):
    outcomes={name:{'state':'not_run','calls':[]} for name in ('collection','clean','corrupt','no-recovery','restart','selective','prevention','expiry-restart','expiry-selective')};cap=17*66144
    call_start=len(backend.records)
    def build():
        memory=PolicyMemory();policy={'rules':[]}
        memory.promote('oncall','baseline',policy,{'accepted':True,'sha256':digest(policy)})
        return Investigation(Runtime(memory,Dispatcher(Budget(cap),concurrency=1)),backend,
            Snapshot(vendor,case,registry,commit,freeze,artifact_root),reservation=66144,max_tools=24,diagnosis_rounds=1)
    def clone(inv):
        other=fork(inv,backend);other.runtime.dispatcher=Dispatcher(Budget(cap),concurrency=1)
        other.runtime.dispatcher.budget.spent=inv.runtime.dispatcher.budget.spent
        other.kind='recovery';return other
    async def run(name,inv,operation,supersedes=None):
        path=out/name;start=len(backend.records)
        try:
            result=await operation()
            if result is not None:
                result=copy.deepcopy(result);result['model_calls']=backend.records[start:]
                write(path/'result.json',result)
                parent='diagnosis_0'
                inv.artifact('public-status',{'status':'provisional','quality_approved':False,'answer':result['answer']},(parent,))
                inv.runtime.publish(inv.identifier,'public-status',lambda p:p['quality_approved'] is False,supersedes=supersedes)
            outcome={'state':'completed','contract_errors':result['contract_errors'] if result else [],'quality_approved':False}
        except Exception as exc:
            outcome={'state':'failed','error_type':type(exc).__name__,'quality_approved':False}
        finally:
            await inv.runtime.dispatcher.join()
            outcome.update(calls=list(range(start+1,len(backend.records)+1)),invalid=sorted(inv.incident.store.invalid()))
            outcomes[name]=outcome
            write(path/'state.json',{k:asdict(v) for k,v in inv.incident.store.results.items()})
            write(path/'ledger.json',inv.incident.store.ledger)
            write(path/'manifest.json',{'case':case,'split':'final_locked' if formal else 'development_seen','code_commit':commit,'dataset_commit':registry['dataset_commit']})
            write(out/'summary.json',{'arms':outcomes,'provider_halted':backend.halted,'calls':len(backend.records),'formal_evaluation':formal})
        return outcome['state']=='completed' and not backend.halted
    base=build()
    if not await run('collection',base,base.collect):return
    collection=clone(base)
    if not await run('clean',base,base.diagnose):return
    clean=clone(base)
    contaminated=clone(collection)
    fault=inject_numeric_port(contaminated);write(out/'fault.json',fault)
    if fault['injected']:
        if not await run('corrupt',contaminated,contaminated.diagnose):return
        invalid=contaminated.incident.store.invalidate(['configuration_evidence'])
        try:contaminated.incident.store.publish('public-status',contaminated.incident.store.epoch,supersedes=1)
        except ValueError:rejected=True
        else:raise AssertionError('stale publication accepted')
        write(out/'notification.json',{'invalid':sorted(invalid),'stale_publication_rejected':rejected})
        outcomes['no-recovery']={'state':'invalid','calls':[],'invalid':sorted(invalid)}
        frozen=copy.deepcopy(contaminated.incident.store.__dict__)
        for method in order:
            inv=clone(contaminated)
            async def recover():
                if method=='restart':
                    inv.runtime.restart(inv.identifier,{'reason':'full restart comparator'})
                    return await inv.run()
                write(out/method/'repair.json',verify_inputs(inv,['configuration_evidence']))
                await inv.collect(reuse=True);return await inv.diagnose()
            if not await run(method,inv,recover,supersedes=1):return
            assert contaminated.incident.store.__dict__==frozen
    else:
        for name in ('corrupt','no-recovery','restart','selective'):
            outcomes[name]={'state':'not_injected','calls':[],'reason':fault}
    inv=build()
    async def prevention():
        await inv.collect();f=inject_numeric_port(inv);write(out/'prevention/fault.json',f)
        write(out/'prevention/repair.json',verify_inputs(inv,['initial','configuration_evidence','telemetry_evidence']))
        return await inv.diagnose()
    if not await run('prevention',inv,prevention):return
    clean.incident.store.invalidate(['initial'])
    for method in order:
        inv=clone(clean)
        async def expired():
            if method=='restart':inv.runtime.restart(inv.identifier,{'reason':'all work expired'})
            # Reset per-workflow query allowance equally for both expiry arms.
            inv.tool_budget_start=len(inv.snapshot.observations)+len(inv.tool_errors)
            await inv.collect(reuse=method=='selective');return await inv.diagnose()
        if not await run('expiry-'+method,inv,expired,supersedes=1):return
    assert len(backend.records)-call_start<=17
    assert len(outcomes['expiry-restart']['calls'])==len(outcomes['expiry-selective']['calls'])==3
    outcomes['checks']={'arm_isolation':True,'expiry_stage_counts_equal':True,'generic_fault':fault['injected']}
    write(out/'summary.json',{'arms':outcomes,'calls':len(backend.records),'provider_halted':backend.halted,'formal_evaluation':formal})

async def main(args):
    registry=json.loads((ROOT/'configs/splits.json').read_text())
    assert not registry['final_unlocked'] and CASE in registry['splits']['development_seen']
    spec=json.loads((ROOT/'configs/triton-workflow-dev-v1.json').read_text())
    assert spec['case']==CASE and spec['max_calls']==17 and spec['formal_evaluation'] is False
    args.output.mkdir(parents=True,exist_ok=False)
    before=account();write(args.output/'account-before.json',before)
    assert before['max_budget']-before['spend']>=1.7
    from datetime import datetime, timezone
    assert datetime.fromisoformat(before['expires']) > datetime.now(timezone.utc)
    backend=TritonProvider(args.output/'calls',max_calls=17,maximum_usd='1.7')
    try:await matrix(args.output,args.vendor,registry,args.commit,backend)
    finally:
        write(args.output/'usage.json',{'calls':backend.records,'halted':backend.halted,'conservative_spend_usd':str(backend.spent_usd)})
        try:write(args.output/'account-after.json',account())
        except Exception:write(args.output/'account-after.json',{'unavailable':True})
        print(json.dumps({'calls':len(backend.records),'halted':backend.halted,'formal_evaluation':False}))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--vendor',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--commit',required=True)
    asyncio.run(main(p.parse_args()))
