"""Controlled integration scenario. No API calls and no model-quality claims."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import subprocess

from .dispatch import Budget, Dispatcher, Outcome
from .memory import PolicyMemory, digest
from .runtime import Runtime, StaleAttempt


def approve(memory, version, check):
    policy = {'check': check}
    memory.promote('oncall', version, policy, {'accepted': True, 'sha256': digest(policy)})


async def controlled_backend(request):
    await asyncio.sleep(0.001)
    return Outcome({'kind': 'controlled', 'query': request.get('policy', {}).get('check', 'metrics'),
                    'input_steps': list(request['inputs'])}, 10)


async def run(config):
    memory = PolicyMemory()
    approve(memory, 'v1', 'configuration')
    dispatcher = Dispatcher(Budget(config['total_token_budget']), config['concurrency'],
                            config['scheduler'], config['age_seconds'])
    runtime = Runtime(memory, dispatcher, config['max_active_incidents'])
    for identifier in ('A', 'B'):
        runtime.start(identifier, 'oncall', 'group-' + identifier)
        await runtime.step(identifier, 'telemetry', controlled_backend)
        await runtime.step(identifier, 'plan', controlled_backend, use_memory=True)
        await runtime.step(identifier, 'diagnosis', controlled_backend, dependencies=('telemetry', 'plan'))
        runtime.publish(identifier, 'diagnosis', lambda value: value['kind'] == 'controlled')
    entered, release = asyncio.Event(), asyncio.Event()
    async def old_backend(request):
        entered.set()
        await release.wait()
        return Outcome({'kind': 'controlled', 'query': 'old-inflight'}, 17)
    old = asyncio.create_task(runtime.step('A', 'plan', old_backend, use_memory=True))
    await entered.wait()
    approve(memory, 'v2', 'recent-change-events')
    promotion_preserved = all(not i.store.invalid() for i in runtime.incidents.values())
    runtime.start('C', 'oncall', 'group-C')
    affected = runtime.revoke('oncall', 'v1')
    release.set()
    rejected = False
    try:
        await old
    except StaleAttempt:
        rejected = True
    async def recover(identifier):
        memory.rebind(identifier)
        await runtime.step(identifier, 'plan', controlled_backend, use_memory=True, kind='recovery')
        await runtime.step(identifier, 'diagnosis', controlled_backend,
                           dependencies=('telemetry', 'plan'), kind='recovery')
        runtime.publish(identifier, 'diagnosis', lambda value: value['kind'] == 'controlled', supersedes=1)
    await asyncio.gather(recover('A'), recover('B'),
                         runtime.step('C', 'plan', controlled_backend, use_memory=True))
    await dispatcher.join()
    checks = {'ordinary_promotion_preserved_work': promotion_preserved,
              'old_inflight_rejected': rejected,
              'only_actual_consumers_invalidated': affected == {'A': ['diagnosis', 'plan'], 'B': ['diagnosis', 'plan']},
              'independent_telemetry_retained': all(runtime.incidents[i].store.results['telemetry'].version == 1 for i in ('A', 'B')),
              'C_unaffected': runtime.incidents['C'].store.epoch == 0,
              'corrections_linked': all(len(runtime.incidents[i].store.ledger) == 2 and runtime.incidents[i].store.ledger[1]['supersedes'] == 1 for i in ('A', 'B')),
              'all_cost_settled_including_discard': dispatcher.budget.spent == 127 and dispatcher.budget.reserved == 0}
    if not all(checks.values()):
        raise AssertionError(checks)
    return {'kind': 'controlled integration; assumed gate approvals; simulated usage; no semantic evaluation',
            'checks': checks, 'tokens': dispatcher.budget.spent,
            'runtime_events': runtime.events, 'dispatch_events': dispatcher.events,
            'settlements': dispatcher.budget.settlements,
            'ledgers': {i: state.store.ledger for i, state in runtime.incidents.items()}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--config', type=Path, default=Path('configs/development.json'))
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output directory exists; choose a new run path')
    raw = args.config.read_bytes()
    config = json.loads(raw)
    if config['provider'] != 'controlled':
        parser.error('This demo supports only the controlled backend')
    result = asyncio.run(run(config))
    result['manifest'] = {'config_sha256': hashlib.sha256(raw).hexdigest(),
                          'code_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                          'code_dirty': bool(subprocess.check_output(['git', 'status', '--porcelain'], text=True)),
                          'dataset_commit': config['dataset_commit'], 'dataset_used': False}
    args.output.mkdir(parents=True)
    (args.output / 'config.json').write_bytes(raw)
    (args.output / 'result.json').write_text(json.dumps(result, indent=2))
    with (args.output / 'events.jsonl').open('w') as stream:
        for event in result['runtime_events']:
            stream.write(json.dumps(event) + '\n')
    print(json.dumps({'checks': result['checks'], 'simulated_tokens': result['tokens'], 'output': str(args.output)}, indent=2))


if __name__ == '__main__':
    main()
