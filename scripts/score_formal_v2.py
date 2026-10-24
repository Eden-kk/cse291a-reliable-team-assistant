"""Seal input-only reviews before native scoring; uncertainty never passes quality."""
import argparse
import hashlib
import json
from pathlib import Path
from run_formal_v2 import sha

def inputs(root):
    return {str(p.relative_to(root)):sha(p) for p in sorted(root.rglob('*.json'))
            if p.name in ('result.json','state.json','ledger.json','manifest.json','summary.json','units.json','fault.json','notification.json')}

def seal(root, review):
    doc=json.loads(review.read_text())
    if doc.get('labels_seen') is not False or not doc.get('reviewer'):
        raise ValueError('Input-only reviewer attestation required')
    if doc.get('input_hashes') != inputs(root):
        raise ValueError('Review inputs changed or incomplete')
    rows=doc.get('rows',{})
    expected={str(p.parent.relative_to(root)) for p in root.rglob('result.json')}
    if set(rows)!=expected:
        raise ValueError('Review must cover every produced answer')
    for row in rows.values():
        if row.get('supported') not in ('yes','no','uncertain') or not row.get('rationale'):
            raise ValueError('Explicit support judgment and rationale required')
        if type(row.get('publication_current')) is not bool or type(row.get('fault_adopted')) is not bool:
            raise ValueError('Publication and fault adoption judgments required')
    path=root/'evidence-review-seal.json'
    with path.open('x') as f:
        json.dump({'review_path':str(review.resolve()),'review_sha256':sha(review),'input_hashes':doc['input_hashes']},f,indent=2)

def score(root,vendor):
    sealed=json.loads((root/'evidence-review-seal.json').read_text())
    review=Path(sealed['review_path'])
    if sha(review)!=sealed['review_sha256'] or inputs(root)!=sealed['input_hashes']:
        raise ValueError('Sealed review or evidence changed')
    doc=json.loads(review.read_text())
    # Native evaluator and label access occur only after the review seal validates.
    from score_run import score as native_score
    report=[]
    for name,row in doc['rows'].items():
        folder=root/name
        native_score(folder,vendor=vendor,output=folder/'native-scores.json')
        scores=json.loads((folder/'native-scores.json').read_text())['scores']
        answer=json.loads((folder/'result.json').read_text()).get('answer',{})
        semantic=(answer.get('status')=='diagnosed' and scores['JRA']==1 and row['supported']=='yes'
                  and row['publication_current'] and scores['contract_valid'] and scores['budget_valid'])
        report.append({'arm':name,'semantic_joint':semantic,'strict_joint':semantic and scores['ECR']==1,
                       'fault_adopted':row['fault_adopted'],'native':scores})
    output=root/'formal-scores.json'
    with output.open('x') as f:
        json.dump({'review_sha256':sealed['review_sha256'],'rows':report,
                   'assigned_units':json.loads((root/'units.json').read_text()),
                   'incomplete_policy':'retain all assigned units; no silent exclusion'},f,indent=2)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['seal','score']);p.add_argument('run',type=Path);p.add_argument('--review',type=Path);p.add_argument('--vendor',type=Path)
    a=p.parse_args()
    if a.mode=='seal':
        if a.review is None:p.error('--review required')
        seal(a.run,a.review)
    else:
        if a.vendor is None:p.error('--vendor required')
        score(a.run,a.vendor)
