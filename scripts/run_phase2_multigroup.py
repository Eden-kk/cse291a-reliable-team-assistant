"""Bounded live source-revision scheduling trial; scoring is strictly post-inference."""
import argparse
import asyncio
from contextlib import nullcontext
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from team_assistant.cloudops import Snapshot
from team_assistant.dispatch import Budget, Dispatcher
from team_assistant.memory import PolicyMemory, digest
from team_assistant.phase2_budget import PhysicalProvider
from team_assistant.provider import CodexProvider
from team_assistant.revision_investigation import RevisionInvestigation
from team_assistant.runtime import Runtime
from run_phase2_revision import recovery_branch, attempts

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / 'configs/phase2/scheduling-live-v1.json'


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, default=list))


def provider(path, physical):
    return PhysicalProvider(CodexProvider(path / 'calls'), physical)


def build(path, physical, runtime, vendor, registry, commit, case, identifier, old):
    snapshot = Snapshot(vendor, case, registry, commit)
    inv = RevisionInvestigation(runtime, provider(path, physical), snapshot,
                                identifier=identifier, case=case, max_tools=24,
                                fault_scope='query-fields-v2')
    inv.old_phase = old
    return inv


def save(path, inv):
    write(path / 'state.json', {k: asdict(v) for k, v in inv.incident.store.results.items()})
    if hasattr(inv, 'revisions'):
        write(path / 'sources.json', {'heads': inv.revisions.heads, 'archive': inv.revisions.archive,
                                     'events': inv.revision_events})
    write(path / 'usage.json', {'calls': inv.provider.records,
                              'logical_total_tool_attempts': attempts(inv)})


async def execute(path, inv, reuse, origin):
    try:
        result = await inv.run(reuse=reuse)
        elapsed = time.monotonic() - origin
        result.update(window_latency_seconds=elapsed, source_events=getattr(inv, 'revision_events', []),
                      source_heads=inv.revisions.heads if hasattr(inv, 'revisions') else {}, logical_total_tool_attempts=attempts(inv))
        write(path / 'result.json', result)
        return {'state': 'completed', 'seconds': elapsed,
                'contract_errors': result['contract_errors'],
                'quality_scoring': 'pending independent native and semantic review'}
    except Exception as exc:
        failure = {'state': 'failed', 'error': repr(exc), 'seconds': time.monotonic() - origin}
        write(path / 'failure.json', failure)
        return failure
    finally:
        save(path, inv)


async def run_window(path, base, physical, policy, build_foreground, manifest):
    prefix_tokens = base.runtime.dispatcher.budget.spent
    inv = recovery_branch(base, provider(path / 'recovery', physical), 200000)
    dispatcher = Dispatcher(inv.runtime.dispatcher.budget, concurrency=2,
                            policy='fifo' if policy == 'fifo' else 'fair',
                            foreground_reservation=60000 if policy == 'fair-reserve' else None)
    inv.runtime.dispatcher = dispatcher
    fresh = build_foreground(path / 'foreground', inv.runtime)
    window_manifest = {**manifest, 'scheduler': policy, 'logical_prefix_tokens': prefix_tokens,
                       'logical_prefix': '../historical-prefix', 'fresh_case': 'boutique/runtime/1'}
    write(path / 'manifest.json', window_manifest)
    for name, case in [('recovery', 'boutique/performance/1'), ('foreground', 'boutique/runtime/1')]:
        write(path / name / 'manifest.json', {**window_manifest, 'case': case})
    before = physical.spent
    origin = time.monotonic()
    inv.deadline, fresh.deadline = origin + 60, origin + 30
    # Bound includes two parallel planners and at most three adaptive diagnosis calls.
    # Registration occurs only when this foreground investigation actually arrives.
    context = dispatcher.response_workflow(fresh.identifier, calls=5) if policy == 'fair-reserve' else nullcontext()
    async def foreground():
        with context:
            return await execute(path / 'foreground', fresh, False, origin)
    outcomes = await asyncio.gather(execute(path / 'recovery', inv, True, origin), foreground())
    await dispatcher.join()
    # Finalize shared accounting after both groups and all in-flight calls settle.
    for name, investigation in [('recovery', inv), ('foreground', fresh)]:
        save(path / name, investigation)
        result_path = path / name / 'result.json'
        if result_path.exists():
            result = json.loads(result_path.read_text())
            result.update(budget={'spent': dispatcher.budget.spent, 'halted': dispatcher.budget.halted},
                          runtime_events=getattr(inv.runtime, 'events', []),
                          dispatch_events=dispatcher.events)
            write(result_path, result)
    write(path / 'runtime-events.json', getattr(inv.runtime, 'events', []))
    summary = {'outcomes': dict(zip(('recovery', 'foreground'), outcomes)),
               'prefix_tokens': prefix_tokens, 'logical_total_tokens': dispatcher.budget.spent,
               'incremental_physical_tokens': physical.spent - before,
               'logical_budget_halted': dispatcher.budget.halted,
               'physical_halted': physical.halted, 'dispatch_events': dispatcher.events,
               'settlements': dispatcher.budget.settlements,
               'tool_attempts': {'recovery': attempts(inv), 'foreground': attempts(fresh)}}
    write(path / 'summary.json', summary)
    return summary


