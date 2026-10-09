"""Cross-component engineering checks; synthetic outputs and usage, never model evidence."""
import asyncio
import copy
from .demo import approve
from .dispatch import Budget, BudgetExhausted, Dispatcher, Outcome
from .memory import PolicyMemory, digest
from .runtime import Runtime, StaleAttempt


async def run(scope='partial', recovery='selective', guard=True):
    if scope not in ('partial', 'full', 'pressure') or recovery not in ('selective', 'restart'):
        raise ValueError('Unsupported controlled condition')
    memory = PolicyMemory()
    approve(memory, 'v1', 'old-config')
    dispatcher = Dispatcher(Budget(117 if scope == 'pressure' else 500), concurrency=2,
                            foreground_reservation=20 if guard else None)
    runtime = Runtime(memory, dispatcher)
    async def backend(request):
        await asyncio.sleep(0)
        return Outcome({'controlled': True, 'policy': request.get('policy'),
                        'parents': list(request['inputs'])}, 10)
    async def step(who, name, dependencies=(), policy=False, kind='recovery', custom=None):
        return await runtime.step(who, name, custom or backend, dependencies, policy,
                                  kind=kind, reservation=20)
    for who in ('A', 'B'):
        runtime.start(who, 'oncall', who)
        await step(who, 'telemetry', policy=scope == 'full')
        await step(who, 'plan', policy=True)
        await step(who, 'diagnosis', ('telemetry', 'plan'))
        runtime.publish(who, 'diagnosis', lambda p: p['controlled'])
    historical = {w: copy.deepcopy(runtime.incidents[w].store.ledger[0]) for w in ('A', 'B')}
    entered, release = asyncio.Event(), asyncio.Event()
    async def delayed(request):
        entered.set()
        await release.wait()
        return Outcome({'controlled': True, 'policy': request['policy']}, 17)
    old = asyncio.create_task(step('A', 'plan', policy=True, custom=delayed))
    await entered.wait()
    rejected = False
    candidate = {'check': 'unvalidated-new-rule'}
    try:
        memory.promote('oncall', 'rejected', candidate, {'accepted': False, 'sha256': digest(candidate)})
    except ValueError:
        rejected = True
    default_unchanged = memory.default['oncall'] == 'v1'
    # This replacement is a fixture-approved safe fallback, not the rejected learned candidate.
    approve(memory, 'safe', 'verified-fallback')
    runtime.start('C', 'oncall', 'C')
    affected = runtime.revoke('oncall', 'v1')
    invalid_publication_blocked = []
    for who in ('A', 'B'):
        try:
            runtime.publish(who, 'diagnosis', lambda p: True)
        except ValueError:
            invalid_publication_blocked.append(who)
    release.set()
    stale = False
    try:
        await old
    except StaleAttempt:
        stale = True
    first_repair_plan = asyncio.Event()
    async def repair(who):
        memory.rebind(who)
        store = runtime.incidents[who].store
        if recovery == 'restart' or 'telemetry' in store.invalid():
            await step(who, 'telemetry', policy=scope == 'full')
            first_repair_plan.set()
        await step(who, 'plan', policy=True)
        first_repair_plan.set()
        await step(who, 'diagnosis', ('telemetry', 'plan'))
        runtime.publish(who, 'diagnosis', lambda p: p['controlled'], supersedes=1)
    async def fresh():
        await first_repair_plan.wait()
        await step('C', 'plan', policy=True, kind='response')
        runtime.publish('C', 'plan', lambda p: p['controlled'])
    async def concurrent():
        return await asyncio.gather(repair('A'), repair('B'), fresh(), return_exceptions=True)
    if guard:
        with dispatcher.response_workflow('C', calls=1):
            outcomes = await concurrent()
    else:
        outcomes = await concurrent()
    await dispatcher.join()
    stores = {k: v.store for k, v in runtime.incidents.items()}
    checks = {
        'rejected_candidate_not_activated': rejected and default_unchanged and 'rejected' not in memory.entries,
        'stale_inflight_rejected': stale,
        'invalid_publications_blocked': invalid_publication_blocked == ['A', 'B'],
        'discarded_call_charged': any(s['actual'] == 17 for s in dispatcher.budget.settlements),
        'invalidation_scope_exact': affected == {w: (['diagnosis', 'plan', 'telemetry'] if scope == 'full'
                                                    else ['diagnosis', 'plan']) for w in ('A', 'B')},
        'fresh_group_unaffected': 'C' not in affected and not stores['C'].invalid(),
        'no_rejected_policy_consumed': all(e.get('request', {}).get('policy') != candidate for e in runtime.events),
        'reservations_drained': dispatcher.budget.reserved == 0 and not dispatcher.tasks and not dispatcher.pending and not dispatcher.running and not dispatcher.foreground_workflows,
        'cost_reconciled': dispatcher.budget.spent == sum(s['charged'] for s in dispatcher.budget.settlements),
        'old_publications_preserved': all(stores[w].ledger[0] == historical[w] for w in ('A', 'B')),
        'corrections_valid_and_linked': all(len(stores[w].ledger) == 1 or
            (stores[w].ledger[1]['supersedes'] == 1 and not stores[w].invalid()) for w in ('A', 'B')),
    }
    starts = [e for e in dispatcher.events if e['event'] == 'start']
    checks['cost_matches_dispatched_calls'] = (len(starts) == len(dispatcher.budget.settlements)
        and dispatcher.budget.spent == 17 + 10 * (len(starts) - 1))
    checks['only_budget_failures'] = all(not isinstance(o, BaseException) or isinstance(o, BudgetExhausted) for o in outcomes)
    checks['completed_publications_match_outcome'] = all(
        len(stores[w].ledger) == (1 if isinstance(o, BaseException) else 2) for w, o in zip(('A', 'B'), outcomes))
    checks['fresh_publication_valid'] = (not isinstance(outcomes[2], BaseException)
        and len(stores['C'].ledger) == 1 and 'plan' in stores['C'].results and not stores['C'].invalid())
    if scope == 'pressure' and guard:
        checks['foreground_reservation_exercised'] = any(e['event'] == 'guard_deferred' for e in dispatcher.events)
    if scope != 'pressure':
        checks['all_work_completed'] = all(not isinstance(o, BaseException) for o in outcomes)
        checks['telemetry_reuse_matches_scope'] = all(stores[w].results['telemetry'].version ==
            (1 if scope == 'partial' and recovery == 'selective' else 2) for w in ('A', 'B'))
    if not all(checks.values()):
        raise AssertionError(checks)
    return {'scope': scope, 'recovery': recovery, 'foreground_guard': guard,
            'kind': 'controlled engineering integration; simulated usage; no model-quality or SLA claim',
            'checks': checks, 'simulated_tokens': dispatcher.budget.spent, 'affected': affected,
            'outcomes': {k: {'state': 'failed' if isinstance(o, BaseException) else 'completed',
                             'error': repr(o) if isinstance(o, BaseException) else None}
                         for k, o in zip(('A', 'B', 'C'), outcomes)},
            'runtime_events': runtime.events, 'dispatch_events': dispatcher.events,
            'settlements': dispatcher.budget.settlements,
            'ledgers': {k: s.ledger for k, s in stores.items()}}
