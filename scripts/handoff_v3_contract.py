"""Pre-output schema eligibility and the fixed typed-claim generation contract."""
import copy
import re

TIMING_CONTRACT = '\nTemporal evidence contract for this final answer:\nAvoid unnecessary timing assertions in reasoning. For every material temporal assertion,\nadd a temporal_claims entry with claim, kind (observed_event/configuration/derived_inference),\nsource_id, source_field, value, unit, temporal_scope, and assumptions. Use null for unknown\nvalues rather than guessing. An empty temporal_claims list is allowed if no such claim is made.\nDistinguish an interval between checks from a count of consecutive failures and from an\nobserved event time. Preserve the identity and units of each field. A configured threshold\nor interval alone does not establish actual restart timing. Derived timing requires explicit\nassumptions and must not be presented as an observed duration. Use only actually inspected\nraw evidence; do not cite specialist summaries as primary evidence. If support is absent,\nomit optional detail or explicitly state uncertainty. Preserve supported diagnosis and required\nevidence; do not replace them with blanket uncertainty. These rules supply no case answer.'
MUTATIONS = {'httpGet': 'grpc', 'grpc': 'httpGet'}


def inspect_schema(observation):
    text = observation.get('text', '')
    match = re.search(r'(?m)^(\s*)livenessProbe:\s*\n', text)
    if not match:
        return {'eligible': False, 'reason': 'fixed adservice livenessProbe absent'}
    indent = len(match[1]); lines = []
    for line in text[match.end():].splitlines():
        if line.strip() and len(line) - len(line.lstrip()) <= indent: break
        lines.append(line)
    found = re.findall(r'(?m)^\s+(httpGet|grpc|tcpSocket):\s*$', '\n'.join(lines))
    if len(found) != 1 or found[0] not in MUTATIONS:
        return {'eligible': False, 'reason': 'categorical field absent, ambiguous, or unallocated tcpSocket'}
    return {'eligible': True, 'field': 'liveness_protocol', 'before': found[0],
            'after': MUTATIONS[found[0]], 'source_sha256': observation['sha256'],
            'family': 'extracted categorical; deterministic opposite mapping before inference'}


def inject_categorical(inv, eligibility):
    original = inv.incident.store.results['configuration_summary']
    if not eligibility['eligible'] or original.payload.get('liveness_protocol') != eligibility['before']:
        return {'injected': False, 'reason': 'clean extraction not eligible; no replacement'}
    wrong = copy.deepcopy(original); wrong.version += 1
    wrong.payload['liveness_protocol'] = eligibility['after']
    wrong.payload['summary'] = f"adservice livenessProbe uses {eligibility['after']}."
    inv.incident.store.commit(wrong, inv.incident.store.epoch)
    return {**eligibility, 'injected': True, 'artifact': 'configuration_summary',
            'raw_sources_unchanged': True, 'seeded_downstream': False}
