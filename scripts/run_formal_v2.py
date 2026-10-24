"""Fail-closed formal execution. No case data is opened until all gates pass."""
import argparse
import asyncio
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from team_assistant.dispatch import Outcome
from team_assistant.triton_provider import TritonProvider
from run_triton_workflow import account, matrix, write
ROOT = Path(__file__).resolve().parents[1]

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def verify(root, approvals, now):
    spec = json.loads((root/'configs/formal-evaluation-v2.json').read_text())
    manifest_path = root/'configs/formal-freeze-v2.json'
    manifest = json.loads(manifest_path.read_text())
    for name, expected in manifest['files'].items():
        path = (root/name).resolve()
        if not path.is_relative_to(root.resolve()) or sha(path) != expected:
            raise ValueError('Frozen file mismatch: '+name)
    window = json.loads((root/'configs/formal-window-v1.json').read_text())
    if not window['user_authorized_conditional_launch']:
        raise ValueError('No conditional launch authorization')
    if not datetime.fromisoformat(window['target_start_local']) <= now <= datetime.fromisoformat(window['latest_new_batch_start_local']):
        raise ValueError('Outside launch window')
    if approvals.get('freeze_sha256') != sha(manifest_path):
        raise ValueError('Approval does not bind this freeze')
    for name in ('benchmark_approval', 'resource_approval', 'model_access', 'workflow_validation'):
        item = approvals.get(name, {})
        path = Path(item.get('evidence_path', ''))
        if item.get('accepted') is not True or not path.is_file() or sha(path) != item.get('sha256'):
            raise ValueError('Missing verified evidence: '+name)
    checked = datetime.fromisoformat(approvals['checked_at'])
    if not timedelta(0) <= now-checked <= timedelta(hours=24):
        raise ValueError('Evidence review older than 24 hours')
    return spec, manifest, window

class DeadlineProvider(TritonProvider):
    def __init__(self, *args, stop, **kwargs):
        super().__init__(*args, **kwargs)
        self.stop = stop
    async def complete(self, instruction, request):
        # Include the complete timeout: no request may cross the online cutoff.
        if datetime.now(timezone.utc)+timedelta(seconds=self.timeout) >= self.stop:
            self.halted=True
            return Outcome({}, 0, 'Online deadline reached')
        return await super().complete(instruction, request)

async def run_units(output, vendor, cases, registry, commit, backend, freeze, units):
    for case in cases:
        for repeat in (1,2):
            name=f'{case.replace("/","-")}-r{repeat}'
            if backend.halted:return
            units[name]={'state':'started'};write(output/'units.json',units)
            order=('restart','selective') if repeat==1 else ('selective','restart')
            try:
                await matrix(output/name,vendor,registry,commit,backend,case=case,formal=True,order=order,freeze=freeze,artifact_root=ROOT)
                summary=json.loads((output/name/'summary.json').read_text())
                state='completed' if all(x.get('state') not in ('failed','not_run') for x in summary['arms'].values()) else 'incomplete'
                units[name]={'state':state,'order':order}
            except BaseException as exc:
                units[name]={'state':'failed','order':order,'error_type':type(exc).__name__}
                backend.halted=True
                write(output/'units.json',units)
                raise
            write(output/'units.json',units)
            if units[name]['state']=='incomplete':backend.halted=True

async def main(args):
    now = datetime.now(timezone.utc)
    approvals = json.loads(args.approvals.read_text())
    spec, manifest, window = verify(ROOT, approvals, now)
    # One durable marker per frozen protocol, independent of caller output path.
    marker = Path.home()/'.local/state/cse291a-team-assistant/formal-v2-launch.json'
    marker.parent.mkdir(parents=True,exist_ok=True)
    with marker.open('x') as f:
        json.dump({'started':now.isoformat(),'output':str(args.output.resolve()),'freeze_sha256':sha(ROOT/'configs/formal-freeze-v2.json')},f)
    args.output.mkdir(parents=True,exist_ok=False)
    write(args.output/'approvals.json',approvals)
    write(args.output/'freeze.json',manifest)
    units={f'{case.replace("/","-")}-r{repeat}':{'state':'not_run'} for case in spec['cases'] for repeat in (1,2)}
    write(args.output/'units.json',units)
    before=account();write(args.output/'account-before.json',before)
    if before['max_budget']-before['spend'] < 20:
        raise ValueError('Cannot reserve full formal budget')
    stop=datetime.fromisoformat(window['online_calls_stop_local'])
    if datetime.fromisoformat(before['expires']) <= stop:
        raise ValueError('Key does not cover entire online window')
    registry=json.loads((ROOT/'configs/splits.json').read_text())
    assert all(case in registry['splits']['final_locked'] for case in spec['cases'])
    registry['final_unlocked']=True
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    # Adapter guard validates the same immutable configuration and prompt artifacts.
    freeze={'code_commit':commit,'dataset_commit':spec['dataset_commit'],'model':spec['provider']['model'],
            'registry_sha256':hashlib.sha256(json.dumps(registry,sort_keys=True).encode()).hexdigest(),
            'artifacts':{name:{'path':'configs/formal-evaluation-v2.json','sha256':sha(ROOT/'configs/formal-evaluation-v2.json')} for name in ('memory','thresholds','faults','arrivals','budget')}}
    freeze['artifacts']['prompts']={'path':'src/team_assistant/investigation.py','sha256':sha(ROOT/'src/team_assistant/investigation.py')}
    backend=DeadlineProvider(args.output/'calls',max_calls=102,maximum_usd='20',stop=stop)
    try:
        await run_units(args.output,args.vendor,spec['cases'],registry,commit,backend,freeze,units)
    finally:
        write(args.output/'usage.json',{'calls':backend.records,'halted':backend.halted,'conservative_spend_usd':str(backend.spent_usd)})
        write(args.output/'units.json',units)
        try:write(args.output/'account-after.json',account())
        except Exception:write(args.output/'account-after.json',{'unavailable':True})
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--vendor',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--approvals',type=Path,required=True)
    asyncio.run(main(p.parse_args()))
