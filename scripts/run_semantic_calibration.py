"""Three new seen-input semantic-prompt development calls; previous results immutable."""
import argparse
import asyncio
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from run_timing_json_calibration import load_stage as load_v4
from run_timing_calibration import ROOT, TIMING_RULES, experiment, sha
from run_triton_workflow import account, write
from semantic_claim_contract import SEMANTIC_CLAIM_CONTRACT
from team_assistant.triton_provider import TritonProvider


def load_stage(root=ROOT):
    config = json.loads((root/'configs/semantic-calibration-v5.json').read_text())
    prior, frozen = load_v4(root)
    assert config['max_calls'] == 3 and config['maximum_usd'] == '0.25'
    assert config['input_byte_cap'] == 40000 and config['json_object'] is True
    assert config['semantic_contract_sha256'] == hashlib.sha256(SEMANTIC_CLAIM_CONTRACT.encode()).hexdigest()
    assert config['source_v4_protocol_sha256'] == sha(root/'configs/timing-json-calibration-v4.json')
    assert config['items'] == prior['items']
    revised = copy.deepcopy(frozen)
    for _, data in revised:
        # The shared runner appends unchanged TIMING_RULES after this new semantic contract.
        data['instruction'] += SEMANTIC_CLAIM_CONTRACT
    return config, revised


async def main(args):
    config, frozen = load_stage()
    args.output.mkdir(parents=True, exist_ok=False)
    write(args.output/'protocol.json', config)
    write(args.output/'execution.json', {'code_commit': args.commit,
          'runner_sha256': sha(Path(__file__)), 'timing_rules': TIMING_RULES,
          'semantic_contract': SEMANTIC_CLAIM_CONTRACT, 'json_object': True,
          'instruction_order': ['original', 'semantic_v5', 'timing_v3'],
          'independent_review_required': True, 'transport_only_followup': False,
          'based_on_seen_development_errors': True, 'retroactive_repair': False})
    before = account(); write(args.output/'account-before.json', before)
    assert before['max_budget'] - before['spend'] >= 0.25
    assert datetime.fromisoformat(before['expires']) > datetime.now(timezone.utc)
    backend = TritonProvider(args.output/'calls', max_calls=3, maximum_usd='0.25',
                             input_byte_cap=40000, json_object=True)
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
