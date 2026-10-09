"""Frozen paired validation; experimental policy use is not production promotion."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from team_assistant.cloudops import Snapshot
from team_assistant.dispatch import Budget,Dispatcher
from team_assistant.investigation import Investigation
from team_assistant.memory import PolicyMemory,digest
from team_assistant.runtime import Runtime
from run_phase2_multigroup import execute,provider,save,write

ROOT=Path(__file__).resolve().parents[1]
BUNDLE=ROOT/'configs/learning/bundle-v1'


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_freeze(bundle,registry_path,threshold_path):
    frozen=json.loads((bundle/'freeze.json').read_text())
    registry=json.loads(registry_path.read_text())
    for name,expected in frozen['files'].items():
        if sha(bundle/name)!=expected:raise ValueError('Frozen policy changed')
    if sha(registry_path)!=frozen['registry_sha256'] or sha(threshold_path)!=frozen['threshold_sha256']:
        raise ValueError('Registry or thresholds changed')
    cases=[row['case'] for row in frozen['cases']]
    if cases!=registry['splits']['gate_validation'] or len(set(cases))!=6:
        raise ValueError('Exactly the six frozen validation cases required')
    if any(sorted(row['order'])!=['baseline','candidate'] for row in frozen['cases']):
        raise ValueError('Each pair must contain both policies once')
    if frozen['planned_physical_token_cap']!=900000 or frozen['logical_token_limit_per_run']!=360000:
        raise ValueError('Retest changed budget settings')
    return frozen,registry


async def main(args):
    if args.output.exists():raise ValueError('Refuse overwrite')
    if subprocess.check_output(['git','-C',str(ROOT),'status','--porcelain'],text=True).strip():
        raise ValueError('Commit before validation')
    frozen,registry=validate_freeze(BUNDLE,ROOT/'configs/splits.json',ROOT/'configs/learning-gate.json')
    for row in frozen['cases']:
        if not (args.vendor/'benchmark'/row['case']/'metadata.json').is_file():
            raise ValueError('Missing predeclared validation snapshot')
    commit=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip()
    settings={'model':'gpt-6-astra','model_alias_not_immutable':True,'reasoning_effort':'low',
              'context_window':131072,'call_reservation':60000,'concurrency':2,'max_tools':24,
              'source_sha256':{name:sha(ROOT/'src/team_assistant'/name) for name in
                ('provider.py','investigation.py','runtime.py','cloudops.py','dispatch.py','memory.py')}}
    manifest={'study':'learning-bundle-v1-validation','code_commit':commit,'frozen':frozen,
              'execution_settings':settings,'execution_settings_sha256':digest(settings),
              'dataset_commit':registry['dataset_commit'],'promotion':'none; experimental paired evaluation'}
    write(args.output/'manifest.json',manifest)
    physical=Budget(900000)
    outcomes={}
    policies={'baseline':json.loads((BUNDLE/'baseline.json').read_text()),
              'candidate':json.loads((BUNDLE/'candidate-bundle.json').read_text())}
    def checkpoint():
        write(args.output/'summary.json',{'outcomes':outcomes,'physical_tokens':physical.spent,
              'physical_halted':physical.halted,'physical_settlements':physical.settlements})
    for index,row in enumerate(frozen['cases'],1):
        for label in row['order']:
            key=f'pair-{index}/{label}'
            if physical.halted or not physical.affordable(60000):
                outcomes[key]={'state':'skipped','case':row['case'],'reason':'Physical budget unavailable'}
                checkpoint()
                continue
            path=args.output/key
            memory=PolicyMemory()
            policy=policies[label]
            memory.promote('oncall','trial',policy,{'accepted':True,'sha256':digest(policy)})
            runtime=Runtime(memory,Dispatcher(Budget(360000)))
            snapshot=Snapshot(args.vendor,row['case'],registry,commit)
            inv=Investigation(runtime,provider(path,physical),snapshot,max_tools=24)
            write(path/'manifest.json',{'case':row['case'],'split':'gate_validation','code_commit':commit,
                  'dirty':False,'dataset_commit':registry['dataset_commit'],'policy':policy,
                  'model':'gpt-6-astra','reservation':60000,'token_limit':360000,
                  'execution_settings_sha256':digest(settings),
                  'policy_origin':'isolated experimental trial; not gate-approved/default policy'})
            outcomes[key]=await execute(path,inv,False,time.monotonic())
            await runtime.dispatcher.join()
            save(path,inv)
            outcomes[key].update(case=row['case'],tokens=runtime.dispatcher.budget.spent)
            if runtime.dispatcher.budget.reserved or physical.reserved:
                raise AssertionError('Unsettled reservation')
            checkpoint()
    checkpoint()
    print(json.dumps({'physical_tokens':physical.spent,'halted':physical.halted}))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--vendor',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    asyncio.run(main(parser.parse_args()))
