"""Predeclared single-case Phase 1 evidence-corruption pilot; labels scored separately."""
import argparse
import asyncio
from dataclasses import asdict
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
from team_assistant.propagation import IntegrityInvestigation, corrupt_port, fork
from team_assistant.provider import CodexProvider
from team_assistant.runtime import Runtime

ROOT = Path(__file__).resolve().parents[1]


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, default=list))


async def main(output, preventive_only=False):
    if output.exists():
        raise ValueError('Refuse overwrite')
    output.mkdir(parents=True)
    if subprocess.check_output(['git', '-C', str(ROOT), 'status', '--porcelain'], text=True).strip():
        raise ValueError('Commit experimental code before live inference')
    registry = json.loads((ROOT / 'configs/splits.json').read_text())
    commit = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
    case = 'boutique/runtime/1'
    manifest = {'case': case, 'split': 'development_seen', 'code_commit': commit, 'dirty': False,
                'dataset_commit': registry['dataset_commit'], 'model': 'gpt-6-astra',
                'reservation': 60000, 'initial_token_limit': 360000, 'recovery_token_limit': 360000,
                'fault': 'all 9555->9556 in configuration_evidence; no adaptive retargeting',
                'tools_per_phase': 12, 'publication': 'none; no real operational writes',
                'verification': 'source integrity only, not semantic always-verify'}
    write(output / 'manifest.json', manifest)
    phases = {}
    def build(name, verify=False):
        snapshot = Snapshot(ROOT.parent / 'preparation/291a-team-agent/vendor/Cloud-OpsBench', case, registry, commit)
        memory = PolicyMemory()
        policy = {'rules': []}
        memory.promote('oncall', 'v1', policy, {'accepted': True, 'sha256': digest(policy)})
        cls = IntegrityInvestigation if verify else Investigation
        inv = cls(Runtime(memory, Dispatcher(Budget(360000))), CodexProvider(output / name / 'calls'), snapshot)
        inv.integrity_audits = []
        phases[name] = inv
        write(output / name / 'manifest.json', manifest)
        return inv
    def branch(base, name):
        inv = fork(base, CodexProvider(output / name / 'calls'))
        phases[name] = inv
        write(output / name / 'manifest.json', manifest)
        return inv
    async def finish(name, inv, run=False, reuse=False):
        started = time.perf_counter()
        result = await inv.run(reuse=reuse) if run else await inv.diagnose()
        result['phase_seconds'] = time.perf_counter() - started
        result['integrity_audits'] = inv.integrity_audits
        write(output / name / 'result.json', result)
        write(output / name / 'state.json', {k: asdict(v) for k, v in inv.incident.store.results.items()})
        return result
    if preventive_only:
        verified = build('always-verify', verify=True)
        started = time.perf_counter()
        await verified.collect()
        fault = corrupt_port(verified)
        result = await finish('always-verify', verified)
        write(output / 'summary.json', {'state': 'completed' if fault['injected'] else 'not_injected',
              'fault': fault, 'seconds': time.perf_counter() - started,
              'tokens': result['budget']['spent'], 'checks': verified.integrity_audits,
              'limitations': 'Independent collection; compare exact injection exposure before prevention claims'})
        return
    try:
        base = build('collection')
        start = time.perf_counter()
        await base.collect()
        await base.runtime.dispatcher.join()
        collection_seconds = time.perf_counter() - start
        collection_tokens = base.runtime.dispatcher.budget.spent
        collection_calls = len(base.provider.records)
        write(output / 'collection/state.json', {k: asdict(v) for k, v in base.incident.store.results.items()})
        clean = branch(base, 'clean')
        clean.runtime.dispatcher.budget.limit -= collection_tokens
        clean_result = await finish('clean', clean)
        damaged = branch(base, 'damaged')
        damaged.runtime.dispatcher.budget.limit -= collection_tokens
        fault = corrupt_port(damaged)
        write(output / 'fault.json', fault)
        if not fault['injected']:
            write(output / 'summary.json', {'state': 'not_injected', 'fault': fault, 'collection_tokens': collection_tokens})
            return
        prefix = await finish('damaged', damaged)
        prefix_tokens = collection_tokens + prefix['budget']['spent']
        selective = branch(damaged, 'selective')
        before = {k: v.version for k, v in selective.incident.store.results.items()}
        invalid = sorted(selective.incident.store.invalidate([fault['artifact']]))
        selective.kind = 'recovery'
        restored = await finish('selective', selective, run=True, reuse=True)
        retained = [k for k, v in before.items() if k not in invalid and selective.incident.store.results[k].version == v]
        restart = build('restart')
        restart.kind = 'recovery'
        fresh = await finish('restart', restart, run=True)
        verified = build('always-verify', verify=True)
        verify_start = time.perf_counter()
        await verified.collect()
        verify_fault = corrupt_port(verified)
        preventive = await finish('always-verify', verified)
        verify_seconds = time.perf_counter() - verify_start
        def cost(result):
            return {'tokens': result['budget']['spent'], 'calls': len(result['model_calls']),
                    'phase_seconds': result['phase_seconds']}
        summary = {'state': 'completed', 'fault': fault, 'preventive_fault': verify_fault,
                   'collection': {'tokens': collection_tokens, 'calls': collection_calls, 'seconds': collection_seconds},
                   'clean_incremental': cost(clean_result), 'damaged_incremental': cost(prefix),
                   'prefix_tokens': prefix_tokens, 'clean_total_tokens': collection_tokens + clean_result['budget']['spent'],
                   'selective_incremental': cost(restored), 'restart_incremental': cost(fresh),
                   'selective_total_tokens': prefix_tokens + restored['budget']['spent'],
                   'restart_total_tokens': prefix_tokens + fresh['budget']['spent'],
                   'preventive_total_tokens': preventive['budget']['spent'], 'preventive_seconds': verify_seconds,
                   'preventive_checks': verified.integrity_audits,
                   'invalidated': invalid, 'retained_valid': retained,
                   'exposure': any(fault['artifact'] in e.get('inputs', {}) for e in prefix['runtime_events'] if e['event'] == 'request'),
                   'propagation': 'pending post-inference correctness and semantic review; exposure is not failure',
                   'limits': 'one seen case; static trusted-source integrity verifier; preventive prefix independent',
                   'physical_total_tokens': sum(i.runtime.dispatcher.budget.spent for i in phases.values())}
        write(output / 'summary.json', summary)
        print(json.dumps(summary, indent=2))
    except Exception as exc:
        for inv in phases.values():
            await inv.runtime.dispatcher.join()
        write(output / 'failure.json', {'error': repr(exc), 'costs': {n: i.runtime.dispatcher.budget.spent for n, i in phases.items()}})
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--preventive-only', action='store_true')
    args = parser.parse_args()
    asyncio.run(main(args.output, args.preventive_only))
