"""Offline mechanism/accounting report for organic v3 and seeded v3 saved artifacts."""
import argparse
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
from handoff_v3_contract import inspect_schema

ORGANIC_ARMS = ('prefix','clean','raw-visible-corrupt','corrupt','no-recovery','restart','selective')
SEEDED_ARMS = ('no-recovery','restart','selective')


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def money(record):
    value=record.get('headers',{}).get('x-litellm-response-cost')
    if value is None: return None
    try: result=Decimal(str(value))
    except InvalidOperation: return None
    return result if result.is_finite() and result>=0 else None


def accounting(records):
    unknown_tokens=[r['call'] for r in records if type(r.get('tokens')) is not int or r['tokens']<0]
    unknown_cost=[r['call'] for r in records if money(r) is None]
    known_tokens=sum(r['tokens'] for r in records if type(r.get('tokens')) is int and r['tokens']>=0)
    known_cost=sum((money(r) for r in records if money(r) is not None),Decimal(0))
    return {'calls':len(records),'tokens':None if unknown_tokens else known_tokens,
            'known_tokens':known_tokens,'tokens_unknown_calls':unknown_tokens,
            'header_cost_usd':None if unknown_cost else str(known_cost),
            'known_header_cost_usd':str(known_cost),'header_cost_missing_calls':unknown_cost,
            'provider_error_calls':[r['call'] for r in records if r.get('error') is not None],
            'input_cap_exceeded_calls':[r['call'] for r in records if r.get('input_bytes',0)+512>40000]}


def mechanism(path,data,expected_supersedes):
    reuse=read(path/'reuse.json'); state=read(path/'state.json'); ledger=read(path/'ledger.json')
    findings=[]
    retained=None
    if reuse:
        retained=reuse.get('valid_unchanged_artifacts',[])
        for name in retained:
            if name not in reuse.get('before_hashes',{}) or reuse['before_hashes'][name]!=reuse.get('after_hashes',{}).get(name):
                findings.append('retained_hash_mismatch:'+name)
        if state is not None:
            for name,value in state.items():
                if digest(value)!=reuse.get('after_hashes',{}).get(name): findings.append('state_hash_mismatch:'+name)
        else: findings.append('missing_state_for_reuse')
    checks=None
    if state is not None and ledger:
        entry=ledger[-1]; brief=state.get('incident_brief',{})
        invalid=(reuse or {}).get('invalid_after')
        parents_ok=all(p in state and state[p]['version']==v for p,v in brief.get('inputs',{}).items())
        checks={'payload_matches_current':entry.get('payload')==brief.get('payload'),
                'version_matches_current':entry.get('version')==brief.get('version'),
                'result_is_brief':entry.get('result')=='incident_brief',
                'expected_supersession':entry.get('supersedes')==expected_supersedes,
                'direct_parents_current':parents_ok,
                'brief_not_invalid':None if invalid is None else 'incident_brief' not in invalid}
        # A completed recovery must contain saved invalidity information, not infer it from absence.
        checks['mechanically_valid_recovery']=all(v is True for v in checks.values()) if expected_supersedes is not None else None
        if data.get('state')=='completed' and expected_supersedes is not None and not checks['mechanically_valid_recovery']:
            findings.append('completed_recovery_publication_check_failed')
    elif data.get('state')=='completed' and expected_supersedes is not None:
        findings.append('missing_completed_recovery_publication_evidence')
    clock=read(path/'end-to-end-clock.json') or read(path/'clock.json')
    return {'retained_artifacts':retained,'retained_hashes_verified':None if reuse is None else not any('hash_mismatch' in x or 'missing_state' in x for x in findings),
            'invalid_after':None if reuse is None else reuse.get('invalid_after'),
            'publication_checks':checks,'end_to_end_clock':clock,'integrity_findings':findings}


