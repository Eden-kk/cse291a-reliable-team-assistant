"""Offline late-notification replay of recorded outputs; no external publication."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from team_assistant.sources import VersionedSources
from team_assistant.state import ExecutionStore, Result


def load(path):
    return json.loads(path.read_text())


def records(state):
    results = {}
    for name, value in state.items():
        result = Result(**{**value, 'citations': set(value['citations'])})
        if result.name != name or result.version < 1 or not result.complete:
            raise ValueError('Invalid/incomplete artifact')
        results[name] = result
    return results


def import_results(store, results):
    """Order from dependencies, never JSON insertion order."""
    pending = dict(results)
    imported = []
    while pending:
        ready = [n for n, r in pending.items() if all(
            p in store.results and store.results[p].version == v and p not in store.invalid()
            for p, v in r.inputs.items())]
        if not ready:
            raise ValueError('Missing, cyclic or mismatched artifact dependencies')
        for name in ready:
            store.commit(pending.pop(name), store.epoch)
            imported.append(name)
    return imported


def source_registry(value, scope):
    registry = VersionedSources(scope)
    registry.heads = copy.deepcopy(value['heads'])
    registry.archive = {k: {int(v): r for v, r in revisions.items()}
                        for k, revisions in value['archive'].items()}
    if set(registry.heads) != set(registry.archive):
        raise ValueError('Source heads/archive mismatch')
    for key, head in registry.heads.items():
        if set(registry.archive[key]) != set(range(1, head + 1)):
            raise ValueError('Non-contiguous source history')
        for version, record in registry.archive[key].items():
            stamped = {**record, 'id': 'replay-validation', 'source_key': key,
                       'source_revision': version}
            if not registry.inspect(stamped)['authentic']:
                raise ValueError('Source scope/identity mismatch')
    return registry


def validate_observations(results, registry):
    for result in results.values():
        for observation in result.payload.get('observations', []):
            if not registry.inspect(observation)['fresh']:
                raise ValueError('Artifact observation is stale or unauthenticated')
            if result.sources.get(observation['source_key']) != observation['source_revision']:
                raise ValueError('Observation not bound to artifact source dependency')


def reject(operation):
    try:
        operation()
    except ValueError as exc:
        return str(exc)
    raise AssertionError('Stale operation unexpectedly succeeded')


def replay(window):
    names = ['manifest.json', 'transition.json'] + [f'{arm}/{file}.json'
        for arm in ('historical-prefix', 'selective')
        for file in ('manifest', 'state', 'sources', 'result')]
    files = {name: load(window / name) for name in names}
    manifest = files['manifest.json']
    for arm in ('historical-prefix', 'selective'):
        arm_manifest = files[f'{arm}/manifest.json']
        if any(arm_manifest.get(k) != v for k, v in manifest.items()):
            raise ValueError('Arm manifest differs from window manifest')
        expected = 'historical-prefix' if arm == 'selective' else None
        if arm_manifest.get('logical_prefix') != expected:
            raise ValueError('Missing/mismatched logical prefix')
        errors = files[f'{arm}/result.json']['contract_errors']
        allowed = ['uncertain: not a successful diagnosis']
        historical_uncertain = (arm == 'historical-prefix'
            and files[f'{arm}/result.json']['answer'].get('status') == 'uncertain'
            and errors == allowed)
        if errors and not historical_uncertain:
            raise ValueError('Cannot replay an output with contract errors')
    scope = f"oncall/incident-1/{manifest['case']}"
    old_sources = files['historical-prefix/sources.json']
    new_sources = files['selective/sources.json']
    before = source_registry(old_sources, scope)
    after = source_registry(new_sources, scope)
    if old_sources['events'] or not set(before.heads) <= set(after.heads):
        raise ValueError('Unexpected historical source history')
    transition = files['transition.json']
    if not transition['events'] or new_sources['events'] != transition['events']:
        raise ValueError('Missing/mismatched source advance events')
    expected_heads = dict(before.heads)
    for event in transition['events']:
        key = event['source']
        if expected_heads.get(key) != event['previous'] or event['revision'] != event['previous'] + 1:
            raise ValueError('Source advance event out of order')
        expected_heads[key] = event['revision']
    if any(after.heads[k] != v for k, v in expected_heads.items()):
        raise ValueError('Recorded source head does not match events')
    for key, history in before.archive.items():
        if any(after.archive[key].get(v) != value for v, value in history.items()):
            raise ValueError('Historical source archive changed')
    for key in set(after.heads) - set(before.heads):
        if after.heads[key] != 1:
            raise ValueError('New recovery query must begin at revision one')
    old = records(files['historical-prefix/state.json'])
    new = records(files['selective/state.json'])
    validate_observations(old, before)
    validate_observations(new, after)
    store = ExecutionStore()
    store.source_versions = dict(before.heads)
    # run_phase2_revision pins unchanged policy:v1 and snapshot for this bounded replay.
    snapshots = {r.sources['snapshot'] for r in [*old.values(), *new.values()] if 'snapshot' in r.sources}
    if len(snapshots) != 1:
        raise ValueError('Snapshot source mismatch')
    store.source_versions['snapshot'] = snapshots.pop()
    store.shared_versions = {'policy:v1': 1}
    if any(k != 'snapshot' and k not in after.heads for r in [*old.values(), *new.values()] for k in r.sources):
        raise ValueError('Unknown source dependency')
    import_results(store, old)
    if store.invalid():
        raise ValueError('Historical prefix is not fresh')
    answer = files['historical-prefix/result.json']['answer']
    targets = [n for n, r in old.items() if n.startswith('diagnosis_') and r.payload == answer]
    if len(targets) != 1:
        raise ValueError('Historical answer missing/ambiguous in state')
    name = targets[0]
    p1 = store.publish(name, store.epoch)
    captured = copy.deepcopy(old[name])
    captured.name = 'late_inflight_diagnosis'
    store.source_versions.update(expected_heads)
    invalid = sorted(store.invalidate([]))
    if invalid != sorted(transition['invalid']):
        raise ValueError('Replay invalidation differs from recorded transition')
    stale_publish = reject(lambda: store.publish(name, store.epoch))
    # Current epoch deliberately used: captured old dependencies must themselves fail.
    stale_commit = reject(lambda: store.commit(captured, store.epoch))
    updates = {}
    for key, value in new.items():
        if key not in old or value.version > old[key].version:
            updates[key] = value
        elif value != old[key]:
            raise ValueError('Recovery modified an artifact without a new version')
    if not set(old) <= set(new):
        raise ValueError('Recovery removed historical artifacts')
    store.source_versions.update(after.heads)
    imported = import_results(store, updates)
    if name not in updates or store.invalid():
        raise ValueError('Recovery did not replace stale published result')
    if store.results[name].payload != files['selective/result.json']['answer']:
        raise ValueError('Recovery answer differs from published artifact')
    p2 = store.publish(name, store.epoch, supersedes=p1['id'])
    assert store.ledger[0] == p1 and p2['supersedes'] == p1['id']
    return {'kind': 'offline replay of recorded real outputs and local simulated publications',
            'window': str(window.resolve()), 'case': manifest['case'],
            'fault_scope': manifest.get('fault_scope', 'legacy-unspecified'),
            'input_sha256': {n: hashlib.sha256((window/n).read_bytes()).hexdigest() for n in names},
            'limitations': ['No new inference or external writes',
                'Late notification/publication timing is simulated, not the original live schedule',
                'Freshness is dependency consistency, not semantic correctness or causal accuracy',
                'Initial policy:v1 active and epoch zero reconstructed from runner contract; no prior publications'],
            'new_model_calls': 0, 'new_tokens': 0, 'external_writes': 0,
            'fresh_before_change': True, 'invalid_after_change': invalid,
            'stale_republish_rejected': stale_publish, 'captured_inflight_rejected': stale_commit,
            'imported_new_versions': imported, 'retained_artifacts': sorted(set(old)-set(updates)),
            'ledger': store.ledger, 'original_publication_retained': store.ledger[0] == p1}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--window', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = replay(args.window)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps({k: result[k] for k in ('case', 'fault_scope', 'new_model_calls',
        'invalid_after_change', 'imported_new_versions', 'original_publication_retained')}))
