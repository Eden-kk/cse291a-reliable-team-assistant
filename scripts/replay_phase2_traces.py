"""Offline fixed-output scheduling sensitivity study; never sends model calls."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from team_assistant.trace_replay import PROTOCOL, experiment, load_pair


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('windows', type=Path, nargs='+')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = {'protocol': PROTOCOL, 'physical_model_calls': 0, 'eligible': [], 'excluded': [],
        'limitations': [
            'Fixed recorded outputs, durations and tokens; neither live latency nor quality evaluation.',
            'Replicated groups and schedule seeds are counterfactual workloads, not independent samples.',
            'Only complete paired arms eligible; exclusions retained; failure rates cannot be inferred.',
            'Model duration includes recorded provider overhead; local tool/verification/handoff cost omitted.',
            'New response uses restart current-source incremental trace without historical prefix.',
            'Recovering groups prepaid identical prefix tokens; all methods share total budget.',
            'Time starts at recovery/arrival window; historical prefix latency is excluded.',
            'Group completion means replayed call DAG finished, not independently verified diagnosis.',
            'Future durations and tokens are used only for completion/settlement, never scheduling rank.',
            'No new output may be inferred for rejected or unfinished groups; no operational writes.',
        ]}
    for window in args.windows:
        try:
            pair = load_pair(window)
            result['eligible'].append({**pair, 'replays': experiment(pair)})
        except (ValueError, KeyError, FileNotFoundError) as exc:
            result['excluded'].append({'source': str(window), 'reason': str(exc)})
    with args.output.open('x') as handle:
        json.dump(result, handle, indent=2)
    print(json.dumps({'eligible': len(result['eligible']), 'excluded': result['excluded'],
                      'replays': sum(len(p['replays']) for p in result['eligible']), 'physical_model_calls': 0}))


if __name__ == '__main__':
    main()
