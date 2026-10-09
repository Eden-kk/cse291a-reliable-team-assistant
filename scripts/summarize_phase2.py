"""Audit recorded Phase 2 costs and consumed source revisions, without inference."""
import argparse
import hashlib
import json
from pathlib import Path


def summarize(directory):
    manifest = json.loads((directory / 'manifest.json').read_text())
    summary = json.loads((directory / 'summary.json').read_text())
    rows, known, unknown, physical_calls = [], 0, 0, 0
    prefix = summary['arms'].get('historical-prefix', {}).get('logical_total_tokens', 0)
    for name, status in summary['arms'].items():
        row = {'arm': name, **status}
        records = [json.loads(p.read_text()) for p in sorted((directory/name/'calls').glob('[0-9][0-9][0-9].json'))]
        row['physical_model_calls'] = len(records)
        row['known_incremental_tokens'] = sum(r['tokens'] for r in records if r['tokens'] is not None)
        row['unknown_usage_calls'] = sum(r['tokens'] is None for r in records)
        known += row['known_incremental_tokens']
        unknown += row['unknown_usage_calls']
        physical_calls += len(records)
        if 'logical_total_tokens' in row:
            logical = row['known_incremental_tokens'] + manifest['reservation'] * row['unknown_usage_calls']
            if name in ('selective', 'restart'):
                logical += prefix
            if logical != row['logical_total_tokens']:
                raise ValueError('Logical ledger mismatch for ' + name)
        path = directory/name/'result.json'
        if path.exists():
            result = json.loads(path.read_text())
            row['answer'] = result['answer']
            row['contract_errors'] = result['contract_errors']
            heads = result['source_heads']
            calls = [r for r in records if r['request'].get('step', '').startswith('diagnosis_')]
            consumed = [o for r in calls for payload in r['request'].get('inputs', {}).values()
                        for o in payload.get('observations', [])]
            row['diagnosis_requests'] = len(calls)
            row['consumed_observation_instances'] = len(consumed)
            row['stale_consumed_instances'] = sum(o.get('source_revision') != heads.get(o.get('source_key')) for o in consumed)
            row['freshness_scope'] = 'at saved source heads; historical prefix has old heads, not current-state success'
            score = directory/name/'scores.json'
            if score.exists():
                row['scores'] = json.loads(score.read_text())['scores']
        rows.append(row)
    charged = known + manifest['reservation'] * unknown
    if charged != summary['physical_tokens']:
        raise ValueError('Physical ledger mismatch')
    hashes = {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(directory.rglob('*')) if p.is_file()}
    return {'manifest': manifest, 'physical_model_calls': physical_calls,
            'known_tokens': known, 'unknown_usage_calls': unknown, 'charged_tokens': charged,
            'physical_halted': summary['physical_halted'], 'arms': rows, 'input_sha256': hashes,
            'limitations': ['constructed query-source transition, not naturally observed history',
                           'explicit change notification; no automatic detector',
                           'small seen-case development trial; no causal latency claim',
                           'fresh source versions and native scores do not prove semantic support']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = summarize(args.directory)
    with args.output.open('x') as handle:
        json.dump(report, handle, indent=2)
    print(json.dumps({k: v for k, v in report.items() if k not in ('arms', 'input_sha256')}, indent=2))
