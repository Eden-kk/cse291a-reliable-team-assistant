"""Same-prefix recovery comparison; foreground scheduling is held constant."""
import argparse
import asyncio
import copy
import json
from pathlib import Path
import subprocess
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from team_assistant.cloudops import Snapshot
from team_assistant.dispatch import Budget, Dispatcher
from team_assistant.investigation import Investigation
from team_assistant.memory import PolicyMemory, digest
from team_assistant.revision_investigation import RevisionInvestigation
from team_assistant.runtime import Runtime
from run_phase2_multigroup import execute, provider, save, write
from run_phase2_revision import recovery_branch, attempts
from run_e5_live_smoke import publish_status
ROOT=Path(__file__).resolve().parents[1]


def validate(spec,registry):
    expected={'recovery_case':'boutique/runtime/1','foreground_case':'boutique/service/1',
              'order':['restart','selective'],'diagnosis_rounds':1,'call_reservation':60000,
              'physical_token_cap':900000,'logical_window_cap':540000,'worst_case_calls':15,
              'concurrency':2,'max_tools_per_group_including_prefix':24,'foreground_guard':True,
              'fault_scope':'query-fields-v2','foreground_arrival_seconds':0}
    if any(spec.get(k)!=v for k,v in expected.items()):raise ValueError('Changed fixed protocol')
    if any(spec[k] not in registry['splits']['development_seen'] for k in ('recovery_case','foreground_case')):
        raise ValueError('Development only')


async def window(root,base,physical,method,args,manifest,registry):
    inv=recovery_branch(base,provider(root/'recovery',physical),540000)
    dispatcher=Dispatcher(inv.runtime.dispatcher.budget,concurrency=2,foreground_reservation=60000)
    inv.runtime.dispatcher=dispatcher
    history=copy.deepcopy(inv.incident.store.ledger)
    if method=='restart':inv.incident.store.invalidate(list(inv.incident.store.results))
    fresh=Investigation(inv.runtime,provider(root/'foreground',physical),
        Snapshot(args.vendor,manifest['foreground_case'],registry,manifest['code_commit']),
        identifier='foreground',max_tools=24,diagnosis_rounds=1)
    before=physical.spent
    prefix=base.runtime.dispatcher.budget.spent
    write(root/'manifest.json',{**manifest,'method':method,'logical_prefix_tokens':prefix})
    for name,case in [('recovery',manifest['recovery_case']),('foreground',manifest['foreground_case'])]:
        write(root/name/'manifest.json',{**manifest,'method':method,'case':case,'logical_prefix_tokens':prefix})
    origin=time.monotonic()
    with dispatcher.response_workflow('foreground',calls=3):
        outcomes=await asyncio.gather(execute(root/'recovery',inv,method=='selective',origin),
                                      execute(root/'foreground',fresh,False,origin))
    await dispatcher.join()
    for name,obj,outcome in zip(('recovery','foreground'),(inv,fresh),outcomes):
        if outcome['state']=='completed':publish_status(obj,root/name,1 if name=='recovery' else None)
        save(root/name,obj)
    if (physical.reserved or dispatcher.budget.reserved or dispatcher.pending or dispatcher.running
        or dispatcher.foreground_workflows or inv.incident.store.ledger[:1]!=history
        or dispatcher.budget.spent!=prefix+physical.spent-before):
        raise AssertionError('Branch accounting or publication isolation failed')
    result={'method':method,'outcomes':dict(zip(('recovery','foreground'),outcomes)),
            'physical_incremental_tokens':physical.spent-before,'logical_prefix_tokens':prefix,
            'logical_total_tokens':dispatcher.budget.spent,'settlements':dispatcher.budget.settlements,
            'tool_attempts':{'recovery':attempts(inv),'foreground':attempts(fresh)},
            'dispatch_events':dispatcher.events,'runtime_events':inv.runtime.events,
            'ledgers':{k:v.store.ledger for k,v in inv.runtime.incidents.items()}}
    write(root/'summary.json',result)
    return result


async def main(args):
    if args.output.exists():raise ValueError('Refuse overwrite')
    if subprocess.check_output(['git','-C',str(ROOT),'status','--porcelain'],text=True).strip():
        raise ValueError('Commit before inference')
    spec=json.loads((ROOT/'configs/phase2/e5-recovery-pair-v1.json').read_text())
    registry=json.loads((ROOT/'configs/splits.json').read_text());validate(spec,registry)
    commit=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip()
    manifest={**spec,'code_commit':commit,'dirty':False,'dataset_commit':registry['dataset_commit'],
              'model':'gpt-6-astra','reasoning_effort':'low','context_window':131072,'split':'development_seen'}
    write(args.output/'manifest.json',manifest)
    physical=Budget(900000);memory=PolicyMemory();policy={'rules':[]}
    memory.promote('oncall','baseline',policy,{'accepted':True,'sha256':digest(policy)})
    base=RevisionInvestigation(Runtime(memory,Dispatcher(Budget(540000))),provider(args.output/'prefix',physical),
        Snapshot(args.vendor,spec['recovery_case'],registry,commit),identifier='recovery',
        case=spec['recovery_case'],fault_scope=spec['fault_scope'],max_tools=24,diagnosis_rounds=1)
    base.kind='recovery'
    write(args.output/'prefix/manifest.json',{**manifest,'case':spec['recovery_case']})
    outcomes={'prefix':await execute(args.output/'prefix',base,False,time.monotonic()),'windows':{}}
    await base.runtime.dispatcher.join();save(args.output/'prefix',base)
    def checkpoint():write(args.output/'summary.json',{**outcomes,'physical_tokens':physical.spent,
        'physical_halted':physical.halted,'settlements':physical.settlements})
    checkpoint()
    if outcomes['prefix']['state']!='completed' or not base.originals or physical.halted:return
    publish_status(base,args.output/'prefix')
    invalid=base.advance()
    if 'public-status' not in invalid:raise AssertionError('No downstream exposure')
    save(args.output/'advanced-prefix',base)
    write(args.output/'transition.json',{'invalid':invalid})
    frozen_store=copy.deepcopy(base.incident.store.__dict__)
    frozen_sources=copy.deepcopy(base.revisions.__dict__)
    for method in spec['order']:
        if physical.halted:
            outcomes['windows'][method]={'state':'skipped','reason':'Unknown/overrun usage'}
        else:
            outcomes['windows'][method]=await window(args.output/method,base,physical,method,args,manifest,registry)
        if base.incident.store.__dict__!=frozen_store or base.revisions.__dict__!=frozen_sources:
            raise AssertionError('Window mutated shared prefix')
        checkpoint()
    print(json.dumps({'physical_tokens':physical.spent,'halted':physical.halted}))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--vendor',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    asyncio.run(main(parser.parse_args()))
