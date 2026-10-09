"""Offline Phase 1 control simulations and synthetic bookkeeping CPU measurements."""
import argparse
import asyncio
import copy
from dataclasses import asdict
import json
from pathlib import Path
import platform
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from team_assistant.dispatch import Budget, BudgetExhausted, Dispatcher, Outcome
from team_assistant.memory import PolicyMemory, digest
from team_assistant.runtime import Runtime, StaleAttempt
from team_assistant.state import ExecutionStore, Result


def promote(memory, version):
    policy = {'rule': version}
    memory.promote('team', version, policy, {'accepted': True, 'sha256': digest(policy)})


def runtime():
    memory = PolicyMemory()
    promote(memory, 'v1')
    runner = Runtime(memory, Dispatcher(Budget(10000), clock=lambda: 0.0))
    return runner, runner.start('incident', 'team', 'group').store


async def fake_provider(request):
    return Outcome({'rule': request.get('policy', {}).get('rule', 'independent'),
                    'inputs': request['inputs']}, 7)


async def usage_stop(mode):
    dispatcher = Dispatcher(Budget(100), concurrency=1, clock=lambda: 0.0)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def failing():
        calls.append(mode)
        entered.set()
        await release.wait()
        if mode == 'timeout':
            raise TimeoutError('simulated provider timeout; usage unavailable')
        return Outcome({'fake': True}, None)

    async def queued():
        calls.append('queued')
        return Outcome({}, 1)

    first = asyncio.create_task(dispatcher.call('group', 'response', 40, failing))
    await entered.wait()
    second = asyncio.create_task(dispatcher.call('group', 'response', 10, queued))
    await asyncio.sleep(0)
    release.set()
    outcomes = await asyncio.gather(first, second, return_exceptions=True)
    await dispatcher.join()
    budget = dispatcher.budget
    passed = (calls == [mode] and isinstance(outcomes[1], BudgetExhausted)
              and budget.halted and budget.spent == 40 and budget.reserved == 0
              and (isinstance(outcomes[0], TimeoutError) if mode == 'timeout'
                   else isinstance(outcomes[0], Outcome)))
    return {'passed': passed, 'simulation': mode, 'provider_calls': calls,
            'first_error': type(outcomes[0]).__name__ if isinstance(outcomes[0], Exception) else None,
            'queued_rejected': isinstance(outcomes[1], BudgetExhausted),
            'spent': budget.spent, 'reserved': budget.reserved,
            'halted': budget.halted, 'settlements': budget.settlements}


async def stale_policy_write():
    runner, store = runtime()
    await runner.step('incident', 'telemetry', fake_provider)
    retained = copy.deepcopy(store.results['telemetry'])
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed(request):
        entered.set()
        await release.wait()
        return Outcome({'rule': request['policy']['rule']}, 31)

    spent_before = runner.dispatcher.budget.spent
    task = asyncio.create_task(runner.step('incident', 'plan', delayed, use_memory=True))
    await entered.wait()
    affected = runner.revoke('team', 'v1')
    release.set()
    rejected = False
    try:
        await task
    except StaleAttempt:
        rejected = True
    await runner.dispatcher.join()
    charged = runner.dispatcher.budget.spent - spent_before
    kept = store.results['telemetry'] == retained and 'telemetry' not in store.invalid()
    return {'passed': rejected and charged == 31 and kept and 'plan' not in store.results,
            'stale_write_rejected': rejected, 'stale_call_charged_tokens': charged,
            'unaffected_branch_retained': kept, 'retained_version': retained.version,
            'stale_result_present': 'plan' in store.results, 'affected': affected,
            'events': runner.events}


async def published_correction():
    runner, store = runtime()
    await runner.step('incident', 'telemetry', fake_provider)
    await runner.step('incident', 'plan', fake_provider, use_memory=True)
    await runner.step('incident', 'answer', fake_provider, dependencies=('plan', 'telemetry'))
    action_ledger = []

    def propose(action, supersedes=None):
        allowed = action == 'publish' and 'answer' not in store.invalid()
        entry = {'actor': 'simulated-assistant', 'resource': 'in-memory:incident/answer',
                 'action': action, 'causal_inputs': dict(store.results['answer'].inputs),
                 'authorization': 'allowed' if allowed else 'denied', 'executed': False,
                 'supersedes': supersedes}
        action_ledger.append(entry)
        if not allowed:
            return None
        publication = runner.publish('incident', 'answer', lambda _: True, supersedes=supersedes)
        entry.update(executed=True, publication_id=publication['id'])
        return publication

    unauthorized = propose('restart_service')
    original = propose('publish')
    retained = copy.deepcopy(store.results['telemetry'])
    promote(runner.memory, 'v2')
    affected = runner.revoke('team', 'v1')
    blocked = False
    try:
        runner.publish('incident', 'answer', lambda _: True)
    except ValueError:
        blocked = True
    runner.memory.rebind('incident')
    await runner.step('incident', 'plan', fake_provider, use_memory=True, kind='recovery')
    await runner.step('incident', 'answer', fake_provider,
                      dependencies=('plan', 'telemetry'), kind='recovery')
    correction = propose('publish', supersedes=original['id'])
    passed = (unauthorized is None and not action_ledger[0]['executed']
              and blocked and len(store.ledger) == 2 and store.ledger[0] == original
              and correction['supersedes'] == original['id'] and correction['version'] == 2
              and correction['payload'] != original['payload']
              and store.results['telemetry'] == retained and not store.invalid())
    return {'passed': passed, 'publication': 'in-memory ledger only; no external publication',
            'invalid_publication_blocked': blocked, 'original_unchanged': store.ledger[0] == original,
            'unaffected_branch_retained': store.results['telemetry'] == retained,
            'affected': affected, 'ledger': store.ledger, 'action_ledger': action_ledger,
            'action_scope': 'bounded scripted allowlist simulation; not model safety evidence'}


