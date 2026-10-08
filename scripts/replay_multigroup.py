"""Replay observed post-revocation DAG with explicit zero/small handoff assumptions."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from team_assistant.replay import replay


def derive(summary):
    origin = summary['window_origin_monotonic']
    events = summary['dispatch_events']
    finishes = {e['job']: e for e in events if e['event'] == 'finish'}
    starts = [e for e in events if e['event'] == 'start']
    prefix_service = {}
    for event in starts:
        if event['time'] < origin:
            prefix_service[event['group']] = prefix_service.get(event['group'], 0) + finishes[event['job']]['actual']
    nodes = []
    for group in ('A', 'B', 'C'):
        group_jobs = [e for e in starts if e['group'] == group and e['time'] >= origin]
        if len(group_jobs) < 3:
            raise ValueError('Expected this workflow: two planners then one or more coordinator calls')
        for index, event in enumerate(group_jobs):
            end = finishes[event['job']]
            if end.get('error') or end['actual'] is None:
                raise ValueError('Failed/unknown-usage trace needs explicit failure model')
            deps = [] if index < 2 else ([e['job'] for e in group_jobs[:2]] if index == 2 else [group_jobs[index-1]['job']])
            nodes.append({'id': event['job'], 'group': group, 'kind': event['kind'],
                          'dependencies': deps, 'duration': end['time'] - event['time'],
                          'tokens': end['actual'], 'reservation': event['reservation'],
                          'arrival': 0, 'deadline': 30 if group == 'C' else 60})
    return nodes, prefix_service


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('window', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    summary = json.loads((args.window / 'summary.json').read_text())
    nodes, service = derive(summary)
    results = {}
    for policy in ('fifo', 'fair'):
        for delay in (0, .02):
            results[f'{policy}-ready-delay-{delay}'] = replay(nodes, policy, 360000,
                initial_spent=summary['prefix_tokens'], initial_service=service, join_delay=delay)
    output = {'source': str(args.window), 'observed': summary['outcomes'], 'nodes': nodes,
              'assumptions': ['all roots ready at origin', 'two parallel planners followed by recorded coordinator chain',
                              'fixed recorded outputs/service/tokens; future durations not used for ranking',
                              'handoff delay is sensitivity assumption, not measured queue latency',
                              'FIFO/fair reuse production selection; instant ready transitions omit asyncio callback refill race'],
              'replays': results}
    with args.output.open('x') as handle:
        json.dump(output, handle, indent=2)
    print(json.dumps({k: v['groups'] for k, v in results.items()}, indent=2))
