"""Collect predeclared learning-only episodes; scoring is a separate later step."""
import argparse
import asyncio
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


def selected_cases(registry):
    cases=[]
    for category in ('runtime','service','performance'):
        matches=[c for c in registry['splits']['learning'] if c.split('/')[1]==category]
        if not matches:
            raise ValueError('Missing learning category')
        cases.append(matches[0])
    if len(set(cases))!=3:
        raise ValueError('Learning episodes must be distinct')
    return cases


async def main(args):
    if args.output.exists():raise ValueError('Refuse overwrite')
    if subprocess.check_output(['git','-C',str(ROOT),'status','--porcelain'],text=True).strip():
        raise ValueError('Commit before inference')
    registry=json.loads((ROOT/'configs/splits.json').read_text())
    cases=selected_cases(registry)
    if any(not (args.vendor/'benchmark'/case/'metadata.json').is_file() for case in cases):
        raise ValueError('Missing predeclared learning snapshot; materialize exact sparse paths first')
    commit=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip()
    manifest={'study':'learning-sources-v1','cases':cases,'code_commit':commit,
              'dataset_commit':registry['dataset_commit'],'physical_token_cap':360000,
              'selection':'first predeclared learning ID per category; no success-seeking retries',
              'final_locked':True}
    write(args.output/'manifest.json',manifest)
    physical=Budget(360000)
    outcomes={}
    for index,case in enumerate(cases,1):
        if physical.halted or not physical.affordable(60000):
            outcomes[case]={'state':'skipped','reason':'Physical budget unavailable'}
        else:
            path=args.output/f'episode-{index}'
            policy={'rules':[]}
            memory=PolicyMemory()
            memory.promote('oncall','baseline',policy,{'accepted':True,'sha256':digest(policy)})
            runtime=Runtime(memory,Dispatcher(Budget(360000)))
            snapshot=Snapshot(args.vendor,case,registry,commit)
            inv=Investigation(runtime,provider(path,physical),snapshot,max_tools=24)
            write(path/'manifest.json',{'case':case,'split':'learning','code_commit':commit,'dirty':False,
                  'dataset_commit':registry['dataset_commit'],'policy':policy,'model':'gpt-6-astra',
                  'reservation':60000,'token_limit':360000,'policy_origin':'fixed empty baseline'})
            outcomes[case]=await execute(path,inv,False,time.monotonic())
            await runtime.dispatcher.join()
            save(path,inv)
        write(args.output/'summary.json',{'outcomes':outcomes,'physical_tokens':physical.spent,
              'physical_halted':physical.halted,'physical_settlements':physical.settlements})
    print(json.dumps({'physical_tokens':physical.spent,'halted':physical.halted}))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--vendor',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    asyncio.run(main(parser.parse_args()))
