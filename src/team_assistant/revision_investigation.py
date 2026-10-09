"""Controlled trusted source updates for Phase 2; no benchmark labels are read."""
import copy
from .investigation import Investigation
from .sources import VersionedSources, source_key, content
from .state import Result
from .revision_faults import historical_record

CASES = {'boutique/runtime/1': ('9555', '9556'),
         'boutique/performance/1': ('200m', '800m')}


class RevisionInvestigation(Investigation):
    def __init__(self, *args, case, fault_scope="query-fields-v2", **kwargs):
        super().__init__(*args, **kwargs)
        self.revisions = VersionedSources(f'{self.incident.team}/{self.identifier}/{case}')
        self.transform = CASES[case]
        self.fault_scope = fault_scope
        self.old_phase = True
        self.originals = {}
        self.revision_events = []
        self.source_rereads = 0

    def artifact(self, name, payload, dependencies=()):
        payload = copy.deepcopy(payload)
        observations = payload.get('observations', [])
        for index, observation in enumerate(observations):
            key = source_key(self.revisions.scope, observation)
            if self.old_phase and name == 'configuration_evidence':
                changed = historical_record(observation, self.transform, self.fault_scope)
                if changed['text'] != observation['text']:
                    self.originals[key] = copy.deepcopy(observation)
                observation = changed
            if key in self.revisions.heads and (not self.old_phase or name.startswith('additional_')):
                stamped = self.revisions.current(key, observation['id'])
                if not self.old_phase and content(stamped) != content(observation):
                    raise ValueError('Fetched current source differs from trusted revision')
            else:
                # Mixed branch versions of the same query fail closed here.
                stamped = self.revisions.record(observation)
            stamped['id'] += '-r' + str(stamped['source_revision'])
            observations[index] = stamped
        store = self.incident.store
        self.revisions.sync(store)
        sources = self.revisions.dependencies(observations)
        sources[self.source] = self.snapshot.source_version
        version = store.results[name].version + 1 if name in store.results else 1
        store.commit(Result(name, version, payload,
                            inputs={n: store.results[n].version for n in dependencies},
                            sources=sources), store.epoch)

    def advance(self):
        if not self.old_phase:
            raise ValueError('Source already advanced')
        for key, original in self.originals.items():
            self.revisions.advance(key, original, 'predeclared environment update at diagnosis barrier')
        self.old_phase = False
        invalid = sorted(self.revisions.sync(self.incident.store))
        self.incident.store.invalidate([])
        self.revision_events.extend(copy.deepcopy(self.revisions.events))
        return invalid

    def refresh_inputs(self):
        """Source-aware verification reads each changed query again; tools are charged."""
        store = self.incident.store
        for name in ('initial', 'configuration_evidence', 'telemetry_evidence'):
            if name not in store.invalid():
                continue
            result = store.results[name]
            queries = [{'tool': o['tool'], 'arguments': o['arguments']}
                       for o in result.payload.get('observations', [])]
            before = len(self.snapshot.observations) + len(self.tool_errors)
            observations = self.execute_queries(queries)
            self.source_rereads += len(self.snapshot.observations) + len(self.tool_errors) - before
            if len(observations) != len(queries):
                raise ValueError('Insufficient tool budget for source verification')
            self.artifact(name, {'observations': observations}, tuple(result.inputs))

    async def run(self, reuse=False):
        # Phase 1 resets tool_budget_start in run(); Phase 2 caps include the prefix.
        await self.collect(reuse)
        return await self.diagnose()
