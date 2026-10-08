"""Fixed-trace discrete-event replay; never interpreted as live model performance."""
from .dispatch import Budget, Dispatcher, Job


def replay(nodes, policy, limit, concurrency=2, initial_spent=0, initial_service=None, join_delay=0):
    ids = {n['id'] for n in nodes}
    if len(ids) != len(nodes) or any(set(n['dependencies']) - ids for n in nodes):
        raise ValueError('Duplicate nodes or missing dependencies')
    if join_delay < 0 or any(n['duration'] <= 0 or n['tokens'] < 0 for n in nodes):
        raise ValueError('Invalid duration, tokens, or delay')
    now = 0.0
    sequence = 0
    budget = Budget(limit)
    budget.spent = initial_spent
    dispatcher = Dispatcher(budget, concurrency, policy, clock=lambda: now)
    dispatcher.service = dict(initial_service or {})
    remaining = {n['id']: n for n in nodes}
    pending, running, finished, events, rejected = {}, {}, {}, [], []
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
        feasible = [job for node, job in pending.values() if budget.affordable(node['reservation'])]
        while feasible and len(running) < concurrency:
            job = dispatcher._choose(feasible)
            key = next(key for key, (_, queued) in pending.items() if queued is job)
            node, _ = pending.pop(key)
            budget.reserve(job.reservation)
            dispatcher.running[key] = job
            running[key] = (now + node['duration'], node)
            events.append({'id': key, 'group': job.group, 'ready': job.arrived,
                           'start': now, 'finish': now + node['duration'], 'queue': now - job.arrived})
            feasible = [j for n, j in pending.values() if budget.affordable(n['reservation'])]
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
    return {'groups': groups, 'events': events, 'rejected_ids': rejected, 'tokens': budget.spent,
            'halted': budget.halted, 'scope': 'fixed-output fixed-service replay; no quality or live latency claim'}
