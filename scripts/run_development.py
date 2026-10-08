"""Bounded development or shakedown run. Oracle scoring is a separate post-inference command."""
import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from team_assistant.cloudops import Snapshot
from team_assistant.dispatch import Budget, Dispatcher
from team_assistant.investigation import Investigation
from team_assistant.memory import PolicyMemory, digest
from team_assistant.provider import CodexProvider
from team_assistant.runtime import Runtime

ROOT = Path(__file__).resolve().parents[1]


async def main(args):
    registry = json.loads((ROOT / 'configs/splits.json').read_text())
    if args.case not in registry['splits'][args.split]:
        raise ValueError('Case is outside the explicitly selected development/shakedown split')
    if args.output.exists():
        raise ValueError('Refuse to overwrite run')
    args.output.mkdir(parents=True)
    commit = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
    vendor = ROOT.parent / 'preparation/291a-team-agent/vendor/Cloud-OpsBench'
    snapshot = Snapshot(vendor, args.case, registry, commit)
    policy = {'rules': ['Treat changed configuration as a hypothesis; inspect actual configuration and runtime evidence before attributing cause.']} if args.policy == 'conditional' else {'rules': []}
    if args.policy_file:
        policy = json.loads(args.policy_file.read_text())
        if not isinstance(policy, dict):
            raise ValueError('Policy must be an object')
    memory = PolicyMemory()
    memory.promote('oncall', 'baseline', policy, {'accepted': True, 'sha256': digest(policy)})
    dispatcher = Dispatcher(Budget(360000), concurrency=2)
    provider = CodexProvider(args.output / 'calls')
    runtime = Runtime(memory, dispatcher)
    manifest = {'case': args.case, 'split': args.split, 'code_commit': commit,
                'dirty': bool(subprocess.check_output(['git', '-C', str(ROOT), 'status', '--porcelain'], text=True)),
                'dataset_commit': registry['dataset_commit'], 'policy': policy, 'policy_origin': 'externally supplied experimental candidate; not newly gate-approved' if args.policy_file else 'fixed development advice, not learned',
                'model': provider.model, 'reservation': 60000, 'token_limit': 360000,
                'reservation_kind': 'conservative development estimate; not enforced provider output ceiling'}
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    try:
        result = await Investigation(runtime, provider, snapshot).run()
    except Exception as exc:
        await dispatcher.join()
        result = {'error': repr(exc), 'budget_spent': dispatcher.budget.spent,
                  'model_calls': provider.records, 'observations': snapshot.observations,
                  'runtime_events': runtime.events, 'dispatch_events': dispatcher.events}
        (args.output / 'result.json').write_text(json.dumps(result, indent=2))
        raise
    (args.output / 'result.json').write_text(json.dumps(result, indent=2))
    print(json.dumps({'answer': result['answer'], 'contract_errors': result['contract_errors'],
                      'budget': result['budget'], 'tool_errors': result['tool_errors']}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--case', default='boutique/runtime/1')
    parser.add_argument('--split', choices=['development_seen', 'shakedown'], default='development_seen')
    parser.add_argument('--policy', choices=['empty', 'conditional'], default='empty')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--policy-file', type=Path)
    asyncio.run(main(parser.parse_args()))