async def main(args):
    if args.output.exists():
        raise ValueError('Refuse overwrite')
    if subprocess.check_output(['git', '-C', str(ROOT), 'status', '--porcelain'], text=True).strip():
        raise ValueError('Commit code before inference')
    protocol = json.loads(PROTOCOL.read_text())
    fixed = {'shared_logical_token_cap_including_prefix': 200000,
             'per_group_total_tool_attempt_cap': 24, 'call_reservation': 60000,
             'foreground_call_bound': 5, 'concurrency': 2,
             'response_slo_seconds': 30, 'recovery_slo_seconds': 60,
             'fault_scope': 'query-fields-v2', 'physical_token_cap': 900000}
    if any(protocol.get(key) != value for key, value in fixed.items()):
        raise ValueError('Protocol constants changed; update and test runner before inference')
    registry = json.loads((ROOT / 'configs/splits.json').read_text())
    for case in protocol['cases']:
        if case not in registry['splits']['development_seen']:
            raise ValueError('Only development_seen cases permitted')
    commit = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
    physical = Budget(protocol['physical_token_cap'])
    manifest = {**protocol, 'code_commit': commit, 'dataset_commit': registry['dataset_commit'],
                'model': 'gpt-6-astra'}
    write(args.output / 'manifest.json', manifest)
    summaries = []
    def checkpoint():
        write(args.output / 'summary.json', {'repeats': summaries, 'physical_tokens': physical.spent,
              'physical_halted': physical.halted, 'physical_settlements': physical.settlements})
    for repeat, order in enumerate(protocol['policy_orders'], 1):
        if physical.halted or not physical.affordable(60000):
            summaries.append({'repeat': repeat, 'state': 'skipped', 'reason': 'physical budget unavailable'})
            checkpoint()
            continue
        path = args.output / f'repeat-{repeat}'
        memory = PolicyMemory()
        policy = {'rules': []}
        memory.promote('oncall', 'v1', policy, {'accepted': True, 'sha256': digest(policy)})
        runtime = Runtime(memory, Dispatcher(Budget(200000)))
        base = build(path / 'historical-prefix', physical, runtime, args.vendor, registry,
                     commit, 'boutique/performance/1', 'recovery', True)
        write(path / 'historical-prefix/manifest.json', {**manifest, 'case': 'boutique/performance/1'})
        entry = {'repeat': repeat, 'order': order, 'windows': {}}
        summaries.append(entry)
        entry['prefix'] = await execute(path / 'historical-prefix', base, False, time.monotonic())
        await runtime.dispatcher.join()
        checkpoint()
        if entry['prefix']['state'] != 'completed' or not base.originals or physical.halted:
            entry['state'] = 'skipped: prefix failure, no fixed exposure, or unknown usage'
            checkpoint()
            continue
        invalid = base.advance()
        write(path / 'transition.json', {'invalid': invalid, 'events': base.revision_events,
                                        'prefix_tokens': runtime.dispatcher.budget.spent})
        save(path / 'advanced-prefix', base)
        def fresh(destination, shared_runtime):
            return build(destination, physical, shared_runtime, args.vendor, registry,
                         commit, 'boutique/runtime/1', 'foreground', False)
        for scheduler in order:
            if physical.halted or not physical.affordable(60000):
                entry['windows'][scheduler] = {'state': 'skipped', 'reason': 'physical budget unavailable'}
            else:
                entry['windows'][scheduler] = await run_window(path / scheduler, base, physical,
                                                               scheduler, fresh, manifest)
            checkpoint()
    checkpoint()
    print(json.dumps({'physical_tokens': physical.spent, 'halted': physical.halted}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--vendor', type=Path, required=True)
    asyncio.run(main(parser.parse_args()))
