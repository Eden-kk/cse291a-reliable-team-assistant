"""Post-inference source-fidelity audit of the fixed Phase 1 development pilot."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / 'runs/phase1-port-corruption-v1'
PREVENTIVE = ROOT / 'runs/phase1-integrity-v2/always-verify'


def read(path):
    return json.loads(path.read_text())


summary = read(PILOT / 'summary.json')
collection_state = read(PILOT / 'collection/state.json')
collection_tools = sum(len(v['payload'].get('observations', [])) + len(v['payload'].get('tool_errors', []))
                       for v in collection_state.values())
rows = []
for name in ('clean', 'damaged', 'selective', 'restart', 'always-verify'):
    directory = PREVENTIVE if name == 'always-verify' else PILOT / name
    result = read(directory / 'result.json')
    scores = read(directory / 'scores.json')['scores']
    pristine = {o['id']: o for o in result['observation_archive']}
    mismatches = [e for e in result['answer'].get('evidence', [])
                  if e['observation_id'] not in pristine
                  or e['quote'] not in pristine[e['observation_id']]['text']]
    new_tools = len(result['observation_archive']) + len(result['tool_errors'])
    if name in ('clean', 'damaged'):
        new_tools -= collection_tools
    elif name == 'selective':
        damaged = read(PILOT / 'damaged/result.json')
        new_tools -= len(damaged['observation_archive']) + len(damaged['tool_errors'])
    rows.append({'method': name, 'native_JRA': scores['JRA'], 'native_ECR': scores['ECR'],
                 'contract_valid': scores['contract_valid'], 'source_mismatching_citations': mismatches,
                 'false_port_in_reasoning': '9556' in result['answer'].get('reasoning', ''),
                 'new_tools_in_this_phase': new_tools, 'model_calls_in_this_phase': len(result['model_calls']),
                 'tokens_in_this_phase': result['budget']['spent'],
                 'phase_seconds': result['phase_seconds']})
preventive = read(PREVENTIVE.parent / 'summary.json')
records = [read(p) for root in (PILOT, PREVENTIVE.parent) for p in root.glob('**/calls/*.json') if p.stem.isdigit()]
audit = {'scope': 'one seen case; fixed accidental evidence corruption; factual propagation, not incorrect root classification',
         'rows': rows, 'same_consumed_prefix_tokens': summary['prefix_tokens'],
         'logical_total_tokens': {'clean': summary['clean_total_tokens'],
                                 'selective': summary['selective_total_tokens'],
                                 'restart': summary['restart_total_tokens'],
                                 'preventive': preventive['tokens']},
         'preventive_injected': preventive['fault']['injected'],
         'verification_cpu_seconds': sum(c['cpu_seconds'] for c in preventive['checks']),
         'verification_wall_seconds': sum(c['wall_seconds'] for c in preventive['checks']),
         'verification_checked_observations': sum(c['checked_observations'] for c in preventive['checks']),
         'verification_repaired_observations': sum(len(c['repairs']) for c in preventive['checks']),
         'physical_model_tokens_including_invalid_arm': sum(r['tokens'] for r in records),
         'physical_calls_including_invalid_arm': len(records),
         'excluded': 'v1 always-verify expanded visible records with raw archive fields; invalid comparison, costs retained',
         'budget_limits': 'initial phase 360k; recovery methods additionally 360k each; preventive 360k. Not equal end-to-end caps.',
         'unmeasured': ['real unauthorized actions: tools are read-only', 'tracking overhead benchmark',
                        'other fault types/stages', 'general semantic verification', 'multi-case robustness']}
path = ROOT / 'docs/phase1-propagation-results.json'
with path.open('x') as handle:
    json.dump(audit, handle, indent=2)
print(json.dumps(audit, indent=2))
