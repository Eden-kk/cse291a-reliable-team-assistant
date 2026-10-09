"""Replay predeclared heterogeneous development traces without model calls."""
import argparse
import hashlib
import itertools
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from team_assistant.trace_replay import derive_nodes, read
from team_assistant.replay import replay

ROOT = Path(__file__).resolve().parents[1]


def load_trace(spec, registry):
    directory = ROOT / spec['directory']
    manifest = read(directory / 'manifest.json')
    if manifest['case'] not in registry['splits']['development_seen']:
        raise ValueError('Only development_seen traces permitted')
    if manifest['dataset_commit'] != registry['dataset_commit']:
        raise ValueError('Dataset revision mismatch')
    files = [directory / name for name in ('manifest.json', 'result.json', 'state.json', 'scores.json')]
    result, state, score = (read(p) for p in files[1:])
    if (result.get('contract_errors') or result['budget']['halted']
            or (directory / 'failure.json').exists()):
        raise ValueError('Incomplete/invalid source trace')
    calls = result['model_calls']
    if sum(c['tokens'] for c in calls if type(c.get('tokens')) is int) != result['budget']['spent']:
        # Live group budget is shared. Its incremental usage is checked separately.
        usage_path = directory / 'usage.json'
        usage = read(usage_path)
        if usage['calls'] != calls:
            raise ValueError('Incremental call ledger mismatch')
        files.append(usage_path)
    if spec.get('collection'):
        collection = ROOT / spec['collection']
        prefix_manifest = read(collection / 'manifest.json')
        if prefix_manifest['case'] != manifest['case']:
            raise ValueError('Collection case mismatch')
        files.append(collection / 'manifest.json')
        call_files = sorted(p for p in (collection / 'calls').glob('*.json')
                            if not p.name.endswith('.request.json'))
        files.extend(call_files)
        calls = [read(p) for p in call_files] + calls
    if spec['kind'] == 'response':
        required = {'configuration_plan', 'telemetry_plan'}
        if not required <= {c['request']['step'] for c in calls}:
            raise ValueError('Response trace missing planner calls; collection cannot be prepaid')
    nodes = derive_nodes(calls, state)
    return {'case': manifest['case'], 'source': spec, 'nodes': nodes,
            'native_scores': score['scores'], 'source_code_commit': manifest['code_commit'],
            'source_hashes': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}


def run(protocol):
    registry = read(ROOT / 'configs/splits.json')
    traces, excluded = {}, []
    for name, spec in protocol['traces'].items():
        try:
            traces[name] = load_trace(spec, registry)
        except (ValueError, KeyError, FileNotFoundError) as exc:
            excluded.append({'trace': name, 'reason': str(exc)})
    summary_path = ROOT / protocol['prefix_summary']
    window = read(summary_path)
    prefix = window['prefix_tokens']
    recovery_manifest = read(ROOT / protocol['traces']['recovery']['directory'] / 'manifest.json')
    if prefix != recovery_manifest['logical_prefix_tokens']:
        raise ValueError('Recovery prefix provenance mismatch')
    incremental = sum(s['charged'] for s in window['settlements'])
    if (prefix + incremental != window['logical_total_tokens']
            or incremental != window['incremental_physical_tokens']):
        raise ValueError('Source window ledger mismatch')
    rows = []
    for foregrounds, spacing, budget, policy in itertools.product(
            protocol['foreground_sets'], protocol['spacing_seconds'],
            protocol['budgets'], protocol['policies']):
        names = ['recovery'] + foregrounds
        if any(name not in traces for name in names):
            rows.append({'foregrounds': foregrounds, 'spacing': spacing, 'budget': budget,
                         'policy': policy, 'state': 'excluded_source'})
            continue
        nodes = []
        for position, name in enumerate(names):
            kind = 'recovery' if name == 'recovery' else 'response'
            arrival = position * spacing
            for original in traces[name]['nodes']:
                nodes.append({**original, 'id': name + ':' + original['id'], 'group': name,
                              'kind': kind, 'arrival': arrival,
                              'deadline': arrival + protocol[kind + '_slo_seconds'],
                              'dependencies': [name + ':' + d for d in original['dependencies']]})
        result = replay(nodes, 'fair' if policy == 'fair-reserve' else policy, budget,
                        initial_spent=prefix, initial_service={}, concurrency=2,
                        reserve_foreground=policy == 'fair-reserve')
        for name, group in result['groups'].items():
            arrival = next(n['arrival'] for n in nodes if n['group'] == name)
            events = [e for e in result['events'] if e['group'] == name]
            group.update(latency_seconds=group['seconds'] - arrival if group['completed'] else None,
                         dispatched_call_max_ready_wait=max((e['queue'] for e in events), default=None),
                         dispatched_call_total_queue_seconds=sum(e['queue'] for e in events),
                         executed_calls=len(events))
        rows.append({'foregrounds': foregrounds, 'spacing': spacing, 'budget': budget,
                     'policy': policy, 'state': 'replayed', 'result': result})
    return {'protocol': protocol, 'physical_model_calls': 0, 'traces': traces,
            'excluded': excluded, 'prefix_tokens': prefix,
            'prefix_summary_sha256': hashlib.sha256(summary_path.read_bytes()).hexdigest(), 'rows': rows}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--protocol', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Refuse overwrite')
    report = run(read(args.protocol))
    with args.output.open('x') as handle:
        json.dump(report, handle, indent=2)
    print(json.dumps({'conditions': len(report['rows']), 'excluded': report['excluded'], 'model_calls': 0}))
