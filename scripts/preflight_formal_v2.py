"""Network-disabled preparation on an already-seen case; never launch evaluation."""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import socket
import sys
import tempfile
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'tests')]
from run_formal_v2 import runtime_receipt, verify
from test_triton_workflow import Controlled, workflow

def main(args):
    receipt = {'formal_started':False, 'online_calls':0, 'heldout_opened':False,
               'checked_at':datetime.now(timezone.utc).isoformat(),
               'runtime':runtime_receipt(ROOT/'requirements.lock')}
    manifest = ROOT/'configs/formal-freeze-v2.json'
    receipt['freeze_sha256'] = hashlib.sha256(manifest.read_bytes()).hexdigest()
    checks = json.loads(manifest.read_text())['files']
    receipt['frozen_files'] = len(checks)
    assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in checks.items())
    assert receipt['runtime']['valid'], 'Dependency mismatch'
    registry = json.loads((ROOT/'configs/splits.json').read_text())
    assert registry['final_unlocked'] is False
    marker = Path.home()/'.local/state/cse291a-team-assistant/formal-v2-launch.json'
    receipt['existing_launch_marker'] = marker.exists()
    assert not marker.exists(), 'Prior launch requires reconciliation'
    with patch.object(socket.socket, 'connect', side_effect=AssertionError('Network forbidden')):
        try: verify(ROOT, {}, datetime(2026,10,29,tzinfo=timezone.utc))
        except ValueError as exc:
            assert 'Approval' in str(exc)
            receipt['missing_approval_rejected'] = True
        else: raise AssertionError('Missing approval accepted')
        backend = Controlled(True)
        with tempfile.TemporaryDirectory() as tmp:
            for i, order in enumerate((('restart','selective'),('selective','restart'))):
                folder = Path(tmp)/str(i)
                asyncio.run(workflow.matrix(folder,args.vendor,registry,'offline-fixture',backend,order=order))
                result = json.loads((folder/'summary.json').read_text())
                assert result['formal_evaluation'] is False
                assert len(result['arms']['expiry-selective']['calls']) == 3
                assert result['arms']['clean']['quality_approved'] is False
        assert len(backend.records)==34
        receipt['mock_calls'] = 34
    receipt['passed'] = True
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as f: json.dump(receipt,f,indent=2)
    print(json.dumps(receipt,indent=2))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--vendor',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    main(p.parse_args())
