"""Post-inference native scoring; never imported by the model execution path."""
import argparse
import importlib
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from team_assistant.cloudops import load_vendor

ROOT = Path(__file__).resolve().parents[1]


def score(directory):
    manifest = json.loads((directory / 'manifest.json').read_text())
    result = json.loads((directory / 'result.json').read_text())
    vendor = ROOT.parent / 'preparation/291a-team-agent/vendor/Cloud-OpsBench'
    native = importlib.import_module(load_vendor(vendor) + '.evaluation')
    system, category, case = manifest['case'].split('/')
    label_root = vendor / 'process-label' / system / category
    truth = native.load_ground_truth(label_root, case)
    answer = result.get('answer', {})
    predictions = [answer] if answer.get('status') == 'diagnosed' else []
    scores = native.prediction_scores(predictions, truth)
    trace = [{'action_type': 'tool', 'action_name': obs['tool'], 'action_input': obs['arguments'],
              'observation': obs['text']} for obs in result.get('observations', [])]
    scores.update(native.process_scores(label_root, case, {'steps': trace},
                                        disable_getalerts_credit=category == 'performance' and scores['CA'] < 1))
    scores['contract_valid'] = not result.get('contract_errors', ['missing']) and not result.get('error')
    scores['budget_valid'] = not result.get('budget', {}).get('halted', True)
    report = {'case': manifest['case'], 'split': manifest['split'], 'scores': scores,
              'semantic_support_audit': 'pending; native scores do not establish causal support',
              'oracle': truth, 'scoring_stage': 'after completed model run; never supplied to model'}
    path = directory / 'scores.json'
    if path.exists():
        raise ValueError('Refusing to overwrite scores')
    path.write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != 'oracle'}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    score(parser.parse_args().directory)
