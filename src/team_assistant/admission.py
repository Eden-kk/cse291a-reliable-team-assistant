"""FIFO admission of whole response workflows, independent of model-call dispatch."""
import asyncio
from collections import deque
from contextlib import asynccontextmanager
import time


class WorkflowAdmission:
    def __init__(self, limit=1, clock=time.monotonic):
        if type(limit) is not int or limit < 1:
            raise ValueError('Positive workflow limit required')
        self.limit, self.clock = limit, clock
        self.active, self.pending, self.events = {}, deque(), []

    def _drain(self):
        while self.pending and len(self.active) < self.limit:
            group, arrived, future = self.pending.popleft()
            if future.cancelled():
                continue
            self.active[group] = future
            self.events.append({'event': 'admitted', 'group': group, 'time': self.clock(),
                                'arrival_wait': self.clock() - arrived})
            future.set_result(None)

    @asynccontextmanager
    async def workflow(self, group):
        if group in self.active or any(item[0] == group for item in self.pending):
            raise ValueError('Workflow already active or waiting')
        arrived = self.clock()
        future = asyncio.get_running_loop().create_future()
        entry = (group, arrived, future)
        self.pending.append(entry)
        self.events.append({'event': 'arrived', 'group': group, 'time': arrived})
        self._drain()
        entered = False
        try:
            await future
            entered = True
            yield
        finally:
            if self.active.get(group) is future:
                del self.active[group]
                self.events.append({'event': 'released', 'group': group, 'time': self.clock(),
                                    'body_entered': entered})
            else:
                if entry in self.pending:
                    self.pending.remove(entry)
                self.events.append({'event': 'cancelled_waiting', 'group': group,
                                    'time': self.clock(), 'arrival_wait': self.clock() - arrived})
            self._drain()
