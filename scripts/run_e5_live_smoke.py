"""One bounded live E5 development smoke; local publications remain provisional."""
import argparse
import asyncio
import copy
import json
from pathlib import Path
import subprocess
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from team_assistant.cloudops import Snapshot
from team_assistant.dispatch import Budget, Dispatcher
from team_assistant.investigation import Investigation
from team_assistant.memory import PolicyMemory, digest
from team_assistant.runtime import Runtime
from team_assistant.revision_investigation import RevisionInvestigation
from run_phase2_multigroup import execute, provider, save, write

ROOT = Path(__file__).resolve().parents[1]


def preflight(spec, registry, receipt):
    if (spec['recovery_case'], spec['foreground_case']) != ('boutique/runtime/1', 'boutique/service/1'):
        raise ValueError('Fixed development cases required')
    if any(spec[k] not in registry['splits']['development_seen'] for k in ('recovery_case', 'foreground_case')):
        raise ValueError('Development cases only')
    fixed = {'physical_token_cap':900000, 'shared_logical_token_cap':900000, 'call_reservation':60000,
             'max_calls_per_stage':4, 'diagnosis_rounds':2, 'stage_count':3, 'concurrency':2, 'max_tools_per_group':24,
             'fault_scope':'query-fields-v2', 'final_unlocked':False}
    if any(spec.get(k) != v for k,v in fixed.items()) or receipt.get('accepted') is not False:
        raise ValueError('Changed fixed protocol or non-rejected receipt')
    prior = spec.get('preparation_tokens_already_spent', 0)
    if type(prior) is not int or prior < 0:
        raise ValueError('Invalid preparation usage')
    if spec['physical_token_cap'] - prior < spec['stage_count'] * spec['max_calls_per_stage'] * spec['call_reservation']:
        raise ValueError('Incomplete stage coverage')


def publish_status(inv, directory, supersedes=None):
    result = json.loads((directory / 'result.json').read_text())
    store = inv.incident.store
    names = [n for n in store.results if n.startswith('diagnosis_') and n not in store.invalid()]
    if not names:
        raise ValueError('No current diagnosis-stage artifact')
    parent = max(names, key=lambda n: int(n.rsplit('_', 1)[1]))
    inv.artifact('public-status', {'status': 'provisional', 'quality_approved': False,
                                 'answer': result['answer']}, (parent,))
    return inv.runtime.publish(inv.identifier, 'public-status',
        lambda payload: payload.get('quality_approved') is False and payload.get('status') == 'provisional',
        supersedes=supersedes)


