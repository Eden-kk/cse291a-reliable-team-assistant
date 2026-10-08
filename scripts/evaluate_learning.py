"""Produce a write-once gate receipt; missing semantic audits fail closed."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from team_assistant.learning_gate import evaluate
from team_assistant.memory import digest

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text())


def load_run(path):
    manifest, result, score = [read(path / name) for name in ('manifest.json', 'result.json', 'scores.json')]
    audit_path = path / 'semantic-audit.json'
    audit = read(audit_path) if audit_path.exists() else {}
    binding = digest({'manifest': manifest, 'result': result, 'scores': score})
    supported = (audit.get('supported') is True and audit.get('evidence_sha256') == binding
                 and bool(audit.get('reviewer')) and bool(audit.get('rationale')))
    return {'manifest': manifest, 'scores': score['scores'], 'tokens': result.get('budget', {}).get('spent'),
            'semantic_supported': bool(supported), 'artifact_sha256': binding}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('spec', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    spec = read(args.spec)
    pairs = [{'case': row['case'], 'baseline': load_run(ROOT / row['baseline']),
              'candidate': load_run(ROOT / row['candidate'])} for row in spec['pairs']]
    result = evaluate(read(ROOT / spec['candidate']), read(ROOT / spec['baseline']), pairs,
                      read(ROOT / 'configs/splits.json'), read(ROOT / 'configs/learning-gate.json'),
                      spec.get('generation_tokens'))
    with args.output.open('x') as out:
        json.dump(result, out, indent=2)
    print(json.dumps(result, indent=2))
