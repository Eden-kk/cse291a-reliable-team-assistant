"""Verify new saved evidence and generic mechanics without network/model calls."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from validate_generic_fault import validate
ROOT=Path(__file__).resolve().parents[1]
def read(p): return json.loads(p.read_text())

def main():
    rows=[]; scored=0
    with tempfile.TemporaryDirectory() as temp:
        for name,number,tokens,call_field in [
            ('cross-case-recovery-20261024',26,487598,'total_physical_calls'),
            ('recovery-boundaries-20261024',18,313367,'task_calls'),
            ('all-expiry-20261024',9,153025,'physical_calls')]:
            base=ROOT/'runs'/name; compact=read(ROOT/'docs'/(name+'-results.json'))
            usage=[c for p in base.rglob('usage.json') if p.parent.name!='advanced-prefix'
                   for c in read(p)['calls']]
            assert len(usage)==compact[call_field]==number
            assert all(c['tokens'] is not None and not c['error'] for c in usage)
            assert sum(c['tokens'] for c in usage)==compact['known_tokens']==tokens
            for path in base.rglob('scores.json'):
                out=Path(temp)/'scores.json'
                subprocess.run([sys.executable,str(ROOT/'scripts/score_run.py'),str(path.parent),
                    '--vendor',str(ROOT/'vendor_offline/Cloud-OpsBench'),'--output',str(out)],
                    check=True,stdout=subprocess.DEVNULL)
                assert read(out)['scores']==read(path)['scores'],str(path)
                scored+=1
            rows.append(dict(batch=name,calls=number,tokens=tokens))
    base=ROOT/'runs/all-expiry-20261024/all-expiry'
    for index in range(1,4):
        a=read(base/'restart/calls'/f'{index:03d}.request.json')
        b=read(base/'selective/calls'/f'{index:03d}.request.json')
        assert a==b
    summary=read(ROOT/'docs/all-expiry-20261024-results.json')
    assert all(summary['arms'][arm]['retained_artifacts']==0 for arm in ('restart','selective'))
    assert validate()==read(ROOT/'docs/generic-fault-validation-20261024.json')
    assert not read(ROOT/'configs/splits.json')['final_unlocked']
    print(json.dumps(dict(batches=rows,total_calls=53,total_tokens=953990,native_scores_reproduced=scored,
                         generic_mechanics_reproduced=True,all_expiry_requests_identical=True,live_model_calls=0)))
if __name__=='__main__': main()
