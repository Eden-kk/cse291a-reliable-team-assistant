"""Validate a planned benchmark case before a future inference runner starts it."""
import hashlib
import json
from pathlib import Path


REQUIRED_ARTIFACTS = ('prompts', 'memory', 'thresholds', 'faults', 'arrivals', 'budget')


def authorize_case(case, registry, commit, freeze=None, artifact_root=None):
    matches = [name for name, cases in registry['splits'].items() if case in cases]
    if len(matches) != 1:
        raise ValueError('Case missing or ambiguous in registry')
    split = matches[0]
    if split != 'final_locked':
        return split
    if not registry.get('final_unlocked') or not freeze or artifact_root is None:
        raise ValueError('Final set locked: freeze and explicit unlock required')
    if freeze.get('code_commit') != commit or freeze.get('dataset_commit') != registry['dataset_commit']:
        raise ValueError('Frozen code/dataset mismatch')
    if not freeze.get('model'):
        raise ValueError('Missing frozen model identity')
    registry_hash = hashlib.sha256(json.dumps(registry, sort_keys=True).encode()).hexdigest()
    if freeze.get('registry_sha256') != registry_hash:
        raise ValueError('Split registry changed since freeze')
    root = Path(artifact_root).resolve()
    for name in REQUIRED_ARTIFACTS:
        entry = freeze.get('artifacts', {}).get(name, {})
        if not entry.get('path') or not entry.get('sha256'):
            raise ValueError('Missing freeze artifact: ' + name)
        path = (root / entry['path']).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError('Invalid freeze artifact path')
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
            raise ValueError('Changed freeze artifact: ' + name)
    return split
