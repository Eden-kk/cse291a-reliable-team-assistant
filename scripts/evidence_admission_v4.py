"""Offline whole-source admission using the unchanged Triton provider wire encoding."""
import copy
import hashlib
import json
from team_assistant.triton_provider import MODEL, MAX_TOKENS

CAP = 40000
FRAMING_RESERVE = 512


def encoded_body(instruction, request, *, json_object=True):
    # Provider exposes no public serializer; keep this byte-for-byte parity tested.
    if not isinstance(instruction, str):
        raise ValueError('Instruction must be a string')
    body = {'model': MODEL, 'messages': [
        {'role': 'system', 'content': 'No tools or files. Use supplied evidence only. Return one JSON object.\n' + instruction},
        {'role': 'user', 'content': json.dumps(request, allow_nan=False)}],
        'max_tokens': MAX_TOKENS, 'temperature': 0, 'reasoning_effort': 'low', 'stream': False}
    if json_object:
        body['response_format'] = {'type': 'json_object'}
    return json.dumps(body, separators=(',', ':'), allow_nan=False).encode()


def preflight(instruction, request, *, json_object=True, cap=CAP, inherited_budget=None):
    if type(cap) is not int or not FRAMING_RESERVE < cap <= CAP:
        raise ValueError('Cap must not exceed the frozen provider input cap')
    encoded = encoded_body(instruction, request, json_object=json_object)
    total = len(encoded) + FRAMING_RESERVE
    return {'allowed': total <= cap, 'gate': 'passed' if total <= cap else 'input_byte_cap',
            'body_sha256': hashlib.sha256(encoded).hexdigest(),
            'request_sha256': hashlib.sha256(json.dumps(request, allow_nan=False).encode()).hexdigest(),
            'encoded_body_bytes': len(encoded), 'framing_reserve': FRAMING_RESERVE,
            'estimated_total_bytes': total, 'actual_total_bytes': total, 'cap_bytes': cap,
            'logical_attempts': 1, 'logical_token_reservation': cap + MAX_TOKENS,
            'physical_calls': 0, 'physical_reserved_tokens': 0, 'physical_reserved_usd': '0',
            'zero_network': True, 'inherited_budget': copy.deepcopy(inherited_budget)}


def _source(record):
    data = copy.deepcopy(record)
    if not isinstance(data.get('id'), str) or not isinstance(data.get('text'), str):
        raise ValueError('Source needs ID and text')
    full = data.pop('raw_text', None)
    if full is not None and not isinstance(full, str):
        raise ValueError('Full raw text must be a string')
    if full is not None:
        if not full.startswith(data['text']):
            raise ValueError('Visible prefix disagrees with full archive')
        text = full
        partial = False
    else:
        text = data['text']
        partial = data.get('truncated') is True
    supplied_sha = hashlib.sha256(text.encode()).hexdigest()
    original_sha = data.get('sha256')
    if not isinstance(original_sha, str) or len(original_sha) != 64 or any(c not in '0123456789abcdef' for c in original_sha):
        raise ValueError('Original source hash required')
    if not partial and supplied_sha != original_sha:
        raise ValueError('Full source hash mismatch')
    original_size = len(text.encode()) if not partial else data.get('original_total_bytes')
    if original_size is not None and (type(original_size) is not int or original_size < len(text.encode())):
        raise ValueError('Invalid original source byte length')
    data['text'] = text
    data['truncated'] = partial
    supplied_record_bytes = len(json.dumps(data, allow_nan=False).encode())
    meta = {'id': data['id'], 'source_sha256': original_sha,
            'supplied_text_sha256': supplied_sha, 'supplied_text_bytes': len(text.encode()),
            'supplied_record_bytes_without_completeness_metadata': supplied_record_bytes,
            'original_total_bytes': original_size, 'partial': partial,
            'scope': 'visible prefix only; unseen remainder cannot be treated as absent' if partial else 'full supplied source',
            'original_size_status': 'unknown; full archive unavailable' if original_size is None else 'known'}
    # Completeness is model-visible even if the adapter previously only set truncated.
    data['evidence_completeness'] = copy.deepcopy(meta)
    return data, meta


def admit_sources(instruction, request, *, json_object=True, cap=CAP, inherited_budget=None):
    """Replace only inputs.retrieved_sources, retaining every requested ID in metadata.

    Input observations are already in the coordinator's requested order. A raw_text archive
    is preferred when supplied. Missing archives never turn a truncated prefix into full text.
    The returned request must still pass the provider's own final gate before any API call.
    """
    base = copy.deepcopy(request)
    payload = base['inputs']['retrieved_sources']
    if 'admission' in payload:
        raise ValueError('Already admitted request; implicit re-admission forbidden')
    records = payload['observations']
    if not isinstance(records, list):
        raise ValueError('Observations must be a list')
    normalized = [_source(r) for r in records]
    identities = [m['id'] for _, m in normalized]
    if len(set(identities)) != len(identities):
        raise ValueError('Duplicate requested source ID')
    admitted = []
    def candidate(indices):
        value = copy.deepcopy(base)
        target = value['inputs']['retrieved_sources']
        target['observations'] = [normalized[i][0] for i in indices]
        target['admission'] = {
            'policy': 'whole sources in requested order; no substitution or text clipping',
            'requested_ids': identities, 'admitted_ids': [identities[i] for i in indices],
            'omitted': [{**meta, 'reason': 'request_byte_cap',
                         'unavailable_scope': 'entire requested source omitted; no content inspected'}
                        for i, (_, meta) in enumerate(normalized) if i not in indices]}
        return value
    empty = candidate([])
    initial = preflight(instruction, empty, json_object=json_object, cap=cap, inherited_budget=inherited_budget)
    if not initial['allowed']:
        initial.update(gate='base_with_omission_metadata_exceeds_cap',
                       requested_ids=identities, admitted_ids=[],
                       omitted=empty['inputs']['retrieved_sources']['admission']['omitted'])
        return {'request': None, 'receipt': initial, 'attempts': []}
    attempts = []
    for index, (_, meta) in enumerate(normalized):
        value = candidate(admitted + [index])
        measured = preflight(instruction, value, json_object=json_object, cap=cap, inherited_budget=inherited_budget)
        attempts.append({'source_id': meta['id'], 'allowed': measured['allowed'],
                         'body_sha256': measured['body_sha256'],
                         'total_bytes': measured['actual_total_bytes']})
        if measured['allowed']:
            admitted.append(index)
    final = candidate(admitted)
    receipt = preflight(instruction, final, json_object=json_object, cap=cap, inherited_budget=inherited_budget)
    if not receipt['allowed']:
        raise AssertionError('Admission final request grew beyond its measured cap')
    metadata = final['inputs']['retrieved_sources']['admission']
    receipt.update(requested_ids=identities, admitted_ids=metadata['admitted_ids'],
                   omitted=metadata['omitted'], admitted_sources=[normalized[i][1] for i in admitted])
    return {'request': final, 'receipt': receipt, 'attempts': attempts}


def citation_errors(answer, admitted_request):
    """Only supplied raw substrings can be cited; omitted sources remain unavailable."""
    records = {r['id']: r['text'] for r in admitted_request['inputs']['retrieved_sources']['observations']}
    errors = []
    for citation in answer.get('evidence', []):
        identity, quote = citation.get('observation_id'), citation.get('quote')
        if identity not in records:
            errors.append('unavailable source: ' + str(identity))
        elif not isinstance(quote, str) or not quote or quote not in records[identity]:
            errors.append('unsupported quote: ' + str(identity))
    return errors
