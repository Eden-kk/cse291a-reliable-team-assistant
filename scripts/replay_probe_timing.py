"""Replay saved handoff answers without inference or historical modifications."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from team_assistant.probe_timing import validate_probe_timing


def raw_visible(value):
    if isinstance(value, dict):
        if {'id', 'tool', 'text', 'availability'} <= value.keys():
            yield value
        else:
            for child in value.values():
                yield from raw_visible(child)
    elif isinstance(value, list):
        for child in value:
            yield from raw_visible(child)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('packets', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('Refusing to overwrite existing replay')
    rows = []
    for path in sorted(args.packets.glob('item-*.json')):
        original = path.read_bytes()
        packet = json.loads(original)
        records = {}
        for turn in packet['coordinator_turns']:
            for record in raw_visible(turn['inputs']):
                records[(record['id'], record['text'])] = record
        answer = packet['output']
        text = ' '.join(v for v in answer.values() if isinstance(v, str))
        rows.append({'packet': path.name, 'packet_sha256': hashlib.sha256(original).hexdigest(),
                     'original_output': answer,
                     'validation': validate_probe_timing(text, list(records.values()))})
        assert path.read_bytes() == original
    if len(rows) != 15:
        raise SystemExit(f'Expected all 15 saved answers; found {len(rows)}')
    result = {'mode': 'offline post-hoc development replay, not new model validation',
              'counts': dict(Counter(r['validation']['decision'] for r in rows)), 'rows': rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result['counts']))


if __name__ == '__main__':
    main()
