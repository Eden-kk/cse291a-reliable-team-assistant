"""Single-case trusted revision experiment with common total logical budgets."""
import argparse
import asyncio
import copy
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from team_assistant.cloudops import Snapshot
from team_assistant.dispatch import Budget, Dispatcher
from team_assistant.memory import PolicyMemory, digest
from team_assistant.phase2_budget import PhysicalProvider
from team_assistant.propagation import fork
from team_assistant.provider import CodexProvider
from team_assistant.revision_investigation import RevisionInvestigation, CASES
from team_assistant.runtime import Runtime

ROOT = Path(__file__).resolve().parents[1]


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, default=list))


def attempts(inv):
    return len(inv.snapshot.observations) + len(inv.tool_errors) - inv.tool_budget_start


def recovery_branch(base, provider, token_limit):
    inv = fork(base, provider)
    inv.__class__ = RevisionInvestigation
    inv.revisions = copy.deepcopy(base.revisions)
    inv.originals = copy.deepcopy(base.originals)
    inv.revision_events = copy.deepcopy(base.revision_events)
    inv.runtime.dispatcher.budget = Budget(token_limit)
    inv.runtime.dispatcher.budget.spent = base.runtime.dispatcher.budget.spent
    inv.kind = 'recovery'
    return inv


async def main(args):
    if args.output.exists():
        raise ValueError('Refuse overwrite')
    if subprocess.check_output(['git', '-C', str(ROOT), 'status', '--porcelain'], text=True).strip():
        raise ValueError('Commit code before inference')
    registry = json.loads((ROOT / 'configs/splits.json').read_text())
    if args.case not in registry['splits']['development_seen']:
        raise ValueError('Development cases only')
    commit = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
    physical = Budget(args.physical_tokens)
    arms, summaries = {}, {}
    args.output.mkdir(parents=True)
    manifest = {'case': args.case, 'split': 'development_seen', 'repeat': args.repeat, 'code_commit': commit,
                'dataset_commit': registry['dataset_commit'], 'model': 'gpt-6-astra',
                'total_tokens': args.tokens, 'total_tool_attempts': args.tools,
                'physical_token_cap': args.physical_tokens, 'reservation': 60000,
                'order': args.order, 'transition': 'after shared historical diagnosis; before prevention diagnosis',
                'transform': CASES[args.case], 'scoring': 'score only post-transition answers against current labels',
                'limitations': 'constructed historical configuration, development case, trusted explicit change notification; no write tools'}
    write(args.output / 'manifest.json', manifest)

    def provider(name):
        return PhysicalProvider(CodexProvider(args.output / name / 'calls'), physical)

    def build(name, old=True):
        snapshot = Snapshot(args.vendor, args.case, registry, commit)
        memory = PolicyMemory()
        policy = {'rules': []}
        memory.promote('oncall', 'v1', policy, {'accepted': True, 'sha256': digest(policy)})
        inv = RevisionInvestigation(Runtime(memory, Dispatcher(Budget(args.tokens))),
                                    provider(name), snapshot, case=args.case, max_tools=args.tools)
        inv.old_phase = old
        arms[name] = inv
        write(args.output / name / 'manifest.json', manifest)
        return inv

    async def save_run(name, inv, operation):
        write(args.output / name / 'manifest.json', {**manifest, 'logical_prefix':
              'historical-prefix' if name in ('selective', 'restart') else None,
              'model_calls': 'incremental physical calls; budget.spent includes logical prefix'})
        started = time.perf_counter()
        before = inv.runtime.dispatcher.budget.spent
        try:
            result = await operation()
            result.update(source_events=inv.revision_events,
                          source_heads=inv.revisions.heads, source_rereads=inv.source_rereads,
                          logical_total_tool_attempts=attempts(inv))
            write(args.output / name / 'result.json', result)
            summary = {'state': 'completed'}
        except Exception as exc:
            summary = {'state': 'failed', 'error': repr(exc)}
            write(args.output / name / 'failure.json', summary)
        finally:
            await inv.runtime.dispatcher.join()
            budget = inv.runtime.dispatcher.budget
            summary.update(seconds=time.perf_counter() - started,
                           incremental_tokens=budget.spent-before, logical_total_tokens=budget.spent,
                           logical_total_tool_attempts=attempts(inv), logical_budget_halted=budget.halted)
            write(args.output / name / 'state.json', {k: asdict(v) for k,v in inv.incident.store.results.items()})
            write(args.output / name / 'sources.json', {'heads': inv.revisions.heads,
                  'archive': inv.revisions.archive, 'events': inv.revision_events})
            write(args.output / name / 'usage.json', {'settlements': budget.settlements,
                  'calls': inv.provider.records, 'summary': summary})
            summaries[name] = summary
            write(args.output / 'summary.json', {'arms': summaries, 'physical_tokens': physical.spent,
                  'physical_halted': physical.halted, 'physical_settlements': physical.settlements})
        return summary['state'] == 'completed'

    base = build('historical-prefix')
    prefix_ok = await save_run('historical-prefix', base, base.run)
    if prefix_ok and base.originals and not physical.halted:
        invalid = base.advance()
        write(args.output / 'transition.json', {'invalid': invalid, 'events': base.revision_events,
              'retained': sorted(set(base.incident.store.results) - set(invalid)),
              'registered_source_count': len(base.revisions.heads), 'source_rereads_at_barrier': 0})
        for name in args.order:
            if physical.halted:
                summaries[name] = {'state': 'skipped', 'reason': 'physical budget halted'}
                continue
            if name == 'selective':
                inv = recovery_branch(base, provider(name), args.tokens)
                arms[name] = inv
            else:
                inv = build(name, old=False)
                inv.revisions = copy.deepcopy(base.revisions)
                inv.revision_events = copy.deepcopy(base.revision_events)
                inv.revisions.sync(inv.incident.store)
                inv.runtime.dispatcher.budget.spent = base.runtime.dispatcher.budget.spent
                inv.tool_budget_start = -attempts(base)
                inv.kind = 'recovery'
            await save_run(name, inv, lambda inv=inv, name=name: inv.run(reuse=name=='selective'))
    else:
        summaries['recovery'] = {'state': 'skipped', 'reason': 'prefix failed, no exposure, or physical halt'}
    if not physical.halted:
        verified = build('verify-before-diagnosis')
        async def preventive():
            await verified.collect()
            if not verified.originals:
                raise ValueError('No fixed historical exposure; no retargeting')
            verified.advance()
            verified.refresh_inputs()
            return await verified.diagnose()
        await save_run('verify-before-diagnosis', verified, preventive)
    else:
        summaries['verify-before-diagnosis'] = {'state': 'skipped', 'reason': 'physical budget halted'}
    write(args.output / 'summary.json', {'arms': summaries, 'physical_tokens': physical.spent,
          'physical_halted': physical.halted, 'physical_settlements': physical.settlements})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--vendor', type=Path, required=True)
    parser.add_argument('--case', choices=CASES, required=True)
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--tokens', type=int, default=240000)
    parser.add_argument('--tools', type=int, default=24)
    parser.add_argument('--physical-tokens', type=int, default=900000)
    parser.add_argument('--order', nargs=2, choices=['selective', 'restart'], default=['selective', 'restart'])
    args = parser.parse_args()
    if min(args.repeat, args.tokens, args.tools, args.physical_tokens) <= 0:
        parser.error('Repeat and budgets must be positive')
    if set(args.order) != {'selective', 'restart'}:
        parser.error('Order must include each recovery arm exactly once')
    asyncio.run(main(args))
