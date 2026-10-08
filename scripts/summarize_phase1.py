"""Aggregate the frozen Phase 1 matrix, retaining non-injection and failure outcomes."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAMES = ('runtime-port', 'service-address', 'performance-limit', 'service-instruction')


def read(path):
    return json.loads(path.read_text())


def tool_count(result):
    return len(result['observation_archive']) + len(result.get('tool_errors', []))


def summarize(root):
    rows, windows = [], []
    for name in NAMES:
        directory = root / name
        if not (directory / 'summary.json').exists():
            windows.append({'name': name, 'state': 'failed_or_missing', 'failure': read(directory / 'failure.json') if (directory / 'failure.json').exists() else None})
            continue
        summary = read(directory / 'summary.json')
        windows.append({'name': name, **summary})
        state = read(directory / 'collection/state.json')
        collection_tools = sum(len(r['payload'].get('observations', [])) + len(r['payload'].get('tool_errors', [])) for r in state.values())
        for method in ('clean', 'damaged', 'selective', 'restart', 'always-verify'):
            path = directory / method
            if not (path / 'result.json').exists():
                continue
            result = read(path / 'result.json')
            scores = read(path / 'scores.json')['scores'] if (path / 'scores.json').exists() else {}
            archive = {o['id']: o for o in result['observation_archive']}
            bad_citations = [e for e in result['answer'].get('evidence', []) if e.get('observation_id') not in archive or e.get('quote', '') not in archive[e['observation_id']]['text']]
            prefix = summary['collection']['tokens'] if method in ('clean', 'damaged') else (summary['prefix_tokens'] if method in ('selective', 'restart') else 0)
            tools = tool_count(result)
            if method in ('clean', 'damaged'):
                tools -= collection_tools
            elif method == 'selective':
                tools -= tool_count(read(directory / 'damaged/result.json'))
            consumed = False
            fault = summary['preventive_fault'] if method == 'always-verify' else summary['fault']
            changed_ids = {r['id'] for r in fault.get('changed', [])}
            for call in result['model_calls']:
                for artifact in call['request'].get('inputs', {}).values():
                    for obs in artifact.get('observations', []):
                        if obs['id'] in changed_ids and obs['id'] in archive and obs['text'] != archive[obs['id']]['text']:
                            consumed = True
            rows.append({'window': name, 'category': read(ROOT / f'configs/phase1/{name}.json')['category'],
                         'case': read(path / 'manifest.json')['case'], 'method': method,
                         'injected': fault.get('injected', False), 'consumed_modified_text': consumed,
                         'answer_status': result['answer'].get('status'), 'native_JRA': scores.get('JRA'),
                         'native_ECR': scores.get('ECR'), 'contract_valid': not result['contract_errors'],
                         'source_mismatch_citations': bad_citations,
                         'incremental_tokens': result['budget']['spent'], 'logical_total_tokens': prefix + result['budget']['spent'],
                         'phase_calls': len(result['model_calls']), 'phase_tools': tools,
                         'phase_seconds': result['phase_seconds'],
                         'rejected_tool_attempts': result.get('tool_errors', []),
                         'verification_cpu_seconds': sum(a['cpu_seconds'] for a in result.get('integrity_audits', [])),
                         'verification_checks': sum(a['checked_observations'] for a in result.get('integrity_audits', [])),
                         'verification_repairs': sum(len(a['repairs']) for a in result.get('integrity_audits', []))})
    calls = [read(p) for p in root.glob('**/calls/*.json') if p.stem.isdigit()]
    return {'scope': 'three seen incidents; four predeclared interventions; one run per arm; not final generalization',
            'windows': windows, 'rows': rows, 'physical_calls': len(calls),
            'physical_tokens': sum(c['tokens'] or 0 for c in calls),
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
