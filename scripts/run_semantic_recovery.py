"""Bounded development-only same-prefix recovery trial; quality scored afterwards."""
import argparse
import asyncio
import copy
import json
from pathlib import Path
import subprocess
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from team_assistant.cloudops import Snapshot
from team_assistant.dispatch import Budget, Dispatcher
from team_assistant.memory import PolicyMemory, digest
from team_assistant.revision_investigation import RevisionInvestigation
from team_assistant.runtime import Runtime
from run_e5_live_smoke import publish_status
from run_phase2_multigroup import provider, save, write
from run_phase2_revision import recovery_branch, attempts

ROOT = Path(__file__).resolve().parents[1]
SPEC = {'case': 'boutique/runtime/1', 'split': 'development_seen',
        'order': ['restart', 'selective', 'fallback'], 'diagnosis_rounds': 1,
        'call_reservation': 60000, 'worst_case_calls': 15, 'physical_token_cap': 900000,
        'logical_token_cap_including_prefix': 360000, 'max_tools_including_prefix': 24,
        'fault_scope': 'query-fields-v2', 'repeats': 1, 'final_unlocked': False}


PERFORMANCE_SPEC = {**SPEC, "case": "boutique/performance/1",
                    "order": ["selective", "restart", "fallback"]}


def preflight(spec, registry):
    if spec not in (SPEC, PERFORMANCE_SPEC) or spec['case'] not in registry['splits']['development_seen']:
        raise ValueError('Fixed development protocol required')
    if spec['physical_token_cap'] < spec['worst_case_calls'] * spec['call_reservation']:
        raise ValueError('Whole-batch reservation unavailable')


def measured(timings, name, operation):
    wall, cpu = time.perf_counter(), time.process_time()
    try:
        return operation()
    finally:
        timings[name] = {'wall_seconds': time.perf_counter()-wall,
                         'process_cpu_seconds': time.process_time()-cpu}


def source_audit(inv, observations):
    return [{'id': o['id'], **inv.revisions.inspect(o)} for o in observations]


def snapshot_state(inv):
    return copy.deepcopy({'store': inv.incident.store.__dict__, 'sources': inv.revisions.__dict__,
                          'observations': inv.snapshot.observations, 'tool_errors': inv.tool_errors,
                          'spent': inv.runtime.dispatcher.budget.spent})


async def run_arm(path, inv, physical, operation, timings, manifest, prefix_tokens=0):
    write(path/'manifest.json', {**manifest, 'logical_prefix_tokens': prefix_tokens})
    before = physical.spent
    wall = time.perf_counter()
    result = None
    try:
        result = await operation()
        write(path/'result.json', result)
        publish_status(inv, path, 1 if prefix_tokens else None)
        outcome = {'state': 'completed', 'contract_errors': result['contract_errors']}
    except Exception as exc:
        outcome = {'state': 'failed', 'error': repr(exc)}
        write(path/'failure.json', outcome)
    finally:
        await inv.runtime.dispatcher.join()
        elapsed = time.perf_counter()-wall
        save(path, inv)
    budget = inv.runtime.dispatcher.budget
    if budget.reserved or physical.reserved or budget.spent != prefix_tokens + physical.spent-before:
        raise AssertionError('Unsettled or nonconserved accounting')
    if result is not None:
        audit = measured(timings, 'post_run_source_inspection',
                         lambda: source_audit(inv, result['observations']))
        write(path/'source-audit.json', audit)
    outcome.update(wall_seconds=elapsed, logical_total_tokens=budget.spent,
                   physical_incremental_tokens=physical.spent-before,
                   logical_total_tools=attempts(inv), source_rereads=inv.source_rereads,
                   logical_halted=budget.halted, provenance_fallback=inv.provenance_fallback,
                   timings=timings, settlements=budget.settlements,
                   ledger=inv.incident.store.ledger,
                   quality_scoring='pending post-inference native and semantic review')
    write(path/'summary.json', outcome)
    return outcome


