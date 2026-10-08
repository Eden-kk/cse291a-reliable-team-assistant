"""Controlled evidence handoff damage and source-integrity verification experiment."""
import copy
import time
from .dispatch import Budget, Dispatcher
from .investigation import Investigation
from .runtime import Runtime


def corrupt_port(inv):
    store = inv.incident.store
    original = store.results['configuration_evidence']
    corrupted = copy.deepcopy(original)
    changed = []
    for obs in corrupted.payload.get('observations', []):
        count = obs['text'].count('9555')
        if count:
            obs['text'] = obs['text'].replace('9555', '9556')
            changed.append({'id': obs['id'], 'occurrences': count})
    if not changed:
        return {'injected': False, 'reason': 'fixed port absent; do not retarget'}
    corrupted.version += 1
    store.commit(corrupted, store.epoch)
    return {'injected': True, 'artifact': original.name, 'changed': changed,
            'transform': 'all 9555 substrings to 9556 in configuration_evidence visible text',
            'category': 'constructed accidental intermediate evidence corruption',
            'source_archive_unchanged': True}


def verify_inputs(inv, dependencies):
    started = time.perf_counter()
    pristine = {o['id']: o for o in inv.snapshot.observations}
    checked, repaired = 0, []
    store = inv.incident.store
    if inv.snapshot.fingerprint() != inv.snapshot.source_version:
        raise ValueError('Source changed; static integrity verifier cannot repair')
    for name in dependencies:
        result = copy.deepcopy(store.results[name])
        changed = False
        for index, obs in enumerate(result.payload.get('observations', [])):
            checked += 1
            if obs['id'] not in pristine:
                raise ValueError('Unknown observation identity; fail closed')
            if obs != pristine[obs['id']]:
                result.payload['observations'][index] = copy.deepcopy(pristine[obs['id']])
                changed = True
                repaired.append({'artifact': name, 'observation': obs['id']})
        if changed:
            result.version += 1
            store.commit(result, store.epoch)
    return {'checked_observations': checked, 'repairs': repaired,
            'wall_seconds': time.perf_counter() - started, 'extra_model_calls': 0,
            'method': 'full record equality against pristine static source archive; not semantic validation'}


class IntegrityInvestigation(Investigation):
    async def model_step(self, name, instruction, dependencies, use_memory):
        audit = verify_inputs(self, dependencies)
        self.integrity_audits.append({'step': name, **audit})
        return await super().model_step(name, instruction, dependencies, use_memory)


def fork(inv, provider, verify=False):
    """Clone settled collection state, sharing neither mutable artifacts nor ledgers."""
    cls = IntegrityInvestigation if verify else Investigation
    clone = cls.__new__(cls)
    clone.__dict__ = inv.__dict__.copy()
    clone.snapshot = copy.deepcopy(inv.snapshot)
    clone.incident = copy.deepcopy(inv.incident)
    memory = copy.deepcopy(inv.runtime.memory)
    memory.runs[clone.identifier]['store'] = clone.incident.store
    clone.runtime = Runtime(memory, Dispatcher(Budget(360000)))
    clone.runtime.incidents[clone.identifier] = clone.incident
    clone.runtime.events = []
    clone.provider = provider
    clone.tool_errors = copy.deepcopy(inv.tool_errors)
    clone.integrity_audits = []
    return clone
