"""In-process incident execution with captured inputs and scoped commit validation."""
import copy
from dataclasses import dataclass, field

from .state import ExecutionStore, Result


class StaleAttempt(RuntimeError):
    pass


@dataclass
class Incident:
    identifier: str
    team: str
    group: str
    store: ExecutionStore = field(default_factory=ExecutionStore)
    generations: dict = field(default_factory=dict)


class Runtime:
    def __init__(self, memory, dispatcher, max_incidents=4):
        self.memory = memory
        self.dispatcher = dispatcher
        self.max_incidents = max_incidents
        self.incidents = {}
        self.events = []

    def start(self, identifier, team, group):
        if identifier in self.incidents or len(self.incidents) >= self.max_incidents:
            raise ValueError('Duplicate incident or admission limit reached')
        incident = Incident(identifier, team, group)
        self.memory.pin(identifier, team, incident.store)
        self.incidents[identifier] = incident
        return incident

    async def step(self, identifier, name, backend, dependencies=(), use_memory=False,
                   kind='response', reservation=200, deadline=None):
        incident = self.incidents[identifier]
        store = incident.store
        if any(p not in store.results or p in store.invalid() for p in dependencies):
            raise ValueError('Missing or invalid dependency')
        inputs = {p: store.results[p].version for p in dependencies}
        shared = {}
        request = {'incident': identifier, 'step': name,
                   'inputs': {p: copy.deepcopy(store.results[p].payload) for p in dependencies}}
        if use_memory:
            version = self.memory.runs[identifier]['version']
            entry = self.memory.entries[version]
            if not entry['valid']:
                raise ValueError('Rebind revoked memory before execution')
            shared['policy:' + version] = 1
            request['policy'] = copy.deepcopy(entry['policy'])
        generation = incident.generations.get(name, 0) + 1
        incident.generations[name] = generation
        self.events.append({'event': 'request', 'incident': identifier, 'step': name,
                            'generation': generation, 'inputs': inputs, 'shared': shared,
                            'request': copy.deepcopy(request)})
        outcome = await self.dispatcher.call(incident.group, kind, reservation,
                                             lambda: backend(copy.deepcopy(request)), deadline)
        try:
            if incident.generations[name] != generation:
                raise ValueError('Newer attempt supersedes this request')
            version = store.results[name].version + 1 if name in store.results else 1
            # Validate captured reads against current state. Unrelated epoch changes alone
            # do not invalidate independent work; revoked inputs still fail commit checks.
            store.commit(Result(name, version, outcome.value, inputs=inputs,
                                shared_reads=shared), store.epoch)
        except ValueError as exc:
            self.events.append({'event': 'discard', 'incident': identifier, 'step': name,
                                'generation': generation, 'reason': str(exc)})
            raise StaleAttempt(str(exc)) from exc
        self.events.append({'event': 'commit', 'incident': identifier, 'step': name,
                            'version': version})
        return copy.deepcopy(store.results[name])

    def revoke(self, team, version):
        affected = self.memory.revoke(team, version)
        self.events.append({'event': 'revoke', 'version': version, 'affected': affected})
        return affected

    def publish(self, identifier, name, validator, supersedes=None):
        store = self.incidents[identifier].store
        if not validator(copy.deepcopy(store.results[name].payload)):
            raise ValueError('External publication validation failed')
        entry = store.publish(name, store.epoch, supersedes)
        self.events.append({'event': 'publication', 'incident': identifier, 'entry': entry})
        return entry
