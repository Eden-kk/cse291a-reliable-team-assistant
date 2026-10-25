"""Bounded v3 service/2 handoff pilot; no seeded-state inference is allocated."""
import argparse
import asyncio
import copy
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_causal_summary_dev import clone, SUMMARY, ROOT
from team_assistant.cloudops import Snapshot
from team_assistant.dispatch import Budget, Dispatcher
from team_assistant.investigation import Investigation
from team_assistant.memory import PolicyMemory
from team_assistant.runtime import Runtime
from handoff_v3_contract import TIMING_CONTRACT, inspect_schema, inject_categorical
CASE = 'boutique/service/2'


def build(vendor, registry, commit, backend):
    memory = PolicyMemory(); policy = {'rules': []}
    memory.promote('oncall', 'baseline', policy, {'accepted': True, 'sha256': digest(policy)})
    return Investigation(Runtime(memory, Dispatcher(Budget(12 * 66144), concurrency=1)),
                         backend, Snapshot(vendor, CASE, registry, commit),
                         reservation=48192, max_tools=24, diagnosis_rounds=1)

from run_triton_workflow import account, write
from team_assistant.contracts import check_diagnosis
from team_assistant.memory import digest
from team_assistant.triton_provider import TritonProvider

CONFIGURATION = SUMMARY + '\nAlso include source_ids listing exact observation IDs inspected.'
TELEMETRY = '''Summarize observed symptoms, chronology, and telemetry for the coordinator.
Return {"summary":"concise factual findings and uncertainty", "source_ids":["observation IDs"]}.
Distinguish observed facts from hypotheses. Cite exact available IDs; never invent evidence.'''
COORDINATE = '''You are an incident coordinator receiving specialist handoffs.
Use supplied summaries and available sources to investigate; specialist claims are not proof.
If raw evidence is needed, return {"retrieve_source_ids":["ID", ...]} (at most three IDs).
Only one retrieval round is available. Otherwise return a final JSON object with
"status":"diagnosed" or "uncertain", "fault_object":"app/NAME or node/NAME or namespace/NAME",
"root_cause":"exact allowed label", "evidence":[{"observation_id":"ID","quote":"literal raw substring"}],
"reasoning":"causal explanation", "adservice_liveness_protocol":"httpGet|grpc|tcpSocket|unknown".
Do not invent raw quotations from summaries. If evidence is insufficient, return uncertain.
The source catalog lists retrievable original observations. Cite only raw records actually inspected.'''
COORDINATE += '\n' + TIMING_CONTRACT
CONFIGURATION += '\n' + TIMING_CONTRACT
TELEMETRY += '\n' + TIMING_CONTRACT
RAW_NAMES = ('initial', 'configuration_evidence', 'telemetry_evidence', 'summary_source')
HANDOFF_NAMES = ('configuration_summary', 'telemetry_summary', 'source_catalog')


def hashes(inv):
    return {n: digest(json.loads(json.dumps(asdict(v), default=sorted)))
            for n, v in inv.incident.store.results.items()}


async def summarize_configuration(inv, reuse=False):
    if not reuse:
        obs = inv.execute_queries([{'tool': 'GetAppYAML', 'arguments': {'app_name': 'adservice'}}])
        if len(obs) != 1 or not inspect_schema(obs[0])['eligible']:
            raise ValueError('Fixed categorical source absent; no retarget')
        inv.artifact('summary_source', {'observations': obs})
    await inv.model_step('configuration_summary', CONFIGURATION, ('summary_source',), False)


async def collect_handoffs(inv):
    await inv.collect()
    await summarize_configuration(inv)
    await inv.model_step('telemetry_summary', TELEMETRY, ('initial', 'telemetry_evidence'), False)
    catalog = [{'id': o['id'], 'tool': o.get('tool'), 'arguments': o.get('arguments')}
               for o in inv.active_observations(RAW_NAMES)]
    inv.artifact('source_catalog', {'sources': catalog}, RAW_NAMES)


