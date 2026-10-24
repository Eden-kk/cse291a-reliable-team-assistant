"""Deterministic synthetic recovery fixtures; no model or benchmark inference."""
import argparse
import copy
from dataclasses import asdict
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from team_assistant.memory import digest
from team_assistant.state import ExecutionStore, Result

ORDER = ('configuration_raw', 'telemetry_raw', 'claim', 'recommendation', 'telemetry_summary', 'brief')
FIXTURES = {
    'derived_wrong_claim': {'closure': ['claim', 'recommendation', 'brief'], 'retained': 3},
    'source_version_update': {'closure': ['configuration_raw', 'claim', 'recommendation', 'brief'], 'retained': 2},
    'dense_dependency': {'closure': list(ORDER), 'retained': 0},
    'missing_provenance': {'closure': ['claim', 'recommendation', 'brief'], 'retained': 0},
}


def edges(fixture):
    return {
        'configuration_raw': (),
        'telemetry_raw': ('configuration_raw',) if fixture == 'dense_dependency' else (),
        'claim': ('configuration_raw',),
        'recommendation': ('claim',),
        'telemetry_summary': ('telemetry_raw',),
        'brief': ('recommendation', 'telemetry_summary'),
    }


def compute(name, store, source):
    """Toy transformations deliberately make semantics fully checkable, not realistic."""
    values = {key: result.payload for key, result in store.results.items()}
    if name == 'configuration_raw': return {'threshold': source['threshold']}
    if name == 'telemetry_raw': return {'health': 'degraded'}
    if name == 'claim': return {'threshold_claim': values['configuration_raw']['threshold']}
    if name == 'recommendation': return {'inspect_threshold': values['claim']['threshold_claim']}
    if name == 'telemetry_summary': return {'health': values['telemetry_raw']['health']}
    return {'inspect_threshold': values['recommendation']['inspect_threshold'],
            'health': values['telemetry_summary']['health']}


def execute(name, store, fixture, source, seed_error=False):
    payload = compute(name, store, source)
    if seed_error and name == 'claim': payload = {'threshold_claim': 9}
    version = store.results[name].version + 1 if name in store.results else 1
    store.commit(Result(name, version, payload,
                        inputs={p: store.results[p].version for p in edges(fixture)[name]},
                        sources={'configuration': source['version']} if name == 'configuration_raw' else {}),
                 store.epoch)


def snapshot(store):
    return json.loads(json.dumps({name: asdict(result) for name, result in store.results.items()}, default=sorted))


def hashes(store):
    return {name: digest(result) for name, result in snapshot(store).items()}


def run_fixture(fixture):
    source = {'version': 1, 'threshold': 3}
    base = ExecutionStore()
    base.source_versions['configuration'] = 1
    for name in ORDER: execute(name, base, fixture, source, seed_error=True)
    base.publish('brief', base.epoch)
    before = snapshot(base)
    # The changed source is a synthetic later snapshot, not a native incident history.
    if fixture == 'source_version_update':
        source = {'version': 2, 'threshold': 4}
        base.source_versions['configuration'] = 2
        changed = []
    elif fixture == 'dense_dependency': changed = ['configuration_raw']
    else: changed = ['claim']
    if fixture == 'missing_provenance': base.results['claim'].complete = False
    closure = base.invalidate(changed)
    assert closure == set(FIXTURES[fixture]['closure'])
    frozen = copy.deepcopy(base.__dict__)
    arms = {}
    for method in ('no_recovery', 'restart', 'selective'):
        store = copy.deepcopy(base)
        starting_hashes = hashes(store)
        gaps = store.provenance_gaps()
        fallback = method == 'selective' and bool(gaps)
        if method == 'restart' or fallback: store.restart()
        work = []
        if method != 'no_recovery':
            for name in ORDER:
                if name in store.invalid():
                    execute(name, store, fixture, source)
                    work.append(name)
        try:
            store.publish('brief', store.epoch, supersedes=1)
            published = True
        except ValueError:
            published = False
        after = snapshot(store)
        retained = sorted(n for n, h in starting_hashes.items()
                          if h == hashes(store)[n] and n not in store.invalid())
        semantic_match = store.results['brief'].payload == {
            'inspect_threshold': source['threshold'], 'health': 'degraded'}
        if method == 'no_recovery':
            assert not published and not semantic_match and not work
        else:
            assert published and semantic_match and not store.invalid()
            assert store.ledger[-1]['supersedes'] == 1
            assert len(retained) == (0 if method == 'restart' else FIXTURES[fixture]['retained'])
            assert store.results['configuration_raw'].sources['configuration'] == source['version']
        arms[method] = {'work_units': len(work), 'executed_nodes': work,
                        'fallback_restart': fallback, 'observable_provenance_gaps': gaps,
                        'published': published, 'matches_toy_semantic_oracle': semantic_match,
                        'valid_unchanged_nodes': retained, 'invalid_after': sorted(store.invalid()),
                        'before_hashes': starting_hashes, 'after_hashes': hashes(store),
                        'state': after, 'ledger': store.ledger}
        assert base.__dict__ == frozen
    return {'source_after': source, 'seeded_state': before,
            'assigned_expected_closure': sorted(FIXTURES[fixture]['closure']),
            'actual_closure': sorted(closure), 'arms': arms}


def run():
    return {'kind': 'deterministic_synthetic_seeded_state_recovery', 'api_calls': 0,
            'limitations': ['Not natural LLM adoption or semantic recovery evidence.',
                            'Handwritten transformations and oracle, unit work counts only.',
                            'Observable provenance gaps trigger explicit controller fallback; silently omitted edges remain undetectable.',
                            'Source update is synthetic; no native benchmark cases or labels used.'],
            'fixtures': {name: run_fixture(name) for name in FIXTURES}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = run()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2, default=sorted)
        stream.write('\n')
