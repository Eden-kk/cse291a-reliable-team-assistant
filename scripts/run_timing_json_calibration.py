"""New three-call format calibration; retains original v3 failures unchanged."""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from run_timing_calibration import ROOT, TIMING_RULES, experiment, load_inputs, sha
from run_triton_workflow import account, write
from team_assistant.triton_provider import TritonProvider


def load_stage(root=ROOT):
    config = json.loads((root/'configs/timing-json-calibration-v4.json').read_text())
    _, original = load_inputs(root)
    assert config['max_calls'] == 3 and config['maximum_usd'] == '0.25'
    assert config['input_byte_cap'] == 40000 and config['json_object'] is True
    assert config['timing_rules_sha256'] == hashlib.sha256(TIMING_RULES.encode()).hexdigest()
    assert config['source_protocol_sha256'] == sha(root/'configs/timing-calibration-v3.json')
    expected = [{**item, 'order': ['revised']} for item, _ in original]
    assert config['items'] == expected
    return config, [(item, old[1]) for item, old in zip(expected, original)]


async def main(args):
    config, frozen = load_stage()
    args.output.mkdir(parents=True, exist_ok=False)
    write(args.output/'protocol.json', config)
    write(args.output/'execution.json', {'code_commit': args.commit,
          'runner_sha256': sha(Path(__file__)), 'timing_rules': TIMING_RULES,
          'json_object': True, 'independent_review_required': True,
          'transport_only_followup': True, 'retroactive_v3_repair': False})
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
