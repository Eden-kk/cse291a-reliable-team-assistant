"""Bounded three-incident live admission trial; no scoring inside inference."""
import argparse
import asyncio
from contextlib import nullcontext
import json
from pathlib import Path
import subprocess
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from team_assistant.admission import WorkflowAdmission
from team_assistant.cloudops import Snapshot
from team_assistant.dispatch import Budget, Dispatcher
from team_assistant.investigation import Investigation
from team_assistant.memory import PolicyMemory, digest
from team_assistant.runtime import Runtime
from run_phase2_multigroup import build, execute, provider, save, write
from run_phase2_revision import recovery_branch, attempts

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / 'configs/phase2/admission-live-v1.json'


async def window(path, base, physical, policy, manifest, make_foreground):
    inv = recovery_branch(base, provider(path / 'recovery', physical), manifest['logical_token_cap'])
    dispatcher = Dispatcher(inv.runtime.dispatcher.budget, concurrency=2, policy='fair',
                            foreground_reservation=60000)
    inv.runtime.dispatcher = dispatcher
    gate = WorkflowAdmission(1)
    groups = {'recovery': inv}
    for spec in manifest['foregrounds']:
        groups[spec['group']] = make_foreground(path / spec['group'], inv.runtime, spec)
    prefix = base.runtime.dispatcher.budget.spent
    win_manifest = {**manifest, 'scheduler': policy, 'logical_prefix_tokens': prefix,
                    'logical_prefix': '../historical-prefix'}
    write(path / 'manifest.json', win_manifest)
    for name in groups:
        case = manifest['recovery_case'] if name == 'recovery' else next(
            s['case'] for s in manifest['foregrounds'] if s['group'] == name)
        write(path / name / 'manifest.json', {**win_manifest, 'case': case})
    before = physical.spent
    origin = time.monotonic()
    inv.deadline = origin + 60
    arrivals = [{'group': 'recovery', 'intended': origin, 'actual': origin}]

    async def foreground(spec):
        await asyncio.sleep(max(0, origin + spec['arrival_seconds'] - time.monotonic()))
        fresh = groups[spec['group']]
        arrived = time.monotonic()
        arrivals.append({'group': spec['group'], 'intended': origin + spec['arrival_seconds'], 'actual': arrived})
        fresh.deadline = arrived + 30
        context = gate.workflow(fresh.identifier) if policy == 'fair-admit-one' else nullcontext()
        async with context:
            # Deferred work neither queries snapshots nor claims dispatcher headroom.
            with dispatcher.response_workflow(fresh.identifier, calls=5):
                result = await execute(path / spec['group'], fresh, False, arrived)
                result['admission_wait_seconds'] = next((e['arrival_wait'] for e in gate.events
                    if e['event'] == 'admitted' and e['group'] == fresh.identifier), 0)
                return result

    outcomes = await asyncio.gather(execute(path / 'recovery', inv, True, origin),
                                    *(foreground(s) for s in manifest['foregrounds']))
    await dispatcher.join()
    for name, investigation in groups.items():
        save(path / name, investigation)
        result_path = path / name / 'result.json'
        if result_path.exists():
            result = json.loads(result_path.read_text())
            result.update(budget={'spent': dispatcher.budget.spent, 'halted': dispatcher.budget.halted},
                          runtime_events=inv.runtime.events, dispatch_events=dispatcher.events)
            write(result_path, result)
    if (gate.active or gate.pending or dispatcher.foreground_workflows or dispatcher.budget.reserved
            or dispatcher.pending or dispatcher.running or physical.reserved):
        raise AssertionError('Workflow or reservation leak')
    charged = sum(s['charged'] for s in dispatcher.budget.settlements)
    if dispatcher.budget.spent != prefix + charged or physical.spent - before != charged:
        raise AssertionError('Logical/physical ledger mismatch')
    summary = {'outcomes': dict(zip(groups, outcomes)), 'arrivals': arrivals,
               'admission_events': gate.events, 'dispatch_events': dispatcher.events,
               'prefix_tokens': prefix, 'logical_total_tokens': dispatcher.budget.spent,
               'incremental_physical_tokens': physical.spent - before,
               'settlements': dispatcher.budget.settlements,
               'physical_halted': physical.halted, 'logical_budget_halted': dispatcher.budget.halted,
               'tool_attempts': {name: attempts(i) for name,i in groups.items()},
               'lifecycle_drained': True}
    write(path / 'summary.json', summary)
    return summary


