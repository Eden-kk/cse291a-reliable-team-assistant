"""Live development integration: two revoked consumers and one new incident."""
import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from team_assistant.cloudops import Snapshot
from team_assistant.dispatch import Budget, Dispatcher
from team_assistant.investigation import Investigation
from team_assistant.memory import PolicyMemory, digest
from team_assistant.provider import CodexProvider
from team_assistant.runtime import Runtime

ROOT = Path(__file__).resolve().parents[1]


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2))


async def main(output, scheduling):
    if output.exists():
        raise ValueError('Refuse to overwrite window')
    output.mkdir(parents=True)
    registry = json.loads((ROOT / 'configs/splits.json').read_text())
    commit = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
    vendor = ROOT.parent / 'preparation/291a-team-agent/vendor/Cloud-OpsBench'
    dispatcher = Dispatcher(Budget(360000), concurrency=2, policy=scheduling)
    memory = PolicyMemory()
    initial_policy = {'rules': []}
    memory.promote('oncall', 'v1', initial_policy, {'accepted': True, 'sha256': digest(initial_policy)})
    runtime = Runtime(memory, dispatcher)
    investigations = {}
    manifest = {'split': 'development_seen', 'code_commit': commit, 'dataset_commit': registry['dataset_commit'],
                'scheduler': scheduling, 'concurrency': 2, 'shared_token_limit': 360000,
                'response_slo_seconds': 30, 'recovery_slo_seconds': 60,
                'kind': 'live integration with externally triggered policy revocation; no claim v1 caused wrong diagnoses'}
    write(output / 'manifest.json', manifest)
    def build(group, case):
        if case not in registry['splits']['development_seen']:
            raise ValueError('Only seen development incidents permitted')
        snapshot = Snapshot(vendor, case, registry, commit)
        inv = Investigation(runtime, CodexProvider(output / group / 'calls'), snapshot, identifier=group)
        write(output / group / 'manifest.json', {**manifest, 'case': case})
        investigations[group] = inv
        return inv
    a, b = build('A', 'boutique/runtime/1'), build('B', 'boutique/service/1')
    try:
        prefix_results = await asyncio.gather(a.collect(), b.collect(), return_exceptions=True)
        for result in prefix_results:
            if isinstance(result, BaseException):
                raise result
        prefix_spent = dispatcher.budget.spent
        policy = json.loads((ROOT / 'configs/historical-candidate.json').read_text())
        memory.promote('oncall', 'v2', policy, {'accepted': True, 'sha256': digest(policy)})
        c = build('C', 'boutique/performance/1')
        begun = time.monotonic()
        affected = runtime.revoke('oncall', 'v1')
        for inv in (a, b):
            memory.rebind(inv.identifier)
            inv.kind = 'recovery'
            inv.deadline = begun + 60
        c.deadline = begun + 30
        async def finish(inv, reuse):
            try:
                result = await inv.run(reuse=reuse)
                result['completed_monotonic'] = time.monotonic()
                result['window_latency_seconds'] = result['completed_monotonic'] - begun
                result['incident_tokens'] = sum(record['tokens'] for record in inv.provider.records if record['tokens'] is not None)
                write(output / inv.identifier / 'result.json', result)
                return {'group': inv.identifier, 'seconds': result['window_latency_seconds'],
                        'contract_valid': not result['contract_errors'], 'state': 'completed',
                        'slo_met_contract_only': not result['contract_errors'] and result['window_latency_seconds'] <= (30 if inv.identifier == 'C' else 60)}
            except Exception as exc:
                write(output / inv.identifier / 'failure.json', {'error': repr(exc)})
                return {'group': inv.identifier, 'state': 'failed', 'error': repr(exc), 'slo_met_contract_only': False}
        outcomes = await asyncio.gather(finish(a, True), finish(b, True), finish(c, False))
        await dispatcher.join()
        summary = {'window_origin_monotonic': begun, 'outcomes': outcomes, 'affected': affected, 'prefix_tokens': prefix_spent,
                   'total_tokens': dispatcher.budget.spent, 'post_revocation_tokens': dispatcher.budget.spent - prefix_spent,
                   'peak_concurrency': max(e['active'] for e in dispatcher.events if e['event'] == 'start'),
                   'initial_versions': {i: inv.incident.store.results['initial'].version for i, inv in investigations.items()},
                   'policies': {i: memory.runs[i]['version'] for i in investigations},
                   'dispatch_events': dispatcher.events, 'settlements': dispatcher.budget.settlements,
                   'quality_scoring': 'pending separate native/semantic scoring; contract-valid latency alone is not useful-response latency',
                   'publication': 'No external messages or publication ledger exercised in this experiment'}
        write(output / 'summary.json', summary)
        print(json.dumps({k: v for k, v in summary.items() if k not in ('dispatch_events', 'settlements')}, indent=2))
    except Exception as exc:
        await dispatcher.join()
        write(output / 'failure.json', {'error': repr(exc), 'spent': dispatcher.budget.spent, 'events': dispatcher.events})
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--scheduler', choices=['fair', 'fifo'], default='fair')
    args = parser.parse_args()
    asyncio.run(main(args.output, args.scheduler))