def summarize(root):
    root=Path(root); summary=read(root/'summary.json'); protocol=read(root/'protocol.json',{})
    if summary is None: raise ValueError('Missing summary.json; cannot infer assigned protocol')
    usage=read(root/'usage.json'); records=[] if usage is None else usage.get('calls',[])
    by_id={r['call']:r for r in records}; findings=[]
    if len(by_id)!=len(records): findings.append('duplicate_physical_call_ids')
    organic='repeats' in summary
    if organic:
        schema=read(root/'schema-preflight.json',{}).get('eligibility',{})
        expected=schema.get('before'); expected_repeats=protocol.get('repeats',2)
        units={r['repeat']:r for r in summary['repeats']}
    else:
        state=read(root/'seeded-state.json',{})
        observations=state.get('summary_source',{}).get('payload',{}).get('observations',[])
        expected=inspect_schema(observations[0]).get('before') if len(observations)==1 else None
        expected_repeats=1; units={1:{'arms':summary.get('arms',{})}}
    rows=[]; assigned_ids=[]
    for repeat in range(1,expected_repeats+1):
        arms=units.get(repeat,{}).get('arms',{})
        for arm in ORGANIC_ARMS if organic else SEEDED_ARMS:
            data=arms.get(arm,{'state':'missing_slot','calls':[]}); ids=data.get('calls',[])
            assigned_ids.extend(ids); missing=[i for i in ids if i not in by_id]
            metrics=accounting([by_id[i] for i in ids if i in by_id])
            if missing or usage is None: metrics.update(tokens=None,header_cost_usd=None)
            result=data.get('result') or {}; answer=result.get('answer') or data.get('answer') or {}
            value=answer.get('adservice_liveness_protocol')
            path=root/f'repeat-{repeat}'/arm if organic else root/arm
            mech=mechanism(path,data, (1 if organic else 2) if arm in ('restart','selective') else None)
            row={'repeat':repeat,'arm':arm,'state':data['state'],'assigned_call_ids':ids,'assigned_calls':len(ids),
                 'missing_call_records':missing,**metrics,'observed_protocol_field':value,
                 'expected_protocol_from_raw':expected,'correct_field':None if value is None or expected is None else value==expected,
                 'answer_status':answer.get('status'),'recommendation_present':bool(answer.get('recommendation')),
                 'contract_errors':result.get('contract_errors'),'logical_total_tokens':result.get('logical_tokens_spent'),
                 'stale_republication_rejected':data.get('stale_republication_rejected'),**mech,
                 'semantic_success':'not assessed; field and mechanism checks do not establish semantic correctness'}
            if missing: row['integrity_findings'].append('assigned_call_record_missing')
            rows.append(row)
    duplicates=sorted({i for i in assigned_ids if assigned_ids.count(i)>1})
    unassigned=sorted(set(by_id)-set(assigned_ids))
    if duplicates: findings.append('physical_calls_assigned_multiple_times')
    if unassigned: findings.append('physical_calls_without_arm_assignment')
    totals=accounting(records)
    if usage is None: totals.update(calls=None,tokens=None,header_cost_usd=None)
    cap=protocol.get('max_calls',36 if organic else 9)
    if len(records)>cap: findings.append('call_cap_exceeded')
    conservative=None if usage is None else usage.get('conservative_spend_usd')
    if conservative is not None and protocol.get('maximum_usd') is not None:
        if Decimal(conservative)>Decimal(protocol['maximum_usd']): findings.append('conservative_usd_cap_exceeded')
    before=read(root/'account-before.json',{}); settled=read(root/'account-settled.json',{})
    delta=None
    if 'spend' in before and 'spend' in settled: delta=Decimal(str(settled['spend']))-Decimal(str(before['spend']))
    return {'track':'organic' if organic else 'seeded_conditional','case':summary.get('case'),
            'totals':totals,'provider_conservative_spend_usd':conservative,'assigned_arms':len(rows),'rows':rows,'usage_file_present':usage is not None,
            'missing_slots':[{'repeat':r['repeat'],'arm':r['arm']} for r in rows if r['state']=='missing_slot'],
            'uncompleted_slots':[{'repeat':r['repeat'],'arm':r['arm'],'state':r['state']} for r in rows if r['state'] not in ('completed','invalid')],
            'unassigned_call_ids':unassigned,'duplicate_assignments':duplicates,
            'settled_account_delta_usd':None if delta is None else str(delta),
            'settled_delta_matches_header_cost':None if delta is None or totals['header_cost_usd'] is None else abs(delta-Decimal(totals['header_cost_usd']))<Decimal('0.000000001'),
            'integrity_findings':findings,'semantic_review_required':True,
            'publication_scope':'Saved payload/version/direct-parent and invalid_after evidence only; not external publication or full source/semantic revalidation.'}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('root',type=Path)
    print(json.dumps(summarize(parser.parse_args().root),indent=2))
