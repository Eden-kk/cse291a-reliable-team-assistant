"""Offline artifact checks: hashes, recomputed metrics, native scores, and controls."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text())


def run(*args):
    subprocess.run([sys.executable, *map(str, args)], cwd=ROOT, check=True,
                   stdout=subprocess.DEVNULL)


def main():
    index = read(ROOT / 'MANIFEST.sha256.json')
    for name, expected in index['files'].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise ValueError('Checksum mismatch: ' + name)
    with tempfile.TemporaryDirectory(prefix='phase1-verification-') as tmp:
        tmp = Path(tmp)
        run('scripts/summarize_phase1.py', '--output', tmp / 'matrix.json')
        if read(tmp / 'matrix.json') != read(ROOT / 'docs/phase1-matrix-results.json'):
            raise ValueError('Matrix aggregation differs')
        run('scripts/run_phase1_controls.py', '--output', tmp / 'controls.json')
        if not read(tmp / 'controls.json')['passed']:
            raise ValueError('Software controls failed')
        for number, row in enumerate(read(tmp / 'matrix.json')['rows']):
            directory = ROOT / row['source']
            target = tmp / f'score-{number}.json'
            run('scripts/score_run.py', directory, '--vendor', ROOT / 'vendor_offline/Cloud-OpsBench', '--output', target)
            if read(target)['scores'] != read(directory / 'scores.json')['scores']:
                raise ValueError('Native scores differ: ' + row['source'])
        subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-q'], cwd=ROOT, check=True)
    print(json.dumps({'checksums': len(index['files']), 'matrix_reproduced': True,
                      'native_scores_reproduced': len(read(ROOT / 'docs/phase1-matrix-results.json')['rows']),
                      'controls_passed': True, 'live_model_calls': 0}))


if __name__ == '__main__':
    main()