def graph(size, tracked):
    # Two independent chains make affected and retained work explicit.
    nodes = []
    for index in range(size):
        name = f'node-{index}'
        inputs = {f'node-{index - 2}': 1} if tracked and index >= 2 else {}
        shared = {'policy:v1': 1} if tracked and index == 0 else {}
        nodes.append(Result(name, 1, {'value': index}, inputs=inputs, shared_reads=shared))
    return nodes


def tracking_overhead(size, repeats):
    if size < 2 or repeats < 1:
        raise ValueError('Graph size must be at least 2 and repeats must be positive')
    samples = {'tracked_commit_cpu_ns': [], 'no_dependency_commit_cpu_ns': [],
               'minimal_dictionary_commit_cpu_ns': [], 'invalidation_cpu_ns': []}
    graphs = {True: graph(size, True), False: graph(size, False)}
    invalid_count = 0
    for repeat in range(repeats + 1):
        # Alternate ordering to reduce systematic first/second measurement bias.
        for tracked in ((True, False) if repeat % 2 else (False, True)):
            store = ExecutionStore()
            store.shared_versions['policy:v1'] = 1
            start = time.process_time_ns()
            for node in graphs[tracked]:
                store.commit(node, store.epoch)
            elapsed = time.process_time_ns() - start
            if repeat:
                key = 'tracked_commit_cpu_ns' if tracked else 'no_dependency_commit_cpu_ns'
                samples[key].append(elapsed)
            if tracked:
                store.shared_versions['policy:v1'] = 0
                start = time.process_time_ns()
                invalid = store.invalidate([])
                elapsed = time.process_time_ns() - start
                invalid_count = len(invalid)
                if repeat:
                    samples['invalidation_cpu_ns'].append(elapsed)
        minimal = {}
        start = time.process_time_ns()
        for node in graphs[True]:
            minimal[node.name] = copy.deepcopy(node)
        elapsed = time.process_time_ns() - start
        if repeat:
            samples['minimal_dictionary_commit_cpu_ns'].append(elapsed)
    serialized_bytes = {str(tracked): len(json.dumps(
        [asdict(node) for node in nodes], sort_keys=True, default=list).encode('utf-8'))
                        for tracked, nodes in graphs.items()}
    summaries = {key: {'median': statistics.median(values), 'min': min(values), 'max': max(values)}
                 for key, values in samples.items()}
    deltas = [a - b for a, b in zip(samples['tracked_commit_cpu_ns'],
                                   samples['no_dependency_commit_cpu_ns'])]
    return {'graph_size': size, 'dependency_edges': size - 2, 'shared_reads': 1,
            'graph_shape': 'two interleaved independent chains; revoke first chain root policy',
            'repeats': repeats, 'warmup_repeats': 1, 'timer': 'time.process_time_ns',
            'python': platform.python_version(), 'platform': platform.platform(),
            'baseline': 'same ExecutionStore.commit and payloads, without dependency/shared-read metadata',
            'minimal_baseline': 'dictionary assignment plus deepcopy of identical tracked Results; '
                                'excludes validation, version checks, and invalidation',
            'serialized_result_bytes': {'tracked': serialized_bytes['True'],
                                        'no_dependency': serialized_bytes['False'],
                                        'minimal_dictionary': serialized_bytes['True']},
            'serialization_scope': 'UTF-8 JSON Result list only; excludes store/runtime and Python heap overhead',
            'timing_scope': 'preconstructed Result commits; store setup and graph construction excluded',
            'limitations': 'Synthetic local CPU bookkeeping only; no inference or workload performance claims. '
                           'Timing values vary by host/run; baseline omits dependency validation.',
            'invalidated_nodes': invalid_count, 'retained_nodes': size - invalid_count,
            'passed': invalid_count == (size + 1) // 2,
            'samples': samples, 'summary': summaries,
            'paired_tracking_increment_median_cpu_ns': statistics.median(deltas),
            'paired_validation_increment_median_cpu_ns': statistics.median([
                a - b for a, b in zip(samples['tracked_commit_cpu_ns'],
                                     samples['minimal_dictionary_commit_cpu_ns'])])}


async def run_controls(size=128, repeats=20):
    controls = {'timeout_unknown_usage_stop': await usage_stop('timeout'),
                'unknown_usage_stop': await usage_stop('unknown_usage'),
                'delayed_stale_policy_write': await stale_policy_write(),
                'published_correction': await published_correction()}
    overhead = tracking_overhead(size, repeats)
    return {'kind': 'offline_fake_provider_control_simulations', 'live_model_calls': 0,
            'limitations': 'Fake providers and in-memory publication exercise control mechanics, '
                           'not model quality, real provider timeout behavior, or external corrections.',
            'controls': controls, 'cpu_overhead': overhead,
            'passed': all(item['passed'] for item in controls.values()) and overhead['passed']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--graph-size', type=int, default=128)
    parser.add_argument('--repeats', type=int, default=20)
    args = parser.parse_args()
    result = asyncio.run(run_controls(args.graph_size, args.repeats))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'passed': result['passed'], 'output': str(args.output)}))
    sys.exit(0 if result['passed'] else 1)
