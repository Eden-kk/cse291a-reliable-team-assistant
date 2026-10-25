"""Pre-output schema eligibility and the fixed typed-claim generation contract."""
import copy
import hashlib
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


def replace_unique_protocol(summary, before, after):
    """Replace one explicit affirmative liveness relation, never a whole summary."""
    if not isinstance(summary, str): return None
    # Deliberately narrow grammar; unrecognized prose remains ineligible, not guessed.
    pattern = re.compile(
        r"\b(?:livenessProbe|liveness probe)(?:\s+(?:uses?|is(?:\s+configured\s+(?:with|as|to use))?|using)|\s*:)"
        r"\s+`?(?P<protocol>httpGet|grpc)`?(?=\W|$)", re.IGNORECASE)
    matches = list(pattern.finditer(summary))
    if len(matches) != 1: return None
    match = matches[0]
    if match.group('protocol').lower() != before.lower(): return None
    # Avoid changing quotations of rejected assertions or explicit negation.
    sentence_start = max(summary.rfind('.', 0, match.start()), summary.rfind('\n', 0, match.start()),
                         summary.rfind(';', 0, match.start())) + 1
    ends = [i for c in ('.', '\n', ';') if (i := summary.find(c, match.end())) >= 0]
    sentence_end = min(ends) if ends else len(summary)
    clause = summary[sentence_start:sentence_end]
    if re.search(r"\b(?:not|no|never|false|incorrect|wrong|deny|denies|reject|rejects|whether|if|claim|claims|said|says|hypothesis|assume|possibly|may)\b", clause, re.IGNORECASE):
        return None
    if len(re.findall(r"\b(?:httpGet|grpc|tcpSocket)\b", clause, re.IGNORECASE)) != 1:
        return None
    start, end = match.span('protocol')
    changed = summary[:start] + after + summary[end:]
    return {'summary': changed, 'span': [start, end], 'old_text': summary[start:end],
            'new_text': after, 'rule': 'one explicit affirmative liveness relation'}


def inject_categorical(inv, eligibility):
    original = inv.incident.store.results['configuration_summary']
    if not eligibility['eligible'] or original.payload.get('liveness_protocol') != eligibility['before']:
        return {'injected': False, 'reason': 'clean extraction not eligible; no replacement'}
    edit = replace_unique_protocol(original.payload.get('summary'), eligibility['before'], eligibility['after'])
    if edit is None:
        return {'injected': False, 'reason': 'no unique safe liveness protocol span; no replacement',
                'mutation_version': 'unique-protocol-span-v1'}
    wrong = copy.deepcopy(original); wrong.version += 1
    wrong.payload['liveness_protocol'] = eligibility['after']
    wrong.payload['summary'] = edit['summary']
    inv.incident.store.commit(wrong, inv.incident.store.epoch)
    return {**eligibility, 'injected': True, 'artifact': 'configuration_summary',
            'raw_sources_unchanged': True, 'seeded_downstream': False,
            'mutation_version': 'unique-protocol-span-v1',
            'summary_edit': {k:v for k,v in edit.items() if k != 'summary'},
            'summary_before_sha256': hashlib.sha256(original.payload['summary'].encode()).hexdigest(),
            'summary_after_sha256': hashlib.sha256(wrong.payload['summary'].encode()).hexdigest(),
            'other_summary_text_preserved': True,
            'other_payload_fields_preserved': all(wrong.payload[k] == v for k,v in original.payload.items()
                                                  if k not in ('summary','liveness_protocol'))}
