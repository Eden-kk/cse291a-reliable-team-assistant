"""Explicit experimental corruptions; never used by normal investigations."""
import copy
from .state import Result


def inject_claim(investigation, artifact_name, claim):
    store = investigation.incident.store
    original = store.results[artifact_name]
    payload = copy.deepcopy(original.payload)
    payload['specialist_claim'] = copy.deepcopy(claim)
    store.commit(Result(artifact_name, original.version + 1, payload,
                        inputs=original.inputs, sources=original.sources,
                        shared_reads=original.shared_reads), store.epoch)
    return {'kind': 'constructed wrong intermediate claim', 'artifact': artifact_name,
            'clean_version': original.version, 'corrupted_version': original.version + 1,
            'claim': claim, 'fault_identity_available_at_notification': True}
