"""Create a portable, credential-free Phase 1 archive with a per-file hash index."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / 'dist/phase1/team-assistant-phase1'
if STAGE.exists():
    raise ValueError('Refuse to overwrite package staging directory')
STAGE.mkdir(parents=True)


def copy(source, relative):
    target = STAGE / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store'))
    else:
        shutil.copy2(source, target)


for name in ('src', 'scripts', 'tests', 'configs'):
    copy(ROOT / name, name)
for name in ('README.txt', 'PHASE1_README.txt', '.gitignore', 'pyproject.toml', 'requirements.lock', 'requirements-report.txt'):
    copy(ROOT / name, name)
for path in (ROOT / 'docs').iterdir():
    if path.is_file() and path.name != 'survey-receipt.txt':
        copy(path, path.relative_to(ROOT))
copy(ROOT / 'output/pdf/phase1-report.pdf', 'output/pdf/phase1-report.pdf')
for name in ('phase1-matrix', 'phase1-port-corruption-v1', 'phase1-integrity-v2'):
    copy(ROOT / 'runs' / name, Path('runs') / name)
copy(ROOT / 'runs/phase1-controls.json', 'runs/phase1-controls.json')
vendor = ROOT.parent / 'preparation/291a-team-agent/vendor/Cloud-OpsBench'
base = Path('vendor_offline/Cloud-OpsBench')
copy(vendor / 'agents/cloudops_agent', base / 'agents/cloudops_agent')
copy(vendor / 'LICENSE', base / 'LICENSE')
for category in ('runtime', 'service', 'performance'):
    rel = Path('process-label/boutique') / category / '1'
    copy(vendor / rel, base / rel)
(STAGE / base / 'SOURCE.txt').write_text('https://github.com/LLM4Ops/Cloud-OpsBench\nCommit: ea05dafbc87b5985df8721622d63d4229c2b4224\nMIT. Offline scoring subset; full snapshot checkout is separate.\n')
for path in STAGE.rglob('*'):
    if path.is_file() and (path.suffix in ('.pem','.key') or path.name in ('auth.json','.env')):
        raise ValueError('Forbidden credential file in package')
files = {str(p.relative_to(STAGE)): hashlib.sha256(p.read_bytes()).hexdigest()
         for p in sorted(STAGE.rglob('*')) if p.is_file()}
manifest = {'code_commit': subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip(),
            'dataset_commit': 'ea05dafbc87b5985df8721622d63d4229c2b4224',
            'scope': 'local Phase 1 deliverable; no credentials, no final benchmark cases', 'files': files}
(STAGE / 'MANIFEST.sha256.json').write_text(json.dumps(manifest, indent=2)+'\n')
archive = ROOT / 'output/phase1-submission.zip'
with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
    for path in sorted(STAGE.rglob('*')):
        if path.is_file():
            z.write(path, str(path.relative_to(STAGE.parent)))
print(json.dumps({'archive': str(archive), 'stage': str(STAGE), 'files': len(files),
                  'bytes': archive.stat().st_size, 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}))
