"""Query-scoped trusted revisions; authenticity and freshness are distinct checks."""
import copy
import hashlib
import json


def source_key(scope, record):
    if not isinstance(scope, str) or not scope:
        raise ValueError('Explicit tenant/incident scope required')
    identity = [scope, record['tool'], record['arguments']]
    return 'query:' + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def content(record):
    return {k: v for k, v in record.items()
            if k not in {'id', 'source_key', 'source_revision'}}


class VersionedSources:
    """In-memory trusted adapter, not a signature scheme or a change detector."""
    def __init__(self, scope):
        if not isinstance(scope, str) or not scope:
            raise ValueError('Explicit tenant/incident scope required')
        self.scope = scope
        self.heads = {}
        self.archive = {}
        self.events = []

    def record(self, observation):
        key = source_key(self.scope, observation)
        value = content(observation)
        if key not in self.heads:
            self.heads[key] = 1
            self.archive[key] = {1: copy.deepcopy(value)}
        elif self.archive[key][self.heads[key]] != value:
            raise ValueError('Changed source requires an explicit advance event')
        return self.current(key, observation['id'])

    def current(self, key, observation_id):
        revision = self.heads[key]
        return {**copy.deepcopy(self.archive[key][revision]), 'id': observation_id,
                'source_key': key, 'source_revision': revision}

    def advance(self, key, observation, reason):
        if not reason or key not in self.heads or source_key(self.scope, observation) != key:
            raise ValueError('Known same-query source and explicit change reason required')
        previous = self.heads[key]
        revision = previous + 1
        self.archive[key][revision] = copy.deepcopy(content(observation))
        self.heads[key] = revision
        self.events.append({'source': key, 'previous': previous, 'revision': revision,
                            'reason': reason,
                            'content_changed': self.archive[key][previous] != content(observation)})

    def inspect(self, observation):
        key, revision = observation.get('source_key'), observation.get('source_revision')
        if (not isinstance(key, str) or not isinstance(revision, int)
                or isinstance(revision, bool) or key not in self.archive
                or revision not in self.archive[key]):
            return {'authentic': False, 'fresh': False, 'reason': 'unknown provenance'}
        authentic = (source_key(self.scope, observation) == key
                     and content(observation) == self.archive[key][revision])
        return {'authentic': authentic, 'fresh': authentic and revision == self.heads[key],
                'reason': 'current' if authentic and revision == self.heads[key]
                else 'stale' if authentic else 'tampered'}

    def dependencies(self, observations):
        sources = {}
        for observation in observations:
            if not self.inspect(observation)['fresh']:
                raise ValueError('Cannot bind stale or unauthenticated source')
            sources[observation['source_key']] = observation['source_revision']
        return sources

    def sync(self, store):
        # The caller supplies change notifications; source identity does not imply detection.
        store.source_versions.update(self.heads)
        return store.invalid()
