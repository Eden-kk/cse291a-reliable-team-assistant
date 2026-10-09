"""Derive model-call DAGs from completed revision runs for fixed-output replay."""
import copy
import hashlib
import json
import math
from pathlib import Path
import random

from .replay import replay


PROTOCOL = {
    'version': 1, 'group_counts': [2, 4], 'arrivals': ['burst', 'staggered'],
    'seeds': [11, 23, 47], 'policies': ['fifo', 'fair'],
    'methods': ['selective', 'restart'], 'concurrency': 2,
    'reservation': 60000, 'budget_per_group': [100000, 180000],
    'stagger_seconds': 5, 'response_slo': 30, 'recovery_slo': 60,
    'seed_role': 'shuffle group admission order only; not independent model samples',
}


def read(path):
    return json.loads(Path(path).read_text())


def derive_arm(window, arm):
    """Collapse non-model artifacts using recorded dependency edges, not call order."""
    directory = Path(window) / arm
    usage = read(directory / 'usage.json')
    state = read(directory / 'state.json')
    if (usage['summary']['state'] != 'completed'
            or usage['summary'].get('logical_budget_halted', False)
            or (directory / 'failure.json').exists()):
        raise ValueError(f'{arm}: failed/incomplete trace is ineligible')
    calls = usage['calls']
    if sum(c['tokens'] for c in calls if type(c.get('tokens')) is int) != usage['summary']['incremental_tokens']:
        raise ValueError('Call usage does not match incremental total')
    return derive_nodes(calls, state)


def derive_nodes(calls, state):
    """Validate recorded calls against immutable artifact lineage before replay."""
    if not calls or any(c.get('error') or type(c.get('tokens')) is not int
                        or c['tokens'] < 0 or type(c.get('seconds')) not in (int, float)
                        or not math.isfinite(c['seconds']) or c['seconds'] <= 0 for c in calls):
        raise ValueError('Missing/failed/unknown-usage calls')
    by_step = {c['request']['step']: c for c in calls}
    if len(by_step) != len(calls):
        raise ValueError('Repeated step requires versioned call history')

    def parents(name):
        for parent, version in state[name]['inputs'].items():
            if parent not in state or version != state[parent]['version']:
                raise ValueError('Input version differs from saved parent; mutable history unsupported')
        return state[name]['inputs']

    def ancestors(name, seen):
        if name in seen or name not in state:
            raise ValueError('Missing/cyclic recorded dependency')
        if name in by_step:
            return {name}
        result = set()
        for parent in parents(name):
            result |= ancestors(parent, seen | {name})
        return result

    nodes = []
    for step, call in sorted(by_step.items(), key=lambda pair: pair[1]['call']):
        if step not in state or not state[step]['complete']:
            raise ValueError('Call has no completed committed artifact')
        if set(call['request']['inputs']) != set(state[step]['inputs']):
            raise ValueError('Request inputs differ from committed dependencies')
        dependencies = set()
        for parent in parents(step):
            if call['request']['inputs'][parent] != state[parent]['payload']:
                raise ValueError('Request payload differs from saved parent; mutable history unsupported')
            dependencies |= ancestors(parent, {step})
        nodes.append({'id': step, 'dependencies': sorted(dependencies),
                      'duration': call['seconds'], 'tokens': call['tokens'],
                      'reservation': PROTOCOL['reservation']})
    # Validates cycles and missing references using the shared event simulator.
    replay([{**n, 'group': 'validation', 'kind': 'response', 'arrival': 0,
             'deadline': 1e9} for n in nodes], 'fifo', 10**9)
    return nodes


def load_pair(window):
    window = Path(window)
    summary = read(window / 'summary.json')['arms']
    if any(summary.get(a, {}).get('state') != 'completed'
           for a in ('historical-prefix', 'selective', 'restart')):
        raise ValueError('Incomplete matched pair; preserve as exclusion')
    prefix = summary['historical-prefix']['logical_total_tokens']
    arms = {a: derive_arm(window, a) for a in ('selective', 'restart')}
    for arm in arms:
        if summary[arm]['logical_total_tokens'] - summary[arm]['incremental_tokens'] != prefix:
            raise ValueError('Recovery arms do not share historical prefix accounting')
    files = ['manifest.json', 'summary.json'] + [f'{a}/{f}.json'
              for a in arms for f in ('usage', 'state')]
    return {'source': str(window), 'manifest': read(window / 'manifest.json'),
            'prefix_tokens': prefix, 'arms': arms,
            'source_hashes': {f: hashlib.sha256((window/f).read_bytes()).hexdigest() for f in files}}


def schedule(pair, count, arrivals, seed, method):
    """Half groups recover; half are new responses using current-source restart DAG."""
    groups = list(range(count))
    random.Random(seed).shuffle(groups)
    nodes, initial_service = [], {}
    for position, group in enumerate(groups):
        recovering = group < count // 2
        kind = 'recovery' if recovering else 'response'
        arrival = 0 if arrivals == 'burst' else position * PROTOCOL['stagger_seconds']
        deadline = arrival + PROTOCOL[f'{kind}_slo']
        identifier = f'group-{group}'
        if recovering:
            initial_service[identifier] = pair['prefix_tokens']
        for original in pair['arms'][method if recovering else 'restart']:
            node = copy.deepcopy(original)
            node.update(id=f'{identifier}:{original["id"]}', group=identifier,
                        kind=kind, arrival=arrival, deadline=deadline,
                        dependencies=[f'{identifier}:{d}' for d in original['dependencies']])
            nodes.append(node)
    return nodes, initial_service


def experiment(pair, reserve_candidate=False):
    outcomes = []
    for count in PROTOCOL['group_counts']:
        for arrival in PROTOCOL['arrivals']:
            for seed in PROTOCOL['seeds']:
                for budget in PROTOCOL['budget_per_group']:
                    for method in PROTOCOL['methods']:
                        nodes, service = schedule(pair, count, arrival, seed, method)
                        for policy in PROTOCOL['policies'] + (['fair-reserve'] if reserve_candidate else []):
                            result = replay(nodes, 'fair' if policy == 'fair-reserve' else policy, count * budget,
                                            concurrency=PROTOCOL['concurrency'],
                                            initial_spent=sum(service.values()), initial_service=service,
                                            reserve_foreground=policy == 'fair-reserve')
                            for group, metrics in result['groups'].items():
                                members = [n for n in nodes if n['group'] == group]
                                events = [e for e in result['events'] if e['group'] == group]
                                start = min(n['arrival'] for n in members)
                                metrics.update(kind=members[0]['kind'], arrival=start,
                                    response_seconds=metrics['seconds']-start if metrics['completed'] else None,
                                    first_dispatch_wait=min((e['start'] for e in events), default=start)-start if events else None,
                                    queue_seconds=sum(e['queue'] for e in events))
                            outcomes.append({'groups': count, 'arrival': arrival, 'seed': seed,
                                'budget': count*budget, 'prefix_tokens': sum(service.values()),
                                'method': method, 'policy': policy, 'result': result})
    return outcomes
