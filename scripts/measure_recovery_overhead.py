"""Offline microbenchmark of actual state tracking and source equality checks."""
import argparse
import copy
from dataclasses import asdict
import json
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from team_assistant.cloudops import Snapshot
from team_assistant.propagation import verify_inputs
from team_assistant.state import ExecutionStore, Result


def timed(fn):
    wall, cpu = time.perf_counter_ns(), time.process_time_ns()
    value = fn()
    return value, {'wall_ns': time.perf_counter_ns() - wall,
                   'cpu_ns': time.process_time_ns() - cpu}


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def fixture(size, topology, root):
    if size < 2 or topology not in ('sparse', 'dense'):
        raise ValueError('Expected size >= 2 and sparse/dense topology')
    observations = [{'id': f'o{i}', 'text': 'x' * 256, 'tool': 'fixture',
                     'arguments': {}} for i in range(size)]
    archive = root / 'source.json'
    archive.write_bytes(encode(observations))
    snapshot = Snapshot.__new__(Snapshot)
    snapshot.files = [archive]
    snapshot.observations = observations
    snapshot.source_version = snapshot.fingerprint()
    results = []
    for i, obs in enumerate(observations):
        parents = range(i) if topology == 'dense' else ([i - 1] if i % 2 else [])
        results.append(Result(f'n{i}', 1, {'observations': [copy.deepcopy(obs)]},
                              inputs={f'n{j}': 1 for j in parents},
                              sources={'archive': snapshot.source_version}))
    return snapshot, results


def serialize_store(store):
    results = {name: {**asdict(result), 'citations': sorted(result.citations)}
               for name, result in store.results.items()}
    return encode({'results': results, 'source_versions': store.source_versions,
                   'shared_versions': store.shared_versions, 'epoch': store.epoch,
                   'ledger': store.ledger,
                   'invalid_versions': sorted(store.invalid_versions)})


def sample(size, topology, root, tracked_first=False):
    snapshot, results = fixture(size, topology, root)
    plain = {}
    store = ExecutionStore()
    store.source_versions['archive'] = snapshot.source_version

    def commit_plain():
        for result in results:
            plain[result.name] = copy.deepcopy(result.payload)

    def commit_tracked():
        for result in results:
            store.commit(result, store.epoch)

    operations = [('baseline_commit', commit_plain), ('tracked_commit', commit_tracked)]
    if tracked_first:
        operations.reverse()
    metrics = {name: timed(fn)[1] for name, fn in operations}
    assert plain == {name: result.payload for name, result in store.results.items()}
    baseline_bytes, metrics['baseline_serialize'] = timed(lambda: encode(plain))
    tracked_bytes, metrics['tracked_serialize'] = timed(lambda: serialize_store(store))
    inv = SimpleNamespace(snapshot=snapshot, incident=SimpleNamespace(store=store))
    clean, metrics['source_check_clean'] = timed(lambda: verify_inputs(inv, list(store.results)))
    assert clean['checked_observations'] == size and clean['repairs'] == []
    # Inject an intermediate-artifact fault; the trusted archive remains unchanged.
    store.results['n0'].payload['observations'][0]['text'] = 'corrupted'
    repaired, metrics['source_check_repair'] = timed(lambda: verify_inputs(inv, ['n0']))
    assert repaired['repairs'] == [{'artifact': 'n0', 'observation': 'o0'}]
    assert store.results['n0'].payload == results[0].payload
    invalid, metrics['tracked_invalidate'] = timed(lambda: store.invalidate(['n0']))
    # Expected sets derive from fixture topology, never from ExecutionStore traversal.
    expected = {f'n{i}' for i in range(size)} if topology == 'dense' else {'n0', 'n1'}
    assert invalid == expected, (invalid, expected)
    # Conservative no-tracking baseline drops all outputs, including independent work.
    _, metrics['baseline_invalidate'] = timed(plain.clear)
    return {'metrics': metrics, 'serialized_bytes': {'baseline': len(baseline_bytes),
                                                    'tracked': len(tracked_bytes)},
            'invalidated': sorted(invalid), 'baseline_discarded': size,
            'checked_observations': clean['checked_observations'],
            'repaired_observations': len(repaired['repairs']), 'checks_pass': True}


def run(sizes=(8, 24, 48), repetitions=7, warmups=1):
    if repetitions < 1 or warmups < 0:
        raise ValueError('Expected positive repetitions and nonnegative warmups')
    conditions = []
    with tempfile.TemporaryDirectory(prefix='recovery-overhead-') as tmp:
        for size in sizes:
            for topology in ('sparse', 'dense'):
                for _ in range(warmups):
                    sample(size, topology, Path(tmp))
                samples = [sample(size, topology, Path(tmp), bool(i % 2))
                           for i in range(repetitions)]
                medians = {op: {clock: statistics.median(s['metrics'][op][clock] for s in samples)
                                for clock in ('wall_ns', 'cpu_ns')}
                           for op in samples[0]['metrics']}
                conditions.append({'size': size, 'topology': topology, 'samples': samples,
                                   'median_ns': medians,
                                   'commit_added_cpu_ns': medians['tracked_commit']['cpu_ns'] - medians['baseline_commit']['cpu_ns']})
    return {'schema_version': 1, 'python': sys.version, 'platform': platform.platform(),
            'sizes': list(sizes), 'repetitions': repetitions, 'warmups': warmups,
            'model_calls': 0, 'dataset_used': False, 'conditions': conditions,
            'scope': 'Local synthetic microbenchmark, not model cost or production latency. '
                     'Baseline copies identical payloads without metadata and clears all on invalidation; '
                     'it does not provide equivalent safety/reuse. Commit comparison includes dependency '
                     'validation and deep copy. Source checks use actual verify_inputs and file fingerprint, '
                     'but do not validate semantic correctness. Serialization measured before fault injection; '
                     'JSON byte counts are not memory usage. No persistence/network time included. '
                     'Construction/setup excluded; raw timing samples retained; no speed threshold asserted.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--sizes', type=int, nargs='+', default=[8, 24, 48])
    parser.add_argument('--repetitions', type=int, default=7)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Refuse overwrite')
    report = run(args.sizes, args.repetitions)
    report['code_commit'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    report['working_tree_dirty'] = bool(subprocess.check_output(['git', 'status', '--porcelain'], text=True).strip())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'conditions': len(report['conditions']), 'output': str(args.output)}))
