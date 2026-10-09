"""Fixed-trace discrete-event replay; never interpreted as live model performance."""
from .dispatch import Budget, Dispatcher, Job


def replay(nodes, policy, limit, concurrency=2, initial_spent=0, initial_service=None, join_delay=0, reserve_foreground=False):
    ids = {n['id'] for n in nodes}
    if len(ids) != len(nodes) or any(set(n['dependencies']) - ids for n in nodes):
        raise ValueError('Duplicate nodes or missing dependencies')
    if join_delay < 0 or any(n['duration'] <= 0 or n['tokens'] < 0 for n in nodes):
        raise ValueError('Invalid duration, tokens, or delay')
    if reserve_foreground:
        for group in {n['group'] for n in nodes}:
            members = [n for n in nodes if n['group'] == group]
            if len({(n['kind'], n['arrival']) for n in members}) != 1:
                raise ValueError('Reservation guard requires one arrival and kind per group')
        owner = {n['id']: n['group'] for n in nodes}
        if any(owner[d] != n['group'] for n in nodes for d in n['dependencies']):
            raise ValueError('Reservation guard requires independent group DAGs')
    now = 0.0
    sequence = 0
    budget = Budget(limit)
    budget.spent = initial_spent
    dispatcher = Dispatcher(budget, concurrency, policy, clock=lambda: now)
    dispatcher.service = dict(initial_service or {})
    remaining = {n['id']: n for n in nodes}
    pending, running, finished, events, rejected = {}, {}, {}, [], []
    guard_events = []

    def feasible_jobs():
        feasible = []
        # Only known, already-arrived foreground DAGs can claim the next-call reserve.
        next_reservations = {}
        if reserve_foreground:
            for n in list(remaining.values()) + [n for n, _ in pending.values()]:
                if n['kind'] == 'response' and n['arrival'] <= now:
                    next_reservations[n['group']] = max(next_reservations.get(n['group'], 0),
                                                         n['reservation'])
        held = sum(next_reservations.values())
        for node, job in pending.values():
            if not budget.affordable(node['reservation']):
                continue
            if (reserve_foreground and node['kind'] != 'response'
                    and budget.limit - budget.spent - budget.reserved - node['reservation'] < held):
                guard_events.append({'id': node['id'], 'time': now, 'held_tokens': held})
            else:
                feasible.append(job)
        return feasible

    while remaining or pending or running:
        for key, node in list(remaining.items()):
            if all(dep in finished for dep in node['dependencies']):
                ready = max([node['arrival']] + [finished[d] + join_delay for d in node['dependencies']])
                if ready <= now:
                    sequence += 1
                    job = Job(sequence, node['group'], node['kind'], node['reservation'], None, None,
                              ready, node['deadline'])
                    pending[key] = (node, job)
                    del remaining[key]
        feasible = feasible_jobs()
        while feasible and len(running) < concurrency:
            job = dispatcher._choose(feasible)
            key = next(key for key, (_, queued) in pending.items() if queued is job)
            node, _ = pending.pop(key)
            budget.reserve(job.reservation)
            dispatcher.running[key] = job
            running[key] = (now + node['duration'], node)
            events.append({'id': key, 'group': job.group, 'ready': job.arrived,
                           'start': now, 'finish': now + node['duration'], 'queue': now - job.arrived})
            feasible = feasible_jobs()
        future = [end for end, _ in running.values()]
        for node in remaining.values():
            if all(dep in finished for dep in node['dependencies']):
                ready = max([node['arrival']] + [finished[d] + join_delay for d in node['dependencies']])
                if ready > now:
                    future.append(ready)
        if not future:
            if pending:
                rejected.extend(pending)
                rejected.extend(remaining)
                break
            if remaining:
                raise ValueError('Dependency cycle')
            break
        now = min(future)
        for key, (end, node) in list(running.items()):
            if end <= now:
                budget.settle(node['reservation'], node['tokens'])
                dispatcher.service[node['group']] = dispatcher.service.get(node['group'], 0) + node['tokens']
                finished[key] = end
                del dispatcher.running[key]
                del running[key]
    groups = {}
    for group in {n['group'] for n in nodes}:
        members = [n for n in nodes if n['group'] == group]
        complete = all(n['id'] in finished for n in members)
        end = max((finished.get(n['id'], 0) for n in members), default=0) if complete else None
        groups[group] = {'completed': complete, 'seconds': end,
                         'slo_met': complete and end <= min(n['deadline'] for n in members)}
    result = {'groups': groups, 'events': events, 'rejected_ids': rejected, 'tokens': budget.spent,
            'halted': budget.halted, 'scope': 'fixed-output fixed-service replay; no quality or live latency claim'}
    if reserve_foreground:
        result['reservation_guard'] = {'simulation_only': True, 'deferrals': guard_events,
            'scope': 'one fixed next-call reservation per arrived unfinished response DAG; no future durations/tokens/arrivals',
            'cost': 'may idle capacity, delay or reject recovery; does not reserve the full remaining DAG cost'}
    return result
