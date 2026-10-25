"""Six paired seen-input generation calls; no repairs, retries, or outcome selection."""
import argparse
import asyncio
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_triton_workflow import ROOT, account, write
from team_assistant.triton_provider import TritonProvider

TIMING_RULES = '''
Temporal evidence contract for this final answer:
Avoid unnecessary timing assertions in reasoning. For every material temporal assertion,
add a temporal_claims entry with claim, kind (observed_event/configuration/derived_inference),
source_id, source_field, value, unit, temporal_scope, and assumptions. Use null for unknown
values rather than guessing. An empty temporal_claims list is allowed if no such claim is made.
Distinguish an interval between checks from a count of consecutive failures and from an
observed event time. Preserve the identity and units of each field. A configured threshold
or interval alone does not establish actual restart timing. Derived timing requires explicit
assumptions and must not be presented as an observed duration. Use only actually inspected
raw evidence; do not cite specialist summaries as primary evidence. If support is absent,
omit optional detail or explicitly state uncertainty. Preserve supported diagnosis and required
evidence; do not replace them with blanket uncertainty. These rules supply no case answer.'''


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_inputs(root=ROOT):
    config = json.loads((root/'configs/timing-calibration-v3.json').read_text())
    assert config['max_calls'] == 6 and config['maximum_usd'] == '0.50'
    assert config['input_byte_cap'] == 40000 and config['reserved_tokens_per_call'] == 48192
    assert [x['id'] for x in config['items']] == ['item-08', 'item-10', 'item-11']
    frozen = []
    for item in config['items']:
        path = root/item['path']
        if sha(path) != item['sha256']:
            raise ValueError('Frozen input hash mismatch')
        data = json.loads(path.read_text())
        assert data['request']['step'] == 'incident_brief'
        assert sorted(item['order']) == ['original', 'revised']
        frozen.append((item, data))
    return config, frozen


async def experiment(out, backend, config, frozen):
    assignments = [{'item': item['id'], 'variant': variant, 'state': 'not_run'}
                   for item, _ in frozen for variant in item['order']]
    def save():
        write(out/'summary.json', {'assignments': assignments, 'calls': len(backend.records),
                                   'halted': backend.halted, 'formal_evaluation': False})
    save()
    for assignment in assignments:
        item, data = next((i, d) for i, d in frozen if i['id'] == assignment['item'])
        instruction = data['instruction'] + (TIMING_RULES if assignment['variant'] == 'revised' else '')
        request = copy.deepcopy(data['request'])
        path = out/'arms'/f"{item['id']}-{assignment['variant']}"
        write(path/'input.json', {'instruction': instruction, 'request': request})
        assignment.update(input_sha256=sha(path/'input.json'), frozen_input_sha256=item['sha256'])
        start = len(backend.records)
        try:
            result = await backend.complete(instruction, request)
            assignment.update(state='failed' if result.error else 'completed', error=result.error,
                              tokens=result.tokens, output=result.value)
        except Exception as exc:
            assignment.update(state='failed', error=type(exc).__name__)
        assignment['calls'] = list(range(start + 1, len(backend.records) + 1))
        write(path/'result.json', assignment)
        save()
        if assignment['state'] == 'failed' or backend.halted:
            break
    assert len(backend.records) <= config['max_calls']
    save()


async def main(args):
    config, frozen = load_inputs()
    args.output.mkdir(parents=True, exist_ok=False)
    write(args.output/'protocol.json', config)
    write(args.output/'execution.json', {'code_commit': args.commit,
          'runner_sha256': sha(Path(__file__)), 'timing_rules': TIMING_RULES,
          'instruction_only_intervention': True, 'independent_review_required': True})
    before = account(); write(args.output/'account-before.json', before)
    assert before['max_budget'] - before['spend'] >= float(config['maximum_usd'])
    assert datetime.fromisoformat(before['expires']) > datetime.now(timezone.utc)
    backend = TritonProvider(args.output/'calls', max_calls=6, maximum_usd='0.50', input_byte_cap=40000)
    assert backend.reservation_tokens == 48192
    try:
        await experiment(args.output, backend, config, frozen)
    finally:
        write(args.output/'usage.json', {'calls': backend.records, 'halted': backend.halted,
                                       'conservative_spend_usd': str(backend.spent_usd)})
        try: write(args.output/'account-after.json', account())
        except Exception: write(args.output/'account-after.json', {'unavailable': True})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--commit', required=True)
    asyncio.run(main(parser.parse_args()))
