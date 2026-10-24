"""Offline replay of two explicitly seen prefixes; no provider or benchmark reads."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from team_assistant.generic_faults import inject_numeric_port
from team_assistant.propagation import verify_inputs
from team_assistant.cloudops import Snapshot
from team_assistant.state import ExecutionStore, Result

ROOT = Path(__file__).resolve().parents[1]
PREFIXES = {
    'boutique/runtime/1': 'recovery-boundaries-20261024/handoff-expiry/prefix',
    'boutique/performance/1': 'cross-case-recovery-20261024/performance/prefix',
}


def validate():
    registry=json.loads((ROOT/'configs/splits.json').read_text())
    assert registry['final_unlocked'] is False
    reports=[]
    for case,relative in PREFIXES.items():
        assert case in registry['splits']['development_seen']
        path=ROOT/'runs'/relative
        state=json.loads((path/'state.json').read_text())
        raw=json.loads((path/'result.json').read_text())
        archive=copy.deepcopy(raw['observation_archive'])
        source=raw['source_version']
        revision_mismatch = case == 'boutique/performance/1'
        if revision_mismatch:
            # The saved model prefix is revision-wrapped; exercise that rejection below.
            # Positive roundtrip uses archived original GetAppYAML records, explicitly
            # scripted acquisition rather than claiming this is the model prefix.
            original_state = copy.deepcopy(state)
            state['configuration_evidence']['payload']['observations'] = [
                Snapshot.visible(o) for o in archive if o['tool'] == 'GetAppYAML']
        store=ExecutionStore()
        for name,data in state.items():
            data=copy.deepcopy(data); data['citations']=set(data['citations'])
            store.results[name]=Result(**data)
            store.source_versions.update(data['sources'])
            store.shared_versions.update(data['shared_reads'])
        inv=SimpleNamespace(incident=SimpleNamespace(store=store),snapshot=SimpleNamespace(
            observations=archive,source_version=source,fingerprint=lambda:source))
        telemetry=copy.deepcopy(store.results['telemetry_evidence'])
        audit=inject_numeric_port(inv)
        assert audit['injected']
        assert archive==raw['observation_archive']
        invalid=sorted(store.invalid())
        assert 'diagnosis_0' in invalid and 'public-status' in invalid
        try: store.publish('public-status',0)
        except ValueError: rejected=True
        else: raise AssertionError('stale publication accepted')
        revision_rejected = None
        if revision_mismatch:
            bad = copy.deepcopy(inv)
            bad.incident.store.results['configuration_evidence'].payload = original_state['configuration_evidence']['payload']
            try: verify_inputs(bad,['configuration_evidence'])
            except ValueError as exc:
                assert 'Unknown observation identity' in str(exc)
                revision_rejected = True
            else: raise AssertionError('unknown revision was repaired')
        repair=verify_inputs(inv,['configuration_evidence'])
        source_by_id={o['id']:Snapshot.visible(o) for o in archive}
        assert all(o==source_by_id[o['id']] for o in store.results['configuration_evidence'].payload['observations'])
        assert telemetry==store.results['telemetry_evidence']
        assert not verify_inputs(inv,['configuration_evidence'])['repairs']
        assert 'diagnosis_0' in store.invalid()
        reports.append(dict(case=case,prefix=relative,injection=audit,invalid_descendants=invalid,
            obsolete_publication_rejected=rejected,exact_source_repair=True,
            telemetry_unchanged=True,archive_unchanged=True,repair_idempotent=True,
            acquisition='original-source YAML replay' if revision_mismatch else 'saved actual collection prefix',
            mismatched_revision_rejected=revision_rejected))
    return dict(validation='offline mechanics only; not new model propagation or quality evidence',
                live_model_calls=0,heldout_access=False,cases=reports)


if __name__=='__main__':
    print(json.dumps(validate(),indent=2))
