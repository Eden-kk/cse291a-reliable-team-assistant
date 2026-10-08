"""Summarize all declared development runs, retaining missing/failed statuses."""
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
rows = []
for category in ('runtime', 'service', 'performance'):
    for policy in ('empty', 'candidate'):
        directory = ROOT / 'runs' / f'query-memory-{category}-{policy}-v1'
        row = {'case': category + '/1', 'policy': policy, 'state': 'missing', 'calls': '', 'tools': '',
               'tokens': '', 'JRA': '', 'ECR': '', 'contract_valid': '', 'commit': ''}
        if (directory / 'result.json').exists():
            result = json.loads((directory / 'result.json').read_text())
            row.update(state='failed' if result.get('error') else 'completed',
                       calls=len(result.get('model_calls', [])), tools=len(result.get('observations', [])),
                       tokens=result.get('budget', {}).get('spent', result.get('budget_spent', '')))
        if (directory / 'scores.json').exists():
            scores = json.loads((directory / 'scores.json').read_text())['scores']
            row.update({k: scores[k] for k in ('JRA', 'ECR', 'contract_valid')})
        if (directory / 'manifest.json').exists():
            row['commit'] = json.loads((directory / 'manifest.json').read_text())['code_commit']
        rows.append(row)
with (ROOT / 'docs/development-memory-results.csv').open('w') as stream:
    writer = csv.DictWriter(stream, fieldnames=rows[0])
    writer.writeheader()
    writer.writerows(rows)
print(json.dumps(rows, indent=2))
