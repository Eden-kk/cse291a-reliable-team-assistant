"""Seen-case role-summary intervention with paired recovery; never a formal run."""
import argparse
import asyncio
import copy
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_triton_workflow import account, write
from team_assistant.cloudops import Snapshot
from team_assistant.contracts import check_diagnosis
from team_assistant.dispatch import Budget, Dispatcher
from team_assistant.investigation import Investigation, DIAGNOSE
from team_assistant.memory import PolicyMemory, digest
from team_assistant.propagation import fork
from team_assistant.runtime import Runtime
from team_assistant.triton_provider import TritonProvider
ROOT = Path(__file__).resolve().parents[1]
CASE = 'boutique/runtime/1'
SUMMARY = '''Summarize adservice configuration from the supplied raw observations.
Return {"service":"adservice","liveness_protocol":"httpGet|grpc|tcpSocket|unknown",
"readiness_protocol":"httpGet|grpc|tcpSocket|unknown","summary":"brief factual explanation"}.
Use the actual livenessProbe and readinessProbe fields, not service protocol guesses.'''
BRIEF = DIAGNOSE + '''
Return a final diagnosis, never supplementary queries. Also include
"adservice_liveness_protocol":"httpGet|grpc|tcpSocket|unknown" in your JSON.
The role summary is fallible; all original raw evidence is also supplied.
Resolve conflicts from raw evidence and acknowledge any conflicting summary in reasoning.'''


def build(vendor, registry, commit, backend):
    memory = PolicyMemory(); policy = {'rules': []}
    memory.promote('oncall', 'baseline', policy, {'accepted': True, 'sha256': digest(policy)})
    return Investigation(Runtime(memory, Dispatcher(Budget(12 * 66144), concurrency=1)),
                         backend, Snapshot(vendor, CASE, registry, commit),
                         reservation=66144, max_tools=24, diagnosis_rounds=1)


def clone(inv, backend):
    result = fork(inv, backend)
    result.runtime.dispatcher = Dispatcher(Budget(12 * 66144), concurrency=1)
    return result


async def summarize(inv):
    # A fixed extra read, selected before inference, makes the intervention eligible.
    obs = inv.execute_queries([{'tool': 'GetAppYAML', 'arguments': {'app_name': 'adservice'}}])
    if len(obs) != 1 or 'livenessProbe:' not in obs[0]['text']:
        raise ValueError('Fixed source fact unavailable')
    probe = obs[0]['text'].split('livenessProbe:', 1)[1].split('---', 1)[0]
    if 'httpGet:' not in probe or 'grpc:' in probe or 'tcpSocket:' in probe:
        raise ValueError('Fixed source field changed; stop rather than retarget')
    inv.artifact('summary_source', {'observations': obs})
    return await inv.model_step('configuration_summary', SUMMARY, ('summary_source',), False)


async def brief(inv):
    deps = ('initial', 'configuration_evidence', 'telemetry_evidence',
            'summary_source', 'configuration_summary')
    result = await inv.model_step('incident_brief', BRIEF, deps, False)
    answer = result.payload
    observations = inv.active_observations(deps)
    return {'answer': answer, 'contract_errors': check_diagnosis(answer, observations, inv.targets, inv.objects),
            'observations': observations, 'quality_approved': False,
            'fact_field_valid': answer.get('adservice_liveness_protocol') in ('httpGet', 'grpc', 'tcpSocket', 'unknown')}


def inject(inv):
    original = inv.incident.store.results['configuration_summary']
    if original.payload.get('liveness_protocol') != 'httpGet':
        return {'injected': False, 'reason': 'clean summary did not extract fixed source field; no retargeting'}
    wrong = copy.deepcopy(original)
    wrong.version += 1
    wrong.payload['liveness_protocol'] = 'grpc'
    wrong.payload['summary'] = 'adservice livenessProbe uses grpc on port 9555, matching readinessProbe.'
    inv.incident.store.commit(wrong, inv.incident.store.epoch)
    return {'injected': True, 'artifact': 'configuration_summary', 'field': 'liveness_protocol',
            'before': 'httpGet', 'after': 'grpc', 'raw_sources_unchanged': True,
            'category': 'constructed incorrect specialist summary, not dataset ground truth'}


