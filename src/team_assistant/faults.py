"""Explicit experimental corruptions; never used by normal investigations."""
import copy


def inject_claim(investigation, artifact_name, claim):
    store = investigation.incident.store
    original = store.results[artifact_name]
    payload = copy.deepcopy(original.payload)
    payload['specialist_claim'] = copy.deepcopy(claim)
    corrupted = copy.deepcopy(original)
    corrupted.version += 1
    corrupted.payload = payload
    store.commit(corrupted, store.epoch)
    return {'kind': 'constructed wrong intermediate claim', 'artifact': artifact_name,
            'clean_version': original.version, 'corrupted_version': original.version + 1,
            'claim': claim, 'fault_identity_available_at_notification': True}
