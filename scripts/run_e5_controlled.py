"""Run all twelve fixed controlled conditions without a dataset or model provider."""
import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from team_assistant.e5_controlled import run


async def main(output):
    if output.exists():
        raise ValueError('Refuse overwrite')
    if subprocess.check_output(['git', 'status', '--porcelain'], text=True).strip():
        raise ValueError('Commit before recording experiments')
    manifest = {'code_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                'backend': 'controlled synthetic', 'dataset_used': False, 'real_model_calls': 0,
                'seed_policy': 'fixture-approved', 'candidate_receipt': 'fixture rejected',
                'scope': 'engineering checks only; no semantics, API-cost or latency evidence'}
    output.mkdir(parents=True)
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    summary = []
    for scope in ('partial', 'full', 'pressure'):
        for recovery in ('selective', 'restart'):
            for guard in (False, True):
                result = await run(scope, recovery, guard)
                name = f'{scope}-{recovery}-guard{int(guard)}'
                (output / (name + '.json')).write_text(json.dumps(result, indent=2))
                summary.append({k: result[k] for k in ('scope', 'recovery', 'foreground_guard',
                                                     'checks', 'simulated_tokens', 'outcomes')})
    (output / 'summary.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps({'conditions': len(summary), 'all_checks_pass': all(all(r['checks'].values()) for r in summary)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(main(parser.parse_args().output))
