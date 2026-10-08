"""Reserve case IDs without reading benchmark questions or answers."""
import json
from pathlib import Path
import random
import re
import subprocess

root = Path(__file__).resolve().parents[1]
config = json.loads((root / 'configs/development.json').read_text())
vendor = (root / config['dataset_repository']).resolve()
revision = subprocess.check_output(['git', '-C', str(vendor), 'rev-parse', 'HEAD'], text=True).strip()
if revision != config['dataset_commit']:
    raise SystemExit('Dataset commit mismatch')
output = root / 'configs/splits.json'
if output.exists():
    raise SystemExit('Refusing to overwrite existing split registry')
paths = subprocess.check_output(['git', '-C', str(vendor), 'ls-tree', '-r', '--name-only', 'HEAD', 'benchmark/boutique'], text=True)
seen = {'runtime': [1, 2, 5, 21, 16], 'service': [1, 2, 18, 31, 34], 'performance': [1, 2, 7, 11, 8]}
pools = {category: set() for category in seen}
for path in paths.splitlines():
    match = re.match(r'benchmark/boutique/(runtime|service|performance)/(\d+)/', path)
    if match:
        pools[match[1]].add(int(match[2]))
rng = random.Random(20261008)
for category in pools:
    pools[category] = sorted(pools[category] - set(seen[category]))
    rng.shuffle(pools[category])
splits = {'development_seen': [f'boutique/{c}/{i}' for c in seen for i in seen[c]]}
for name, counts in [('learning', (2, 2, 2)), ('gate_validation', (2, 2, 2)),
                     ('shakedown', (2, 1, 1)), ('final_locked', (2, 3, 3))]:
    splits[name] = []
    for category, count in zip(pools, counts):
        if len(pools[category]) < count:
            raise SystemExit('Insufficient unseen IDs')
        splits[name].extend(f'boutique/{category}/{i}' for i in pools[category][:count])
        pools[category] = pools[category][count:]
registry = {'schema': 1, 'dataset_commit': revision, 'seed': 20261008,
            'isolation': 'case-ID only; generator/template overlap not yet audited',
            'final_unlocked': False, 'splits': splits}
output.write_text(json.dumps(registry, indent=2) + '\n')
print(json.dumps({k: len(v) for k, v in splits.items()}))
