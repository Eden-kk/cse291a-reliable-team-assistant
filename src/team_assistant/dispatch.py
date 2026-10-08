"""Non-preemptive asynchronous dispatch with reservations and actual usage settlement."""
import asyncio
from dataclasses import dataclass
import time


class BudgetExhausted(RuntimeError):
    pass


@dataclass
class Outcome:
    value: dict
    tokens: int | None
    error: str | None = None


class Budget:
    def __init__(self, limit):
        if limit <= 0:
            raise ValueError("Positive token limit required")
        self.limit = limit
        self.spent = 0
        self.reserved = 0
        self.halted = False
        self.settlements = []

    def affordable(self, cap):
        return cap > 0 and not self.halted and self.spent + self.reserved + cap <= self.limit

    def reserve(self, cap):
        if not self.affordable(cap):
            raise BudgetExhausted("Cannot cover call reservation")
        self.reserved += cap

    def settle(self, cap, actual):
        if cap > self.reserved:
            raise ValueError("Settlement lacks reservation")
        known = isinstance(actual, int) and not isinstance(actual, bool) and actual >= 0
        charged = actual if known else cap
        self.reserved -= cap
        self.spent += charged
        self.halted |= not known or charged > cap or self.spent + self.reserved > self.limit
        self.settlements.append({"reservation": cap, "actual": actual,
                                 "charged": charged, "halted": self.halted})


@dataclass
class Job:
    identifier: int
    group: str
    kind: str
    reservation: int
    invoke: object
    future: object
    arrived: float
    deadline: float | None = None


class Dispatcher:
    def __init__(self, budget, concurrency=2, policy="fair", age_seconds=10, clock=time.monotonic, response_handoff_seconds=0):
        if concurrency < 1 or policy not in {"fair", "fifo"} or age_seconds <= 0:
            raise ValueError("Invalid dispatcher configuration")
        if not 0 <= response_handoff_seconds <= .1:
            raise ValueError("Handoff must be bounded to 100ms")
        self.response_handoff_seconds = response_handoff_seconds
        self.background_hold_until = 0
        self.budget = budget
        self.concurrency = concurrency
        self.policy = policy
        self.age_seconds = age_seconds
        self.clock = clock
        self.pending = []
        self.running = {}
        self.tasks = set()
        self.service = {}
        self.events = []
        self._next_id = 0

    async def call(self, group, kind, reservation, invoke, deadline=None):
        if kind not in {"response", "recovery", "learning"} or reservation <= 0:
            raise ValueError("Invalid job")
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._next_id += 1
        job = Job(self._next_id, group, kind, reservation, invoke, future, self.clock(), deadline)
        self.pending.append(job)
        self.events.append({"event": "queued", "job": job.identifier, "group": group,
                            "kind": kind, "time": job.arrived, "deadline": deadline})
        # Coalesce submissions from the same event-loop turn before selecting priority.
        loop.call_soon(self._drain)
        try:
            # Cancelling a waiter must not make a request already sent to a provider free.
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            if job in self.pending:
                self.pending.remove(job)
                future.cancel()
            else:
                future.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)
            self.events.append({"event": "waiter_cancelled", "job": job.identifier})
            raise

    def _choose(self, feasible):
        if self.policy == "fifo":
            return min(feasible, key=lambda j: j.identifier)
        response_ready = any(j.kind == "response" for j in feasible)
        now = self.clock()
        background = sum(j.kind != "response" for j in self.running.values())
        choices = [j for j in feasible if not (response_ready and background >= self.concurrency - 1
                                               and j.kind != "response"
                                               and now - j.arrived < self.age_seconds
                                               and (j.deadline is None or j.deadline > now))]
        now = self.clock()
        rank = {"response": 0, "recovery": 1, "learning": 2}
        return min(choices, key=lambda j: (
            0 if j.deadline is not None and j.deadline <= now else 1,
            rank[j.kind] - int((now - j.arrived) / self.age_seconds),
            self.service.get(j.group, 0), j.identifier))

    def _drain(self):
        while len(self.running) < self.concurrency and self.pending:
            feasible = [j for j in self.pending if self.budget.affordable(j.reservation)]
            if not feasible:
                if not self.running:
                    for job in self.pending:
                        job.future.set_exception(BudgetExhausted("No remaining affordable reservation"))
                        self.events.append({"event": "rejected", "job": job.identifier})
                    self.pending.clear()
                return
            if self.clock() < self.background_hold_until:
                feasible = [j for j in feasible if j.kind == "response"
                            or self.clock() - j.arrived >= self.age_seconds
                            or (j.deadline is not None and j.deadline <= self.clock())]
                if not feasible:
                    return
            job = self._choose(feasible)
            self.pending.remove(job)
            self.budget.reserve(job.reservation)
            self.running[job.identifier] = job
            self.events.append({"event": "start", "job": job.identifier, "group": job.group,
                                "kind": job.kind, "reservation": job.reservation,
                                "active": len(self.running), "time": self.clock(),
                                "queue_seconds": self.clock() - job.arrived})
            task = asyncio.create_task(self._execute(job))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    async def _execute(self, job):
        actual = None
        try:
            result = await job.invoke()
            actual = result.tokens
        except asyncio.CancelledError:
            result = None
            error = RuntimeError("Provider cancelled; usage unknown; dispatch halted")
        except Exception as exc:
            result = None
            error = exc
        else:
            error = RuntimeError(result.error) if result.error else None
        self.budget.settle(job.reservation, actual)
        self.service[job.group] = self.service.get(job.group, 0) + (actual if isinstance(actual, int) and not isinstance(actual, bool) and actual >= 0 else job.reservation)
        del self.running[job.identifier]
        self.events.append({"event": "finish", "job": job.identifier, "actual": actual,
                            "halted": self.budget.halted, "error": repr(error) if error else None,
                            "time": self.clock()})
        if (job.kind == "response" and self.response_handoff_seconds
                and self.clock() >= self.background_hold_until):
            self.background_hold_until = self.clock() + self.response_handoff_seconds
            asyncio.get_running_loop().call_later(self.response_handoff_seconds, self._drain)
        if error is not None:
            job.future.set_exception(error)
        else:
            job.future.set_result(result)
        asyncio.get_running_loop().call_soon(self._drain)

    async def join(self):
        while self.tasks or self.pending:
            if self.tasks:
                await asyncio.shield(asyncio.gather(*list(self.tasks)))
            else:
                await asyncio.sleep(0)