async def main(args):
    if args.output.exists():
        raise ValueError('Refuse overwrite')
    if subprocess.check_output(['git','-C',str(ROOT),'status','--porcelain'], text=True).strip():
        raise ValueError('Commit before inference')
    protocol = json.loads(PROTOCOL.read_text())
    registry = json.loads((ROOT / 'configs/splits.json').read_text())
    cases = [protocol['recovery_case']] + [s['case'] for s in protocol['foregrounds']]
    if any(case not in registry['splits']['development_seen'] for case in cases):
        raise ValueError('Development cases only')
    if protocol['policies'] != ['fair-reserve', 'fair-admit-one']:
        raise ValueError('Unexpected policy order')
    if protocol['foregrounds'] != [
            {'group':'service','case':'boutique/service/1','arrival_seconds':0},
            {'group':'runtime','case':'boutique/runtime/1','arrival_seconds':0}]:
        raise ValueError('Retest changed arrivals or cases')
    fixed = {'logical_token_cap':360000, 'physical_token_cap':600000,
             'call_reservation':60000, 'concurrency':2, 'foreground_call_bound':5}
    if any(protocol[k] != v for k,v in fixed.items()):
        raise ValueError('Retest runner before changing protocol constants')
    commit = subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'], text=True).strip()
    manifest = {**protocol, 'code_commit':commit, 'dataset_commit':registry['dataset_commit'], 'model':'gpt-6-astra'}
    write(args.output / 'manifest.json', manifest)
    physical = Budget(protocol['physical_token_cap'])
    memory = PolicyMemory()
    rules = {'rules':[]}
    memory.promote('oncall','v1',rules,{'accepted':True,'sha256':digest(rules)})
    runtime = Runtime(memory, Dispatcher(Budget(protocol['logical_token_cap'])))
    base = build(args.output / 'historical-prefix', physical, runtime, args.vendor, registry,
                 commit, protocol['recovery_case'], 'recovery', True)
    write(args.output / 'historical-prefix/manifest.json', {**manifest,'case':protocol['recovery_case']})
    result = {'prefix':await execute(args.output / 'historical-prefix',base,False,time.monotonic()), 'windows':{}}
    await runtime.dispatcher.join()

    def checkpoint():
        write(args.output / 'summary.json', {**result,'physical_tokens':physical.spent,
             'physical_halted':physical.halted,'physical_settlements':physical.settlements})

    checkpoint()
    if result['prefix']['state'] != 'completed' or not base.originals or physical.halted:
        result['stop_reason'] = 'Prefix failed, fixed exposure absent, or unknown usage; no retry'
        checkpoint()
        return
    invalid = base.advance()
    write(args.output / 'transition.json', {'invalid':invalid,'events':base.revision_events})
    save(args.output / 'advanced-prefix',base)
    def fresh(path, shared, spec):
        snapshot = Snapshot(args.vendor,spec['case'],registry,commit)
        return Investigation(shared,provider(path,physical),snapshot,identifier=spec['group'],max_tools=24)
    for policy in protocol['policies']:
        if physical.halted or not physical.affordable(60000):
            result['windows'][policy]={'state':'skipped','reason':'Physical budget unavailable'}
        else:
            result['windows'][policy]=await window(args.output / policy,base,physical,policy,manifest,fresh)
        checkpoint()
    print(json.dumps({'physical_tokens':physical.spent,'halted':physical.halted}))


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--vendor',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    asyncio.run(main(parser.parse_args()))
