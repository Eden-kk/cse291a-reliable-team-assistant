"""Fixed three-arm development coverage trial; no oracle in inference."""
import argparse
import asyncio
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
from team_assistant.runtime import Runtime
from run_phase2_multigroup import execute, provider, save, write
ROOT=Path(__file__).resolve().parents[1]


async def main(args):
    if args.output.exists():raise ValueError('Refuse overwrite')
    if subprocess.check_output(['git','-C',str(ROOT),'status','--porcelain'],text=True).strip():raise ValueError('Commit first')
    spec=json.loads((ROOT/'configs/phase2/evidence-coverage-v1.json').read_text())
    registry=json.loads((ROOT/'configs/splits.json').read_text())
    if spec['case']!='boutique/service/1' or spec['case'] not in registry['splits']['development_seen']:raise ValueError('Fixed dev case only')
    if spec['call_reservation'] != 60000:raise ValueError('Runner requires fixed 60000 reservation')
    required=sum(2+a['diagnosis_rounds'] for a in spec['arms'])*spec['call_reservation']
    if spec['physical_token_cap']<required or any((2+a['diagnosis_rounds'])*spec['call_reservation']>spec['logical_token_cap_per_arm'] for a in spec['arms']):raise ValueError('Incomplete budget coverage')
    commit=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip()
    manifest={**spec,'code_commit':commit,'dirty':False,'dataset_commit':registry['dataset_commit'],'split':'development_seen',
              'model':'gpt-6-astra','reasoning_effort':'low','context_window':131072}
    write(args.output/'manifest.json',manifest)
    physical=Budget(spec['physical_token_cap']);outcomes={}
    for arm in spec['arms']:
        path=args.output/arm['name']
        if physical.halted:
            outcomes[arm['name']]={'state':'skipped','reason':'Physical budget halted'}
        else:
            memory=PolicyMemory();policy={'rules':[]}
            memory.promote('oncall','baseline',policy,{'accepted':True,'sha256':digest(policy)})
            runtime=Runtime(memory,Dispatcher(Budget(spec['logical_token_cap_per_arm'])))
            inv=Investigation(runtime,provider(path,physical),Snapshot(args.vendor,spec['case'],registry,commit),
                max_tools=spec['max_tools'],diagnosis_rounds=arm['diagnosis_rounds'],collection_mode=arm['collection_mode'])
            write(path/'manifest.json',{**manifest,**arm})
            outcomes[arm['name']]=await execute(path,inv,False,time.monotonic())
            await runtime.dispatcher.join();save(path,inv)
            outcomes[arm['name']]['tokens']=runtime.dispatcher.budget.spent
            if physical.reserved or runtime.dispatcher.budget.reserved:raise AssertionError('Unsettled budget')
        write(args.output/'summary.json',{'outcomes':outcomes,'physical_tokens':physical.spent,
              'physical_halted':physical.halted,'settlements':physical.settlements})
    print(json.dumps({'tokens':physical.spent,'outcomes':outcomes}))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--vendor',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    asyncio.run(main(p.parse_args()))
