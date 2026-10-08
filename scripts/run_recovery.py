"""One seen development incident: wrong claim, selective repair and fresh restart."""
import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from team_assistant.cloudops import Snapshot
from team_assistant.dispatch import Budget, Dispatcher
from team_assistant.faults import inject_claim
from team_assistant.investigation import Investigation
from team_assistant.memory import PolicyMemory, digest
from team_assistant.provider import CodexProvider
from team_assistant.runtime import Runtime

ROOT = Path(__file__).resolve().parents[1]


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2))


async def main(output):
    if output.exists():
        raise ValueError('Refuse to overwrite experiment')
    output.mkdir(parents=True)
    registry = json.loads((ROOT / 'configs/splits.json').read_text())
    case = 'boutique/runtime/1'
    if case not in registry['splits']['development_seen']:
        raise ValueError('Only seen development case allowed')
    commit = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
    vendor = ROOT.parent / 'preparation/291a-team-agent/vendor/Cloud-OpsBench'
    manifest = {'case': case, 'split': 'development_seen', 'code_commit': commit,
                'dataset_commit': registry['dataset_commit'], 'model': 'gpt-6-astra',
                'reservation': 60000, 'token_limit': 360000,
                'comparison': 'shared physical fault prefix; restart gets same question and fault identity, no diagnoses',
                'semantic_propagation': 'must score after inference; exposure alone is not propagation'}
    def build(directory):
        snapshot = Snapshot(vendor, case, registry, commit)
        memory = PolicyMemory()
        policy = {'rules': []}
        memory.promote('oncall', 'v1', policy, {'accepted': True, 'sha256': digest(policy)})
        runtime = Runtime(memory, Dispatcher(Budget(360000)))
        provider = CodexProvider(directory / 'calls')
        write(directory / 'manifest.json', manifest)
        return Investigation(runtime, provider, snapshot)
    investigation = build(output / 'fault-prefix')
    phases = {'fault-prefix': investigation.runtime.dispatcher}
    try:
        await investigation.collect()
        fault = inject_claim(investigation, 'configuration_evidence',
                             {'fault_object': 'app/adservice', 'root_cause': 'pod_cpu_overload',
                              'reasoning': 'Adservice CPU throttling is the cause of the application restarts.'})
        write(output / 'fault.json', fault)
        prefix = await investigation.diagnose()
        write(output / 'fault-prefix/result.json', prefix)
        store = investigation.incident.store
        before = {k: v.version for k, v in store.results.items()}
        invalid = sorted(store.invalidate(['configuration_evidence']))
        old_spent = investigation.runtime.dispatcher.budget.spent
        old_tool_count = len(investigation.snapshot.observations)
        investigation.runtime.dispatcher = Dispatcher(Budget(360000))
        phases['selective'] = investigation.runtime.dispatcher
        investigation.provider = CodexProvider(output / 'selective/calls')
        write(output / 'selective/manifest.json', manifest)
        selective = await investigation.run(reuse=True)
        write(output / 'selective/result.json', selective)
        selective_cost = selective['budget']['spent']
        retained = [k for k, version in before.items() if k not in invalid and store.results[k].version == version]
        restart = build(output / 'restart')
        phases['restart'] = restart.runtime.dispatcher
        fresh = await restart.run()
        write(output / 'restart/result.json', fresh)
        summary = {'fault': fault, 'invalidated': invalid, 'retained_valid_artifacts': retained,
                   'prefix_tokens': old_spent, 'recovery_ceiling_equal': {'tokens_each': 360000, 'tools_each': 12},
                   'selective_end_to_end_tokens': old_spent + selective_cost,
                   'restart_end_to_end_tokens': old_spent + fresh['budget']['spent'], 'selective_incremental_tokens': selective_cost,
                   'restart_incremental_tokens': fresh['budget']['spent'],
                   'selective_model_calls': len(selective['model_calls']), 'restart_model_calls': len(fresh['model_calls']),
                   'selective_new_tools': len(selective['observation_archive']) - old_tool_count,
                   'restart_new_tools': len(fresh['observations']),
                   'prefix_answer': prefix['answer'], 'selective_answer': selective['answer'], 'restart_answer': fresh['answer'],
                   'valid_contracts': {'prefix': not prefix['contract_errors'], 'selective': not selective['contract_errors'], 'restart': not fresh['contract_errors']},
                   'warning': 'If prefix diagnosis already correct, this is contaminated-artifact repair, not recovery of an incorrect diagnosis. One seen case is not generalization.'}
        write(output / 'summary.json', summary)
        print(json.dumps(summary, indent=2))
    except Exception as exc:
        for dispatcher in phases.values():
            await dispatcher.join()
        write(output / 'failure.json', {'error': repr(exc),
              'phases': {name: {'spent': dispatcher.budget.spent, 'reserved': dispatcher.budget.reserved,
                                'events': dispatcher.events} for name, dispatcher in phases.items()}})
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(main(parser.parse_args().output))
