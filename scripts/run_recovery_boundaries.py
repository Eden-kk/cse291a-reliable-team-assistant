"""Frozen development handoff-expiry and intermediate-corruption semantic pilot."""
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
from team_assistant.propagation import corrupt_port, fork, verify_inputs
from team_assistant.runtime import Runtime
from run_e5_live_smoke import publish_status
from run_phase2_multigroup import provider, save, write
from run_phase2_revision import attempts

ROOT = Path(__file__).resolve().parents[1]
CASE = 'boutique/runtime/1'
EXPECTED = {
    'all-expiry': {'initial', 'telemetry_plan', 'telemetry_evidence', 'configuration_plan',
                   'configuration_evidence', 'diagnosis_0', 'public-status'},
    'handoff-expiry': {'telemetry_evidence', 'configuration_plan', 'configuration_evidence',
                       'diagnosis_0', 'public-status'},
    'wrong-content': {'configuration_evidence', 'diagnosis_0', 'public-status'}}


def clone(base, backend):
    inv = fork(base, backend)
    inv.runtime.dispatcher.budget.spent = base.runtime.dispatcher.budget.spent
    inv.kind = 'recovery'
    return inv


def source_audit(inv, result):
    pristine = {o['id']: Snapshot.visible(o) for o in inv.snapshot.observations}
    return [{'id': o['id'], 'matches_archive': o == pristine.get(o['id'])}
            for o in result['observations']]


async def execute(path, inv, physical, operation, manifest, supersedes=None):
    write(path/'manifest.json', manifest)
    before = physical.spent
    logical = inv.runtime.dispatcher.budget.spent
    started = time.perf_counter()
    try:
        result = await operation()
        write(path/'result.json', result)
        publish_status(inv, path, supersedes)
        write(path/'source-audit.json', source_audit(inv, result))
        outcome = {'state': 'completed', 'contract_errors': result['contract_errors']}
    except Exception as exc:
        outcome = {'state': 'failed', 'error': repr(exc)}
        write(path/'failure.json', outcome)
    finally:
        await inv.runtime.dispatcher.join()
        save(path, inv)
    budget = inv.runtime.dispatcher.budget
    assert not budget.reserved and not physical.reserved
    assert budget.spent - logical == physical.spent - before
    outcome.update(wall_seconds=time.perf_counter()-started,
                   incremental_tokens=physical.spent-before, logical_tokens=budget.spent,
                   tools=attempts(inv), ledger=inv.incident.store.ledger,
                   invalid=sorted(inv.incident.store.invalid()))
    write(path/'summary.json', outcome)
    return outcome