async def experiment(out, vendor, registry, commit, backend):
    assert CASE in registry['splits']['development_seen'] and not registry['final_unlocked']
    outcomes = {name: {'state': 'not_run'} for name in ('prefix', 'clean', 'corrupt', 'no-recovery', 'restart', 'selective')}
    async def run(name, inv, operation, supersedes=None):
        start = len(backend.records)
        wall_start, cpu_start = time.perf_counter(), time.process_time()
        try:
            result = await operation()
            if result is not None:
                write(out/name/'result.json', result)
                inv.incident.store.publish('incident_brief', inv.incident.store.epoch, supersedes)
            outcomes[name] = {'state': 'completed', 'result': result}
        except Exception as exc:
            outcomes[name] = {'state': 'failed', 'error_type': type(exc).__name__}
        finally:
            await inv.runtime.dispatcher.join()
            outcomes[name]['calls'] = list(range(start + 1, len(backend.records) + 1))
            outcomes[name]['wall_seconds'] = time.perf_counter() - wall_start
            outcomes[name]['process_cpu_seconds'] = time.process_time() - cpu_start
            write(out/name/'state.json', {k: asdict(v) for k, v in inv.incident.store.results.items()})
            write(out/name/'ledger.json', inv.incident.store.ledger)
            write(out/name/'trace.json', inv.runtime.events)
            write(out/'summary.json', {'case': CASE, 'formal_evaluation': False, 'arms': outcomes,
                                       'calls': len(backend.records), 'provider_halted': backend.halted})
        return outcomes[name]['state'] == 'completed' and not backend.halted
    base = build(vendor, registry, commit, backend)
    async def prefix():
        await base.collect(); await summarize(base)
    if not await run('prefix', base, prefix): return
    frozen = copy.deepcopy(base.incident.store.__dict__)
    clean = clone(base, backend)
    if not await run('clean', clean, lambda: brief(clean)): return
    bad = clone(base, backend)
    raw_before = digest(bad.snapshot.observations)
    prefix_hashes = {n: digest(json.loads(json.dumps(asdict(v), default=sorted))) for n, v in base.incident.store.results.items()}
    write(out/'prefix-hashes.json', prefix_hashes)
    fault = inject(bad); write(out/'fault.json', fault)
    assert digest(bad.snapshot.observations) == raw_before
    if not fault['injected']:
        for name in ('corrupt', 'no-recovery', 'restart', 'selective'):
            outcomes[name] = {'state': 'not_injected', 'calls': []}
    else:
        if not await run('corrupt', bad, lambda: brief(bad)): return
        tracking_start, tracking_cpu = time.perf_counter(), time.process_time()
        invalid = bad.incident.store.invalidate(['configuration_summary'])
        tracking = {'invalidation_wall_seconds': time.perf_counter() - tracking_start,
                    'invalidation_cpu_seconds': time.process_time() - tracking_cpu}
        event = {'artifact': 'configuration_summary', 'reason': 'trusted notification: summary producer output invalid',
                 'correct_value_supplied': False, 'invalid': sorted(invalid), 'tracking': tracking}
        write(out/'notification.json', event)
        try: bad.incident.store.publish('incident_brief', bad.incident.store.epoch)
        except ValueError: rejected = True
        else: raise AssertionError('invalid publication accepted')
        outcomes['no-recovery'] = {'state': 'invalid', 'calls': [], 'retained_answer': outcomes['corrupt']['result']['answer'],
                                   'stale_republication_rejected': rejected}
        frozen_bad = copy.deepcopy(bad.incident.store.__dict__)
        for method in ('restart', 'selective'):
            inv = clone(bad, backend)
            before = copy.deepcopy(inv.incident.store.results)
            async def recover():
                if method == 'restart':
                    inv.runtime.restart(inv.identifier, event)
                    inv.tool_budget_start = len(inv.snapshot.observations) + len(inv.tool_errors)
                    await inv.collect()
                await summarize(inv)
                return await brief(inv)
            if not await run(method, inv, recover, supersedes=1): return
            preserved = [n for n, v in before.items() if inv.incident.store.results.get(n) == v and n not in inv.incident.store.invalid()]
            write(out/method/'reuse.json', {'valid_unchanged_artifacts': preserved,
                                          'invalid_artifacts': sorted(inv.incident.store.invalid()), 'same_notification': event,
                                          'before_hashes': {n: digest(json.loads(json.dumps(asdict(v), default=sorted))) for n, v in before.items()},
                                          'after_hashes': {n: digest(json.loads(json.dumps(asdict(v), default=sorted))) for n, v in inv.incident.store.results.items()}})
            assert bad.incident.store.__dict__ == frozen_bad
        def value(arm): return outcomes[arm]['result']['answer'].get('adservice_liveness_protocol')
        outcomes['checks'] = {'clean_protocol': value('clean'), 'corrupt_protocol': value('corrupt'),
                              'wrong_fact_adopted': value('corrupt') == 'grpc',
                              'restart_correct_fact': value('restart') == 'httpGet',
                              'selective_correct_fact': value('selective') == 'httpGet',
                              'semantic_recovery_demonstrated': value('corrupt') == 'grpc' and value('selective') == 'httpGet',
                              'raw_evidence_available_to_all_arms': True,
                              'clean_end_to_end_wall_seconds': outcomes['prefix']['wall_seconds'] + outcomes['clean']['wall_seconds'],
                              'source_fact_basis': 'raw adservice YAML livenessProbe.httpGet; no native label read'}
    assert base.incident.store.__dict__ == frozen
    assert len(backend.records) <= 11
    write(out/'summary.json', {'case': CASE, 'formal_evaluation': False, 'arms': outcomes,
                               'calls': len(backend.records), 'provider_halted': backend.halted})


async def main(args):
    spec = json.loads((ROOT/'configs/causal-summary-dev-v1.json').read_text())
    assert spec['case'] == CASE and spec['max_calls'] == 12 and spec['maximum_usd'] == '1.20'
    args.output.mkdir(parents=True, exist_ok=False)
    write(args.output/'protocol.json', spec)
    before = account(); write(args.output/'account-before.json', before)
    assert before['max_budget'] - before['spend'] >= 1.20
    assert datetime.fromisoformat(before['expires']) > datetime.now(timezone.utc)
    backend = TritonProvider(args.output/'calls', max_calls=12, maximum_usd='1.20')
    try:
        await experiment(args.output, args.vendor, json.loads((ROOT/'configs/splits.json').read_text()), args.commit, backend)
    finally:
        write(args.output/'usage.json', {'calls': backend.records, 'halted': backend.halted,
                                       'conservative_spend_usd': str(backend.spent_usd)})
        try: write(args.output/'account-after.json', account())
        except Exception: write(args.output/'account-after.json', {'unavailable': True})

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--vendor', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    p.add_argument('--commit', required=True)
    asyncio.run(main(p.parse_args()))