async def coordinate(inv, raw_visible=False):
    deps = list(HANDOFF_NAMES) + (list(RAW_NAMES) if raw_visible else [])
    instruction = COORDINATE + ('\nAll raw sources are supplied; return final now.' if raw_visible else '')
    first = await inv.model_step('coordinator_decision', instruction, tuple(deps), False)
    requested = first.payload.get('retrieve_source_ids')
    inspected = inv.active_observations(RAW_NAMES) if raw_visible else []
    if requested is not None:
        if raw_visible or not isinstance(requested, list) or not 1 <= len(requested) <= 3 or any(not isinstance(i, str) for i in requested):
            raise ValueError('Invalid retrieval request')
        sources = {o['id']: o for o in inv.active_observations(RAW_NAMES)}
        if any(i not in sources for i in requested):
            raise ValueError('Unknown requested source; no substitution')
        inspected = [sources[i] for i in dict.fromkeys(requested)]
        owners = [n for n in RAW_NAMES if any(o['id'] in requested for o in inv.active_observations((n,)))]
        inv.artifact('retrieved_sources', {'observations': inspected}, ('coordinator_decision', *owners))
        deps += ['coordinator_decision', 'retrieved_sources']
        final = await inv.model_step('incident_brief', COORDINATE + '\nRetrieval exhausted. Return final diagnosis or uncertain; do not request more.', tuple(deps), False)
        if 'retrieve_source_ids' in final.payload:
            raise ValueError('Retrieval round limit exceeded')
    else:
        # Publishing the already produced final answer uses no additional inference.
        inv.artifact('incident_brief', first.payload, ('coordinator_decision',))
        final = inv.incident.store.results['incident_brief']
    return {'answer': final.payload, 'inspected_raw': inspected, 'retrieved_ids': requested or [],
            'contract_errors': check_diagnosis(final.payload, inspected, inv.targets, inv.objects),
            'quality_approved': False, 'raw_visible': raw_visible,
            'logical_tokens_spent': inv.runtime.dispatcher.budget.spent}


