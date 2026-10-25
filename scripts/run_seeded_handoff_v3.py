"""Separately allocated conditional recovery from an explicitly seeded saved state."""
import argparse
import asyncio
import copy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_causal_summary_dev import build, clone, ROOT
from run_handoff_dev import collect_handoffs, summarize_configuration, coordinate, hashes, RAW_NAMES
from handoff_v3_contract import TIMING_CONTRACT
from run_triton_workflow import account, write
from team_assistant.state import Result
from team_assistant.triton_provider import TritonProvider


class RevisedProvider:
    """Use unchanged runtime/1 workflow with the same revised contract on both arms."""
    def __init__(self, backend): self.backend = backend
    async def complete(self, instruction, request):
        if not request['step'].endswith('_plan'): instruction += TIMING_CONTRACT
        if request['step'] in ('coordinator_decision','incident_brief'):
            instruction += '\nFor a final answer include recommendation: a source-supported action or justified abstention; do not invent operational actions.'
        return await self.backend.complete(instruction, request)


def file_hash(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def restore(vendor, registry, commit, backend, saved, spec):
    for name, expected in spec['saved_hashes'].items():
        if file_hash(saved/name) != expected: raise ValueError('Saved evidence hash mismatch')
    inv = build(vendor, registry, commit, backend); inv.reservation = 48192
    state = json.loads((saved/'state.json').read_text())
    # Reacquire only fixed recorded queries, to prove restored raw equals the immutable snapshot.
    records = {}
    for name in RAW_NAMES:
        for obs in state[name]['payload'].get('observations', []):
            if obs['id'] in records and records[obs['id']] != obs: raise ValueError('Conflicting saved raw')
            records[obs['id']] = obs
    ordered = sorted(records.values(), key=lambda r:int(r['id'].split('-')[-1]))
    if [r['id'] for r in ordered] != [f'observation-{n+1}' for n in range(len(ordered))]:
        raise ValueError('Missing source replay lineage')
    for obs in ordered:
        if inv.snapshot.query(obs['tool'], obs['arguments']) != obs:
            raise ValueError('Raw replay changed; conditional restore prohibited')
    store = inv.incident.store
    store.results = {name: Result(**{**v, 'citations':set(v['citations'])}) for name,v in state.items()}
    store.ledger = json.loads((saved/'ledger.json').read_text())
    if store.invalid() or store.provenance_gaps(): raise ValueError('Saved state not valid/complete')
    result = json.loads((saved/'result.json').read_text())['result']
    inv.runtime.dispatcher.budget.spent = result['logical_tokens_spent']
    inv.tool_budget_start = len(inv.snapshot.observations)
    if store.results['configuration_summary'].payload.get('liveness_protocol') != 'grpc':
        raise ValueError('Fixed prior corrupted categorical state unavailable')
    source = store.results['summary_source'].payload['observations'][0]
    if 'httpGet:' not in source['text'].split('livenessProbe:',1)[1].split('---',1)[0]:
        raise ValueError('Fixed runtime/1 raw premise unavailable')
    return inv


def seed(inv):
    store = inv.incident.store
    original = hashes(inv)
    wrong = {'status':'uncertain', 'adservice_liveness_protocol':'grpc',
             'reasoning':'Both adservice probes use grpc, so the liveness protocol already matches readiness.',
             'recommendation':'Keep the existing livenessProbe unchanged; no protocol correction is needed.',
             'evidence':[]}
    for name in ('coordinator_decision','retrieved_sources','incident_brief'):
        value = copy.deepcopy(store.results[name]); value.version += 1
        if name != 'retrieved_sources': value.payload = copy.deepcopy(wrong)
        value.inputs = {n:store.results[n].version for n in value.inputs}
        store.commit(value,store.epoch)
    publication = store.publish('incident_brief',store.epoch,supersedes=1)
    assert not store.invalid()
    return {'seeded':True, 'organic_model_adoption':False, 'model_calls_for_seeding':0,
            'mutated_artifacts':['coordinator_decision','incident_brief'],
            'rebound_raw_child':'retrieved_sources retains identical observations but has new parent version',
            'wrong_payload':wrong,'seeded_publication_id':publication['id'],
            'logical_tokens_before_recovery':inv.runtime.dispatcher.budget.spent,
            'before_hashes':original,'after_hashes':hashes(inv),
            'interpretation':'Synthetic wrong downstream assertion/recommendation; uncertainty status does not make these assertions supported'}


async def experiment(out,vendor,registry,commit,backend,saved,spec):
    arms={n:{'state':'not_run','calls':[]} for n in ('no-recovery','restart','selective')}
    def summary():
        write(out/'summary.json',{'case':'boutique/runtime/1','track':'seeded conditional, not organic',
              'formal_evaluation':False,'arms':arms,'calls':len(backend.records),'halted':backend.halted})
    summary()
    provider=RevisedProvider(backend)
    inv=restore(vendor,registry,commit,provider,saved,spec)
    write(out/'restore.json',{'saved_hashes':spec['saved_hashes'],'raw_verified_by_offline_requery':True,
          'old_logical_tokens':inv.runtime.dispatcher.budget.spent,'restore_model_calls':0,
          'raw_query_count':len(inv.snapshot.observations)})
    write(out/'seed.json',seed(inv))
    write(out/'seeded-state.json',{k:asdict(v) for k,v in inv.incident.store.results.items()})
    write(out/'seeded-ledger.json',inv.incident.store.ledger)
    start=time.perf_counter(); invalid=inv.incident.store.invalidate(['configuration_summary'])
    event={'artifact':'configuration_summary','reason':'trusted producer invalidity notification',
           'correct_value_supplied':False,'invalidated':sorted(invalid),
           'tracking_wall_seconds':time.perf_counter()-start}
    write(out/'notification.json',event)
    try: inv.incident.store.publish('incident_brief',inv.incident.store.epoch)
    except ValueError: pass
    else: raise AssertionError('Stale seeded publication accepted')
    arms['no-recovery']={'state':'invalid','calls':[],'stale_republication_rejected':True,
                        'answer':copy.deepcopy(inv.incident.store.results['incident_brief'].payload)}
    frozen=copy.deepcopy(inv.incident.store.__dict__)
    for method in ('restart','selective'):
        clock=time.perf_counter(); run=clone(inv,provider); before=hashes(run); first=len(backend.records)
        try:
            if method=='restart':
                run.runtime.restart(run.identifier,event)
                run.tool_budget_start=len(run.snapshot.observations)+len(run.tool_errors)
                await collect_handoffs(run)
            else: await summarize_configuration(run,reuse=True)
            result=await coordinate(run)
            result['recommendation_present']=isinstance(result['answer'].get('recommendation'),str) and bool(result['answer']['recommendation'].strip())
            result['semantic_success']='requires independent claim/action review; protocol field alone is insufficient'
            run.incident.store.publish('incident_brief',run.incident.store.epoch,supersedes=2)
            arms[method]={'state':'completed','result':result}
        except Exception as exc:
            arms[method]={'state':'failed','error_type':type(exc).__name__}
        finally:
            await run.runtime.dispatcher.join()
            arms[method]['calls']=list(range(first+1,len(backend.records)+1))
            write(out/method/'state.json',{k:asdict(v) for k,v in run.incident.store.results.items()})
            write(out/method/'ledger.json',run.incident.store.ledger)
            write(out/method/'trace.json',run.runtime.events)
            after=hashes(run)
            write(out/method/'reuse.json',{'before_hashes':before,'after_hashes':after,
                 'valid_unchanged_artifacts':[n for n in before if before[n]==after.get(n) and n not in run.incident.store.invalid()],
                 'invalid_after':sorted(run.incident.store.invalid())})
            write(out/method/'result.json',arms[method]); summary()
            write(out/method/'clock.json',{'wall_seconds':time.perf_counter()-clock+event['tracking_wall_seconds'],
                 'scope':'shared invalidation+clone+model/tools+validation/publication+evidence persistence; excludes fixed restore and clock write'})
        assert inv.incident.store.__dict__==frozen
        if arms[method]['state']=='failed' or backend.halted: break
    assert len(backend.records)<=9
    summary()


async def main(args):
    spec=json.loads((ROOT/'configs/seeded-handoff-v3.json').read_text())
    assert spec['max_calls']==9 and spec['maximum_usd']=='0.70'
    args.output.mkdir(parents=True,exist_ok=False)
    write(args.output/'protocol.json',spec); write(args.output/'execution.json',{'code_commit':args.commit,'json_object':args.json_object})
    before=account();write(args.output/'account-before.json',before)
    assert before['max_budget']-before['spend']>=0.70
    assert datetime.fromisoformat(before['expires'])>datetime.now(timezone.utc)
    backend=TritonProvider(args.output/'calls',max_calls=9,maximum_usd='0.70',input_byte_cap=40000,json_object=args.json_object)
    try: await experiment(args.output,args.vendor,json.loads((ROOT/'configs/splits.json').read_text()),args.commit,backend,args.saved,spec)
    finally:
        write(args.output/'usage.json',{'calls':backend.records,'halted':backend.halted,'conservative_spend_usd':str(backend.spent_usd)})
        try: write(args.output/'account-after.json',account())
        except Exception: write(args.output/'account-after.json',{'unavailable':True})

if __name__=='__main__':
    p=argparse.ArgumentParser()
    for field in ('vendor','output','saved'): p.add_argument('--'+field,type=Path,required=True)
    p.add_argument('--commit',required=True)
    p.add_argument('--json-object',action='store_true')
    asyncio.run(main(p.parse_args()))