async def main(args):
    if args.output.exists(): raise ValueError('Refuse overwrite')
    if subprocess.check_output(['git','-C',str(ROOT),'status','--porcelain'],text=True).strip():
        raise ValueError('Commit before inference')
    spec = json.loads(args.protocol.read_text())
    registry = json.loads((ROOT / 'configs/splits.json').read_text())
    receipt = json.loads((ROOT / spec['rejected_receipt']).read_text())['receipt']
    preflight(spec, registry, receipt)
    commit = subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip()
    memory = PolicyMemory()
    empty = {'rules':[]}
    memory.promote('oncall','baseline',empty,{'accepted':True,'sha256':digest(empty)})
    candidate = json.loads((ROOT / 'configs/learning/bundle-v1/candidate-bundle.json').read_text())
    try: memory.promote('oncall','rejected-candidate',candidate,receipt)
    except ValueError: pass
    else: raise AssertionError('Rejected candidate activated')
    if memory.default != {'oncall':'baseline'}: raise AssertionError('Default changed')
    remaining = spec['physical_token_cap'] - spec.get('preparation_tokens_already_spent', 0)
    physical = Budget(remaining)
    dispatcher = Dispatcher(Budget(900000),concurrency=2,foreground_reservation=60000)
    runtime = Runtime(memory,dispatcher)
    manifest = {**spec,'code_commit':commit,'dirty':False,'dataset_commit':registry['dataset_commit'],
                'model':'gpt-6-astra','reasoning_effort':'low','context_window':131072,
                'receipt_sha256':digest(receipt),'policy':empty,'split':'development_seen'}
    write(args.output / 'manifest.json',manifest)
    inv = RevisionInvestigation(runtime,provider(args.output/'prefix',physical),
          Snapshot(args.vendor,spec['recovery_case'],registry,commit),identifier='recovery',
          case=spec['recovery_case'],fault_scope=spec['fault_scope'],max_tools=24,diagnosis_rounds=spec['diagnosis_rounds'])
    # The historical workflow is not a registered new foreground arrival.
    inv.kind = 'recovery'
    write(args.output/'prefix/manifest.json',{**manifest,'case':spec['recovery_case']})
    outcomes = {'prefix':await execute(args.output/'prefix',inv,False,time.monotonic())}
    await dispatcher.join()
    save(args.output/'prefix',inv)
    def checkpoint():
        write(args.output/'summary.json',{'outcomes':outcomes,'physical_tokens':physical.spent,
              'physical_halted':physical.halted,'settlements':physical.settlements,
              'preparation_tokens_from_terminal':spec.get('preparation_tokens_already_spent',0),
              'physical_tokens_including_preparation':physical.spent+spec.get('preparation_tokens_already_spent',0),
              'rejected_candidate_not_activated':'rejected-candidate' not in memory.entries,
              'runtime_events':runtime.events,'dispatch_events':dispatcher.events,
              'ledgers':{k:v.store.ledger for k,v in runtime.incidents.items()}})
    checkpoint()
    if outcomes['prefix']['state'] != 'completed' or not inv.originals or physical.halted:
        outcomes['stop'] = {'reason':'Failed prefix, absent exposure or unknown usage; no retry'}
        checkpoint(); return
    first = publish_status(inv,args.output/'prefix')
    historical = copy.deepcopy(first)
    invalid = inv.advance()
    blocked = False
    try: runtime.publish(inv.identifier,'public-status',lambda p: True)
    except ValueError: blocked = True
    if not blocked: raise AssertionError('Stale status was publishable')
    write(args.output/'transition.json',{'invalid':invalid,'stale_republish_blocked':blocked})
    inv.provider = provider(args.output/'recovery',physical)
    fresh = Investigation(runtime,provider(args.output/'foreground',physical),
                         Snapshot(args.vendor,spec['foreground_case'],registry,commit),identifier='foreground',max_tools=24,diagnosis_rounds=spec['diagnosis_rounds'])
    for name,case in [('recovery',spec['recovery_case']),('foreground',spec['foreground_case'])]:
        write(args.output/name/'manifest.json',{**manifest,'case':case})
    origin = time.monotonic()
    with dispatcher.response_workflow('foreground',calls=spec['max_calls_per_stage']):
        results = await asyncio.gather(execute(args.output/'recovery',inv,True,origin),
                                       execute(args.output/'foreground',fresh,False,origin))
    await dispatcher.join()
    for name,obj,result in zip(('recovery','foreground'),(inv,fresh),results):
        outcomes[name] = result
        if result['state'] == 'completed':
            publish_status(obj,args.output/name,first['id'] if name == 'recovery' else None)
        save(args.output/name,obj)
    if (physical.reserved or dispatcher.budget.reserved or dispatcher.running or dispatcher.pending
            or dispatcher.foreground_workflows or physical.spent != dispatcher.budget.spent
            or inv.incident.store.ledger[0] != historical):
        raise AssertionError('Unsettled ledger or altered publication')
    checkpoint()
    print(json.dumps({'physical_tokens':physical.spent,'outcomes':outcomes}))


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--protocol',type=Path,default=ROOT/'configs/phase2/e5-live-smoke-v2.json')
    parser.add_argument('--vendor',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    asyncio.run(main(parser.parse_args()))