async def condition(path, name, vendor, registry, commit, physical):
    manifest = {'case': CASE, 'split': 'development_seen', 'condition': name,
                'code_commit': commit, 'dataset_commit': registry['dataset_commit'],
                'model': 'gpt-6-astra', 'diagnosis_rounds': 1, 'logical_cap': 360000,
                'tool_cap_including_prefix': 24, 'physical_batch_cap': 540000 if name == 'all-expiry' else 1200000,
                'max_batch_calls': 9 if name == 'all-expiry' else 20, 'no_retries': True,
                'collection_mode': 'telemetry_first' if name == 'handoff-expiry' else 'parallel'}
    write(path/'manifest.json', manifest)
    outcomes = {}
    def checkpoint():
        write(path/'summary.json', {'arms': outcomes, 'physical_spent_so_far': physical.spent,
                                   'physical_halted': physical.halted})
    def build(arm):
        memory = PolicyMemory(); policy = {'rules': []}
        memory.promote('oncall', 'baseline', policy, {'accepted': True, 'sha256': digest(policy)})
        inv = Investigation(Runtime(memory, Dispatcher(Budget(360000))), provider(path/arm, physical),
                            Snapshot(vendor, CASE, registry, commit), max_tools=24,
                            diagnosis_rounds=1, collection_mode=manifest['collection_mode'])
        return inv
    base = build('prefix')
    async def prefix():
        await base.collect()
        fault = (corrupt_port(base) if name == 'wrong-content' else
                 {'injected': True, 'artifact': 'initial' if name == 'all-expiry' else 'telemetry_evidence',
                  'kind': 'expiry after consumption; unchanged source contents'})
        write(path/'fault.json', fault)
        if not fault['injected']:
            raise ValueError('Predeclared corruption absent; no retargeting')
        return await base.diagnose()
    outcomes['prefix'] = await execute(path/'prefix', base, physical, prefix, manifest)
    checkpoint()
    if outcomes['prefix']['state'] != 'completed' or physical.halted:
        outcomes.update({n: {'state': 'skipped', 'reason': 'prefix failed or physical halt'}
                         for n in ('no-recovery', 'selective', 'restart', 'prevention')})
        checkpoint(); return
    fault = json.loads((path/'fault.json').read_text())
    store = base.incident.store
    invalid = store.invalidate([fault['artifact']])
    assert invalid == EXPECTED[name], (invalid, EXPECTED[name])
    try:
        store.publish('public-status', store.epoch, supersedes=1)
    except ValueError:
        rejected = True
    else:
        raise AssertionError('Invalid publication accepted')
    write(path/'notification.json', {'invalid': sorted(invalid), 'artifact': fault['artifact'],
                                    'obsolete_publication_rejected': rejected})
    historical = json.loads((path/'prefix/result.json').read_text())
    write(path/'no-recovery/manifest.json', manifest)
    write(path/'no-recovery/result.json', historical)
    write(path/'no-recovery/source-audit.json', source_audit(base, historical))
    outcomes['no-recovery'] = {'state': 'completed', 'incremental_tokens': 0,
                              'logical_tokens': base.runtime.dispatcher.budget.spent,
                              'tools': attempts(base), 'invalid': sorted(invalid)}
    frozen = copy.deepcopy(store.__dict__)
    order = ('selective', 'restart') if name == 'handoff-expiry' else ('restart', 'selective')
    for method in order:
        if physical.halted:
            outcomes[method] = {'state': 'skipped', 'reason': 'physical halt'}; continue
        inv = clone(base, provider(path/method, physical))
        assert inv.incident.store.__dict__ == frozen
        if method == 'restart':
            inv.runtime.restart(inv.identifier, {'reason': 'full restart comparator'})
        async def recover():
            await inv.collect(reuse=method == 'selective')
            return await inv.diagnose()
        outcomes[method] = await execute(path/method, inv, physical, recover, manifest, 1)
        assert store.__dict__ == frozen
        assert inv.incident.store.ledger[:1] == store.ledger[:1]
        checkpoint()
    if name == 'wrong-content' and not physical.halted:
        inv = build('prevention')
        async def prevent():
            await inv.collect()
            fault = corrupt_port(inv)
            write(path/'prevention/fault.json', fault)
            if not fault['injected']: raise ValueError('Predeclared corruption absent')
            audit = verify_inputs(inv, ['initial', 'configuration_evidence', 'telemetry_evidence'])
            write(path/'prevention/integrity-check.json', audit)
            return await inv.diagnose()
        outcomes['prevention'] = await execute(path/'prevention', inv, physical, prevent, manifest)
    else:
        outcomes['prevention'] = {'state': 'not_applicable' if name != 'wrong-content' else 'skipped',
                                 'reason': 'Unchanged source equality cannot discharge expiry' if name != 'wrong-content' else 'physical halt'}
    checkpoint()


async def main(args):
    if args.output.exists(): raise ValueError('Refuse overwrite')
    if subprocess.check_output(['git', '-C', str(ROOT), 'status', '--porcelain'], text=True).strip():
        raise ValueError('Commit before inference')
    registry = json.loads((ROOT/'configs/splits.json').read_text())
    if CASE not in registry['splits']['development_seen']: raise ValueError('Development only')
    commit = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
    negative = getattr(args, 'protocol', 'boundaries') == 'all-expiry'
    cap, call_cap = (540000, 9) if negative else (1200000, 20)
    physical = Budget(cap)
    for name in (('all-expiry',) if negative else ('handoff-expiry', 'wrong-content')):
        if physical.halted: break
        await condition(args.output/name, name, args.vendor, registry, commit, physical)
        write(args.output/'budget.json', {'spent': physical.spent, 'halted': physical.halted,
              'settlements': physical.settlements, 'cap': cap})
    assert len(physical.settlements) <= call_cap


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--protocol', choices=('boundaries', 'all-expiry'), default='boundaries')
    parser.add_argument('--vendor', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(main(parser.parse_args()))
