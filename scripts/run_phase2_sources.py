"""Offline source lifecycle controls using frozen Phase 1 collection graphs."""
import argparse
import copy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from team_assistant.sources import VersionedSources, source_key
from team_assistant.state import ExecutionStore, Result

ROOT = Path(__file__).resolve().parents[1]


def controlled_old(observation, current, historical):
    result = copy.deepcopy(observation)
    if current in result['text']:
        if result.get('truncated'):
            raise ValueError('Cannot reconstruct a truncated historical record')
        result['text'] = result['text'].replace(current, historical)
        result['sha256'] = hashlib.sha256(result['text'].encode()).hexdigest()
    return result


def replay(state, spec, mode):
    catalog = VersionedSources('offline/' + spec['case'])
    store = ExecutionStore()
    selected = state['configuration_evidence']['payload']['observations']
    changed_keys = {source_key(catalog.scope, o) for o in selected if spec['current'] in o['text']}
    if not changed_keys:
        return {'condition': mode, 'status': 'not_injected'}
    current_records = {}
    for value in state.values():
        for obs in value['payload'].get('observations', []):
            current_records[source_key(catalog.scope, obs)] = obs
    for name, value in state.items():
        payload = copy.deepcopy(value['payload'])
        records = []
        for obs in payload.get('observations', []):
            key = source_key(catalog.scope, obs)
            if mode == 'trusted_stale' and key in changed_keys:
                obs = controlled_old(obs, spec['current'], spec['historical'])
            records.append(catalog.record(obs))
        if 'observations' in payload:
            payload['observations'] = records
        catalog.sync(store)
        store.commit(Result(name, value['version'], payload, inputs=value['inputs'],
                            sources=catalog.dependencies(records)), store.epoch)
    dependencies = {name: store.results[name].version for name in
                    ('initial', 'configuration_evidence', 'telemetry_evidence')}
    store.commit(Result('diagnosis', 1, {'mechanism_marker': 'not an LLM answer'},
                        inputs=dependencies, citations={'initial', 'telemetry_evidence'}), store.epoch)
    store.publish('diagnosis', store.epoch)
    before = copy.deepcopy(store.results)
    keys = set(catalog.heads) if mode == 'dense_version_only' else changed_keys
    if mode != 'unchanged':
        for key in sorted(keys):
            catalog.advance(key, current_records[key], mode)
    audit = [catalog.inspect(obs) for r in store.results.values()
             for obs in r.payload.get('observations', [])]
    invalid = catalog.sync(store)
    citation_invalid = store.invalid(mode='citation')
    rejected = False
    try:
        store.commit(Result('late_answer', 1, {}, inputs=dependencies), store.epoch)
    except ValueError:
        rejected = True
    store.results.pop('late_answer', None)
    refreshed = []
    for name in list(store.results):
        old = store.results[name]
        if name not in invalid or 'observations' not in old.payload:
            continue
        if any(parent in store.invalid() for parent in old.inputs):
            continue
        records = [catalog.current(o['source_key'], o['id'] + '-r' + str(catalog.heads[o['source_key']]))
                   for o in old.payload['observations']]
        store.commit(Result(name, old.version + 1, {**old.payload, 'observations': records},
                            inputs=old.inputs, sources=catalog.dependencies(records)), store.epoch)
        refreshed.append(name)
    retained = sorted(name for name, value in before.items() if name not in invalid
                      and asdict(store.results[name]) == asdict(value))
    return {'condition': mode, 'status': 'completed', 'changed_sources': catalog.events,
            'authentic_observations': sum(a['authentic'] for a in audit),
            'stale_observations': sum(a['authentic'] and not a['fresh'] for a in audit),
            'local_record_checks': len(audit), 'invalidated': sorted(invalid),
            'citation_only_invalidated': sorted(citation_invalid), 'retained_unchanged': retained,
            'selective_recompute_nodes': len(invalid), 'restart_nodes': len(before),
            'late_commit_rejected': rejected, 'refreshed_evidence': refreshed,
            'pending_semantic_recompute': sorted(store.invalid()),
            'publication_history_preserved': len(store.ledger) == 1 and store.ledger[0]['version'] == 1,
            'state_after_refresh': {n: asdict(r) for n, r in store.results.items()}}


def main(output):
    protocol_path = ROOT / 'configs/phase2/protocol.json'
    protocol = json.loads(protocol_path.read_text())
    registry = json.loads((ROOT / 'configs/splits.json').read_text())
    results, sources = [], []
    for spec in protocol['cases']:
        if spec['case'] not in registry['splits']['development_seen']:
            raise ValueError('Only seen development cases allowed')
        path = ROOT / 'runs/phase1-matrix' / spec['phase1_run'] / 'collection/state.json'
        raw = path.read_bytes()
        sources.append({'path': str(path.relative_to(ROOT)), 'sha256': hashlib.sha256(raw).hexdigest()})
        for mode in protocol['controls']:
            results.append({'case': spec['case'], **replay(json.loads(raw), spec, mode)})
    report = {'study': 'offline constructed source transitions over recorded collection graphs',
              'code_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
              'protocol_sha256': hashlib.sha256(protocol_path.read_bytes()).hexdigest(),
              'inputs': sources, 'live_model_calls': 0, 'native_scores': 'not applicable',
              'limitations': ['not naturally observed history', 'not semantic recovery evidence',
                             'node counts are not latency or inference cost',
                             'query identity does not capture cross-query resource dependencies'],
              'results': results}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x') as handle:
        json.dump(report, handle, indent=2, default=list)
    for row in results:
        print(json.dumps({k: row[k] for k in ('case', 'condition', 'status', 'stale_observations',
              'invalidated', 'retained_unchanged', 'pending_semantic_recompute') if k in row}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    main(parser.parse_args().output)
