"""Label-free, single-field configuration corruption for development validation."""
import copy
import hashlib
import json
import re

# Whole YAML scalar lines only: no commented, quoted, named, URL, or partial values.
PORT = re.compile(r"(?m)^[ \t]*(?:-[ \t]+)?(?:port|containerPort|targetPort):[ \t]*(?P<number>[0-9]+)[ \t]*(?:#[^\r\n]*)?\r?$")


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def mutate_observations(observations):
    """Return a detached copy and an audit; never inspect diagnosis labels."""
    result = copy.deepcopy(observations)
    ids = [o['id'] for o in observations]
    if len(ids) != len(set(ids)):
        raise ValueError('Ambiguous duplicate observation identities')
    ordered = sorted(enumerate(observations), key=lambda pair: (
        pair[1]['tool'], json.dumps(pair[1]['arguments'], sort_keys=True, separators=(',', ':')),
        pair[1]['id']))
    for index, obs in ordered:
        if (obs['tool'] != 'GetAppYAML' or obs.get('availability', 'returned') != 'returned'
                or obs.get('truncated', False)):
            continue
        for match in PORT.finditer(obs['text']):
            old = match.group('number')
            if not 1 <= int(old) <= 65534:
                continue
            start, end = match.span('number')
            new = str(int(old) + 1)
            text = obs['text'][:start] + new + obs['text'][end:]
            result[index]['text'] = text
            return result, {'injected': True, 'observation_id': obs['id'],
                            'tool': obs['tool'], 'arguments': copy.deepcopy(obs['arguments']),
                            'span': [start, end], 'before': old, 'after': new,
                            'pristine_visible_text_sha256': digest(obs['text']),
                            'corrupted_visible_text_sha256': digest(text),
                            'claimed_source_sha256': obs.get('sha256'),
                            'source_archive_unchanged': True}
    return result, {'injected': False, 'reason': 'no eligible returned untruncated YAML numeric-port field; no retargeting'}


def inject_numeric_port(inv):
    store = inv.incident.store
    result = copy.deepcopy(store.results['configuration_evidence'])
    observations, audit = mutate_observations(result.payload.get('observations', []))
    if audit['injected']:
        result.payload['observations'] = observations
        result.version += 1
        store.commit(result, store.epoch)
        audit.update(artifact=result.name, artifact_version=result.version)
    return audit
