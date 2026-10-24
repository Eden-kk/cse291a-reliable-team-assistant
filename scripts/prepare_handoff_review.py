"""Prepare method-blinded answer/source packets; keep the mapping outside review input."""
import argparse
import hashlib
import json
from pathlib import Path
import random


def prepare(run, out):
    out.mkdir(parents=True, exist_ok=False)
    packets = out / 'packets'
    packets.mkdir()
    usage = json.loads((run / 'usage.json').read_text())['calls']
    summary = json.loads((run / 'summary.json').read_text())
    candidates = [(r['repeat'], arm, data) for r in summary['repeats']
                  for arm, data in r['arms'].items()
                  if arm != 'prefix' and data.get('result') is not None]
    random.Random(20261024).shuffle(candidates)
    mapping, hashes = {}, {}
    for index, (repeat, arm, data) in enumerate(candidates, 1):
        identity = f'item-{index:02d}'
        state = json.loads((run / f'repeat-{repeat}' / arm / 'state.json').read_text())
        sources = {}
        for name in ('initial', 'configuration_evidence', 'telemetry_evidence', 'summary_source'):
            for observation in state[name]['payload'].get('observations', []):
                if observation['id'] in sources:
                    assert sources[observation['id']] == observation
                sources[observation['id']] = observation
        calls = [c for c in usage if c['call'] in data['calls']
                 and c['request']['step'] in ('coordinator_decision', 'incident_brief')]
        packet = {'id': identity, 'output': data['result']['answer'],
                  'source_archive_for_review': list(sources.values()),
                  'coordinator_turns': [{'instruction': c['instruction'],
                                        'inputs': c['request']['inputs'], 'output': c['response']} for c in calls]}
        path = packets / f'{identity}.json'
        path.write_text(json.dumps(packet, indent=2))
        hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        mapping[identity] = {'repeat': repeat, 'arm': arm, 'calls': data['calls']}
    (out / 'mapping-private-until-review.json').write_text(json.dumps(mapping, indent=2))
    (packets / 'MANIFEST.json').write_text(json.dumps(hashes, indent=2))
    print(f'{len(mapping)} blinded packets prepared')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('run', type=Path)
    p.add_argument('out', type=Path)
    args = p.parse_args()
    prepare(args.run, args.out)
