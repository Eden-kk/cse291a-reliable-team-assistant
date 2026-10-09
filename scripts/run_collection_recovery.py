"""Controlled expiry/recompute matrix on a fixed snapshot; no model provider."""
import argparse
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from team_assistant.cloudops import Snapshot
from team_assistant.dispatch import Budget, Dispatcher, Outcome
from team_assistant.investigation import Investigation
from team_assistant.memory import PolicyMemory, digest
from team_assistant.runtime import Runtime
ROOT=Path(__file__).resolve().parents[1]


class ControlledProvider:
    def __init__(self): self.records=[]
    async def complete(self,instruction,request):
        await asyncio.sleep(0)
        if request['step']=='telemetry_plan':
            value={'queries':[{'tool':'GetErrorLogs','arguments':{'namespace':'boutique','service_name':'frontend'}}]}
        elif request['step']=='configuration_plan':
            value={'queries':[{'tool':'GetAppYAML','arguments':{'app_name':'cartservice'}}]}
        else:value={'status':'uncertain','reasoning':'Controlled fixture; no semantic assessment'}
        self.records.append({'request':request,'response':value,'simulated_tokens':10})
        return Outcome(value,10)


async def condition(vendor,mode,expired,recovery,commit):
    registry=json.loads((ROOT/'configs/splits.json').read_text())
    case='boutique/service/1'
    if case not in registry['splits']['development_seen']:raise ValueError('Development case only')
    policy={'rules':[]};memory=PolicyMemory()
    memory.promote('oncall','baseline',policy,{'accepted':True,'sha256':digest(policy)})
    runtime=Runtime(memory,Dispatcher(Budget(600000)))
    provider=ControlledProvider()
    snapshot=Snapshot(vendor,case,registry,commit)
    inv=Investigation(runtime,provider,snapshot,max_tools=24,diagnosis_rounds=1,collection_mode=mode)
    await inv.run();await runtime.dispatcher.join()
    store=inv.incident.store
    before={k:asdict(v) for k,v in store.results.items()}
    invalid=sorted(store.invalidate([expired]))
    if recovery=='restart':store.invalidate(list(store.results))
    calls_before=len(provider.records);tools_before=len(snapshot.observations)
    # collect directly preserves the cumulative tool allowance across both phases.
    await inv.collect(reuse=recovery=='selective');await inv.diagnose();await runtime.dispatcher.join()
    after={k:asdict(v) for k,v in store.results.items()}
    recovery_calls=provider.records[calls_before:]
    if store.invalid() or runtime.dispatcher.budget.reserved:raise AssertionError('Incomplete recovery')
    if not runtime.dispatcher.budget.spent == sum(x['charged'] for x in runtime.dispatcher.budget.settlements) == 10*len(provider.records):
        raise AssertionError('Accounting mismatch')
    return {'mode':mode,'expired':expired,'recovery':recovery,'invalidated_by_expiry':invalid,
            'prefix_calls':calls_before,'recovery_calls':len(recovery_calls),
            'recovery_call_steps':[c['request']['step'] for c in recovery_calls],
            'prefix_tools':tools_before,'recovery_tools':len(snapshot.observations)-tools_before,
            'simulated_recovery_tokens':10*len(recovery_calls),
            'before':before,'after':after,'requests':provider.records,
            'runtime_events':runtime.events,'settlements':runtime.dispatcher.budget.settlements,
            'kind':'controlled expiry on unchanged snapshot; predetermined queries and simulated usage'}


async def main(args):
    if args.output.exists():raise ValueError('Refuse overwrite')
    if subprocess.check_output(['git','-C',str(ROOT),'status','--porcelain'],text=True).strip():raise ValueError('Commit first')
    commit=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip()
    args.output.mkdir(parents=True)
    manifest={'code_commit':commit,'dataset_commit':json.loads((ROOT/'configs/splits.json').read_text())['dataset_commit'],'case':'boutique/service/1','real_model_calls':0,
              'backend':'controlled','intervention':'artifact expiry, unchanged snapshot; no fabricated fault evidence'}
    (args.output/'manifest.json').write_text(json.dumps(manifest,indent=2))
    rows=[]
    for mode in ('parallel','telemetry_first'):
        for expired in ('telemetry_evidence','configuration_evidence','initial'):
            for recovery in ('selective','restart'):
                result=await condition(args.vendor,mode,expired,recovery,commit)
                name=f'{mode}-{expired}-{recovery}'
                (args.output/(name+'.json')).write_text(json.dumps(result,indent=2,default=list))
                rows.append({k:v for k,v in result.items() if k not in ('before','after','requests','runtime_events','settlements')})
    (args.output/'summary.json').write_text(json.dumps(rows,indent=2))
    print(json.dumps({'conditions':len(rows),'real_model_calls':0}))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--vendor',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    asyncio.run(main(p.parse_args()))
