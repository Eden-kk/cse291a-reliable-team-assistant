"""Aggregate fixed conditions; retain partial failures, retries and unknown-usage charges."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAMES = ('runtime-port', 'service-address', 'performance-limit', 'service-instruction')


def read(path):
    return json.loads(path.read_text())


def optional(path):
    return read(path) if path.exists() else {}


def records(directory):
    return [read(p) for p in directory.glob('**/calls/*.json') if p.stem.isdigit()]


def tools(result):
    return len(result['observation_archive']) + len(result.get('tool_errors', []))


def summarize(root):
    rows, windows = [], []
    for name in NAMES:
        original = root / name
        retry = root / (name + '-retry1')
        directory = retry if (retry / 'summary.json').exists() else original
        summary = optional(directory / 'summary.json')
        fault = optional(directory / 'fault.json')
        state = optional(directory / 'collection/state.json')
        collection_tools = sum(len(r['payload'].get('observations', [])) + len(r['payload'].get('tool_errors', [])) for r in state.values())
        collection_calls = records(directory / 'collection')
        collection_tokens = sum(r['tokens'] or 0 for r in collection_calls)
        damaged = optional(directory / 'damaged/result.json')
        prefix_tokens = collection_tokens + damaged.get('budget', {}).get('spent', 0)
        win = {'name': name, 'state': summary.get('state', 'partial_provider_failure'), 'fault': fault,
               'source': str(directory.relative_to(ROOT)), 'collection_tokens': collection_tokens,
               'collection_calls': len(collection_calls), 'prefix_tokens': prefix_tokens,
               'failures': [{'path': str(p.relative_to(ROOT)), 'record': read(p)} for d in (original, retry, root / (name+'-preventive-retry1')) for p in d.glob('failure.json')],
               'invalidated': summary.get('invalidated'), 'retained_valid': summary.get('retained_valid')}
        for method in ('clean', 'damaged', 'selective', 'restart', 'always-verify'):
            path = directory / method
            preventive_retry = root / (name + '-preventive-retry1')
            if method == 'always-verify' and (preventive_retry / method / 'result.json').exists():
                path = preventive_retry / method
            if not (path / 'result.json').exists():
                continue
            result = read(path / 'result.json')
            scores = optional(path / 'scores.json').get('scores', {})
            archive = {o['id']: o for o in result['observation_archive']}
            bad = [e for e in result['answer'].get('evidence', []) if e.get('observation_id') not in archive or e.get('quote', '') not in archive[e['observation_id']]['text']]
            prefix = collection_tokens if method in ('clean', 'damaged') else (prefix_tokens if method in ('selective', 'restart') else 0)
            count = tools(result) - (collection_tools if method in ('clean', 'damaged') else (tools(damaged) if method == 'selective' else 0))
            injection = fault
            if method == 'always-verify':
                parent = optional(path.parent / 'summary.json')
                injection = parent.get('preventive_fault', parent.get('fault', {}))
            ids = {r['id'] for r in injection.get('changed', [])}
            consumed = any(o['id'] in ids and o['id'] in archive and o['text'] != archive[o['id']]['text']
                           for c in result['model_calls'] for a in c['request'].get('inputs', {}).values() for o in a.get('observations', []))
            rows.append({'window': name, 'method': method, 'source': str(path.relative_to(ROOT)),
                         'category': read(ROOT / f'configs/phase1/{name}.json')['category'],
                         'case': read(path / 'manifest.json')['case'], 'injected': injection.get('injected', False),
                         'consumed_modified_text': consumed, 'answer_status': result['answer'].get('status'),
                         'native_JRA': scores.get('JRA'), 'native_ECR': scores.get('ECR'),
                         'contract_valid': not result['contract_errors'], 'source_mismatch_citations': bad,
                         'incremental_tokens': result['budget']['spent'], 'logical_total_tokens': prefix + result['budget']['spent'],
                         'phase_calls': len(result['model_calls']), 'phase_tools': count, 'phase_seconds': result['phase_seconds'],
                         'rejected_tool_attempts': result.get('tool_errors', []),
                         'verification_cpu_seconds': sum(a['cpu_seconds'] for a in result.get('integrity_audits', [])),
                         'verification_checks': sum(a['checked_observations'] for a in result.get('integrity_audits', [])),
                         'verification_repairs': sum(len(a['repairs']) for a in result.get('integrity_audits', []))})
            key = 'preventive' if method == 'always-verify' else method
            win[key+'_total_tokens'] = prefix + result['budget']['spent']
        after = optional(directory / 'selective/state.json')
        before = optional(directory / 'damaged/state.json')
        if after and before and win['invalidated'] is None:
            win['invalidated'] = [k for k,v in before.items() if k not in after or v['version'] != after[k]['version']]
            win['retained_valid'] = [k for k,v in before.items() if k in after and v['version'] == after[k]['version']]
            win['state_comparison_note'] = 'Recomputed/retained derived from saved versions; run failed later during prevention.'
        windows.append(win)
    calls = records(root)
    return {'scope': 'three seen incidents; four predeclared conditions; retries and non-injection retained; not final generalization',
            'windows': windows, 'rows': rows, 'physical_calls': len(calls),
            'physical_tokens_known': sum(c['tokens'] or 0 for c in calls),
            'physical_tokens_charged': sum(c['tokens'] if c['tokens'] is not None else 60000 for c in calls),
            'unknown_usage': sum(c['tokens'] is None for c in calls), 'provider_errors': sum(bool(c['error']) for c in calls)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--runs', type=Path, default=ROOT / 'runs/phase1-matrix')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.runs)
    with args.output.open('x') as handle:
        json.dump(result, handle, indent=2)
    print(json.dumps({k: v for k, v in result.items() if k not in ('windows', 'rows')}))