async def main(args):
    spec = PERFORMANCE_SPEC if getattr(args, "protocol", "runtime") == "performance" else SPEC
    if args.output.exists():
        raise ValueError('Refuse overwrite')
    if subprocess.check_output(['git', '-C', str(ROOT), 'status', '--porcelain'], text=True).strip():
        raise ValueError('Commit before inference')
    registry = json.loads((ROOT/'configs/splits.json').read_text())
    preflight(spec, registry)
    commit = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
    manifest = {**spec, 'code_commit': commit, 'dirty': False,
                'dataset_commit': registry['dataset_commit'], 'model': 'gpt-6-astra',
                'reasoning_effort': 'low', 'context_window': 131072,
                'fallback_intervention': 'configuration_evidence.complete=false, independent of outcomes',
                'timing_scope': 'sync stage CPU and wall; async model stage wall only, not tracking overhead',
                'publication_scope': 'local provisional only; quality_approved=false, no external messages',
                'latency_limits': 'single fixed order, one case; no counterbalance or general superiority claim',
                'verification_scope': 'independent from-start source consistency, not shared-prefix or semantic oracle',
                'scoring': 'current benchmark labels; consumed-source freshness separately; no retarget or retry'}
    write(args.output/'manifest.json', manifest)
    physical = Budget(spec['physical_token_cap'])
    outcomes = {}
    def checkpoint():
        write(args.output/'summary.json', {'arms': outcomes, 'physical_tokens': physical.spent,
              'physical_halted': physical.halted, 'physical_settlements': physical.settlements})
    def build(name):
        memory = PolicyMemory()
        empty = {'rules': []}
        memory.promote('oncall', 'baseline', empty, {'accepted': True, 'sha256': digest(empty)})
        inv = RevisionInvestigation(Runtime(memory, Dispatcher(Budget(360000))),
            provider(args.output/name, physical), Snapshot(args.vendor, spec['case'], registry, commit),
            identifier='recovery', case=spec['case'], fault_scope=spec['fault_scope'],
            max_tools=24, diagnosis_rounds=1)
        inv.kind = 'recovery'
        return inv
    base = build('prefix')
    outcomes['prefix'] = await run_arm(args.output/'prefix', base, physical, base.run, {}, manifest)
    checkpoint()
    if outcomes['prefix']['state'] == 'completed' and base.originals and not physical.halted:
        historical = json.loads((args.output/'prefix/result.json').read_text())
        timings = {}
        invalid = measured(timings, 'source_advance_and_invalidation', base.advance)
        write(args.output/'transition.json', {'invalid': invalid, 'timings': timings,
                                             'notification': sorted(base.originals)})
        save(args.output/'advanced-prefix', base)
        try:
            base.incident.store.publish('public-status', base.incident.store.epoch, supersedes=1)
        except ValueError as exc:
            stale_publication = {'rejected': True, 'reason': str(exc)}
        else:
            raise AssertionError('Stale publication accepted')
        write(args.output/'no-recovery/publication-check.json', stale_publication)
        frozen = snapshot_state(base)
        prefix_tokens = base.runtime.dispatcher.budget.spent
        # Keep the original answer/evidence; audit against new source heads without inference.
        write(args.output/'no-recovery/manifest.json', manifest)
        write(args.output/'no-recovery/result.json', historical)
        write(args.output/'no-recovery/source-audit.json', source_audit(base, historical['observations']))
        outcomes['no-recovery'] = {'state': 'completed', 'physical_incremental_tokens': 0,
                                  'logical_total_tokens': prefix_tokens, 'logical_total_tools': attempts(base),
                                  'quality_scoring': 'pending current-label score; historical evidence preserved'}
        checkpoint()
        for method in spec['order']:
            if physical.halted:
                outcomes[method] = {'state': 'skipped', 'reason': 'unknown or overrun physical usage'}
                checkpoint()
                continue
            timings = {}
            inv = measured(timings, 'fork_preparation', lambda: recovery_branch(
                base, provider(args.output/method, physical), 360000))
            if snapshot_state(inv) != frozen:
                raise AssertionError('Fork does not match shared settled prefix')
            if method == 'restart':
                measured(timings, 'full_restart_invalidation',
                         lambda: inv.runtime.restart(inv.identifier, {'reason': 'explicit full restart'}))
            elif method == 'fallback':
                inv.incident.store.results['configuration_evidence'].complete = False
            gaps = measured(timings, 'pre_recovery_provenance_check', inv.incident.store.provenance_gaps)
            write(args.output/method/'intervention.json', {'method': method, 'observed_gaps': gaps})
            outcomes[method] = await run_arm(args.output/method, inv, physical,
                lambda: inv.run(reuse=method != 'restart'), timings, manifest, prefix_tokens)
            if (snapshot_state(base) != frozen or
                inv.incident.store.ledger[:len(base.incident.store.ledger)] != base.incident.store.ledger):
                raise AssertionError('Shared prefix or publication history mutated')
            checkpoint()
    else:
        for name in ('no-recovery', *spec['order']):
            outcomes[name] = {'state': 'skipped', 'reason': 'prefix failed or no fixed exposure'}
        checkpoint()
    name = 'source-consistency-from-start'
    if physical.halted:
        outcomes[name] = {'state': 'skipped', 'reason': 'unknown or overrun physical usage'}
    else:
        inv, timings = build(name), {}
        async def prevention():
            await inv.collect()
            if not inv.originals:
                raise ValueError('No fixed exposure; no retarget')
            measured(timings, 'source_advance_and_invalidation', inv.advance)
            measured(timings, 'source_requery_and_refresh_including_tool_work', inv.refresh_inputs)
            return await inv.diagnose()
        outcomes[name] = await run_arm(args.output/name, inv, physical, prevention, timings, manifest)
    if len(physical.settlements) > spec['worst_case_calls']:
        raise AssertionError('Predeclared call bound exceeded')
    checkpoint()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--protocol', choices=('runtime', 'performance'), default='runtime')
    parser.add_argument('--vendor', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(main(parser.parse_args()))