async def experiment(out, vendor, registry, commit, backend):
    assert CASE in registry['splits']['development_seen'] and not registry['final_unlocked']
    names = ('prefix', 'clean', 'raw-visible-corrupt', 'corrupt', 'no-recovery', 'restart', 'selective')
    all_repeats = [{'repeat': n + 1,
                    'recovery_order': ('restart', 'selective') if n % 2 == 0 else ('selective', 'restart'),
                    'arms': {name: {'state': 'not_run', 'calls': []} for name in names}} for n in range(2)]
    def save_summary():
        write(out/'summary.json', {'case': CASE, 'formal_evaluation': False, 'repeats': all_repeats,
                                  'calls': len(backend.records), 'halted': backend.halted})
    save_summary()
    preflight = build(vendor, registry, commit, backend)
    source = preflight.snapshot.query('GetAppYAML', {'app_name': 'adservice'})
    eligibility = inspect_schema(source)
    write(out/'schema-preflight.json', {'eligibility': eligibility, 'observation': source})
    if not eligibility['eligible']:
        for rep in all_repeats:
            for arm in rep['arms'].values(): arm['state'] = 'schema_ineligible'
        save_summary()
        return
    for repeat in range(2):
        dest = out / f'repeat-{repeat + 1}'
        arms = all_repeats[repeat]['arms']
        order = all_repeats[repeat]['recovery_order']
        async def run(name, inv, operation, supersedes=None):
            start = len(backend.records)
            wall, cpu = time.perf_counter(), time.process_time()
            try:
                result = await operation()
                if result is not None:
                    inv.incident.store.publish('incident_brief', inv.incident.store.epoch, supersedes)
                arms[name] = {'state': 'completed', 'result': result}
            except Exception as exc:
                arms[name] = {'state': 'failed', 'error_type': type(exc).__name__}
            finally:
                await inv.runtime.dispatcher.join()
                arms[name].update(calls=list(range(start + 1, len(backend.records) + 1)),
                                  wall_seconds=time.perf_counter() - wall, process_cpu_seconds=time.process_time() - cpu)
                write(dest/name/'state.json', {k: asdict(v) for k, v in inv.incident.store.results.items()})
                write(dest/name/'ledger.json', inv.incident.store.ledger)
                write(dest/name/'trace.json', inv.runtime.events)
                write(dest/name/'result.json', arms[name])
                write(dest/'summary.json', {'arms': arms, 'repeat': repeat + 1})
                save_summary()
            return arms[name]['state'] == 'completed' and not backend.halted
        base = build(vendor, registry, commit, backend)
        base.reservation = 48192
        if not await run('prefix', base, lambda: collect_handoffs(base)): break
        base_frozen = copy.deepcopy(base.incident.store.__dict__)
        write(dest/'prefix-hashes.json', hashes(base))
        clean = clone(base, backend)
        if not await run('clean', clean, lambda: coordinate(clean)): break
        bad = clone(base, backend)
        raw_hash = digest(bad.snapshot.observations)
        assert bad.incident.store.results['summary_source'].payload['observations'][0]['sha256'] == eligibility['source_sha256']
        fault = inject_categorical(bad, eligibility); write(dest/'fault.json', fault)
        assert raw_hash == digest(bad.snapshot.observations)
        if not fault['injected']:
            for name in ('raw-visible-corrupt', 'corrupt', 'no-recovery', 'restart', 'selective'):
                arms[name] = {'state': 'not_injected', 'calls': []}
            save_summary()
            write(dest/'summary.json', {'arms': arms, 'repeat': repeat + 1})
            continue
        raw_baseline = clone(bad, backend)
        if not await run('raw-visible-corrupt', raw_baseline, lambda: coordinate(raw_baseline, True)): break
        if not await run('corrupt', bad, lambda: coordinate(bad)): break
        wall, cpu = time.perf_counter(), time.process_time()
        invalid = bad.incident.store.invalidate(['configuration_summary'])
        event = {'artifact': 'configuration_summary', 'reason': 'trusted producer invalidation',
                 'correct_value_supplied': False, 'invalidated': sorted(invalid),
                 'tracking_wall_seconds': time.perf_counter() - wall, 'tracking_cpu_seconds': time.process_time() - cpu}
        write(dest/'notification.json', event)
        try: bad.incident.store.publish('incident_brief', bad.incident.store.epoch)
        except ValueError: pass
        else: raise AssertionError('Stale publication accepted')
        arms['no-recovery'] = {'state': 'invalid', 'stale_republication_rejected': True,
                               'answer': arms['corrupt']['result']['answer'], 'calls': []}
        frozen_bad = copy.deepcopy(bad.incident.store.__dict__)
        for method in order:
            recovery_started = time.perf_counter()
            inv = clone(bad, backend); before = hashes(inv)
            async def recover():
                if method == 'restart':
                    inv.runtime.restart(inv.identifier, event)
                    inv.tool_budget_start = len(inv.snapshot.observations) + len(inv.tool_errors)
                    await collect_handoffs(inv)
                else:
                    await summarize_configuration(inv, reuse=True)
                return await coordinate(inv)
            if not await run(method, inv, recover, 1): break
            after = hashes(inv)
            write(dest/method/'reuse.json', {'before_hashes': before, 'after_hashes': after,
                  'valid_unchanged_artifacts': [n for n in before if before[n] == after.get(n) and n not in inv.incident.store.invalid()],
                  'invalid_after': sorted(inv.incident.store.invalid()), 'same_notification': event})
            write(dest/method/'end-to-end-clock.json', {
                'wall_seconds': time.perf_counter() - recovery_started + event['tracking_wall_seconds'],
                'scope': 'shared invalidation + branch clone/hashes + recovery including model/tools/validation/publication and evidence persistence; excludes other arm and final clock write',
                'notification_arrival': 'synthetic immediate trusted notification; no external delivery latency measured'})
            assert bad.incident.store.__dict__ == frozen_bad
        assert base.incident.store.__dict__ == base_frozen
        if backend.halted or any(a['state'] == 'failed' for a in arms.values()): break
    assert len(backend.records) <= 36
    save_summary()


async def main(args):
    spec = json.loads((ROOT/'configs/handoff-dev-v3.json').read_text())
    assert spec['max_calls'] == 36 and spec['maximum_usd'] == '2.70' and spec['input_byte_cap'] == 40000 and spec['repeats'] == 2
    if args.seeded_state:
        raise ValueError('Seeded conditional recovery needs a separate budget and protocol; zero extra calls allowed')
    args.output.mkdir(parents=True, exist_ok=False)
    write(args.output/'protocol.json', spec)
    write(args.output/'execution.json', {'json_object': args.json_object, 'code_commit': args.commit, 'config_sha256': digest(spec)})
    before = account(); write(args.output/'account-before.json', before)
    assert before['max_budget'] - before['spend'] >= 2.70
    assert datetime.fromisoformat(before['expires']) > datetime.now(timezone.utc)
    backend = TritonProvider(args.output/'calls', max_calls=36, maximum_usd='2.70', input_byte_cap=40000,json_object=args.json_object)
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
    p.add_argument('--json-object', action='store_true')
    p.add_argument('--seeded-state', action='store_true', help='Unallocated; fails closed before account or inference')
    asyncio.run(main(p.parse_args()))
