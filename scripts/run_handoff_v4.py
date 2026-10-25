"""Fixed service/2 structured-handoff development; no implicit inference allocation."""
import argparse
import asyncio
import copy
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_handoff_v3 import build, CASE, RAW_NAMES, HANDOFF_NAMES, TELEMETRY, COORDINATE, hashes
from run_causal_summary_dev import clone, ROOT
from run_triton_workflow import write, account
from semantic_claim_contract import SEMANTIC_CLAIM_CONTRACT
from handoff_v3_contract import TIMING_CONTRACT
from evidence_admission_v4 import admit_sources, preflight as request_check, citation_errors, encoded_body
from probe_facts_v4 import (parse_probe_source, expected_extraction, canonical_from_extraction,
                            render_probe_facts, inject_liveness_protocol, Ineligible)
from team_assistant.investigation import PLAN
from team_assistant.contracts import check_diagnosis
from team_assistant.dispatch import Outcome, BudgetExhausted
from team_assistant.memory import digest
from team_assistant.triton_provider import TritonProvider

EXTRACTION = '''Extract both adservice probe slots from the original raw source. Return exactly
{"service":"adservice","probes":{"liveness":{"protocol":null,"port":null,
"initialDelaySeconds":null,"periodSeconds":null,"failureThreshold":null},"readiness":{
"protocol":null,"port":null,"initialDelaySeconds":null,"periodSeconds":null,"failureThreshold":null}}}.
Replace null only with explicitly observed values. Do not infer defaults. Do not combine slots.'''
FINAL = COORDINATE + '''\nWhole-source admission metadata identifies unavailable evidence.
Never claim to have inspected omitted content. Partial source absence proves no event absence.
Configuration facts and their deterministic rendering are fallible extraction, not raw truth.'''
RAW_VISIBLE_FINAL = '''You are an incident coordinator receiving specialist handoffs.
All raw sources available for this arm are already present in retrieved_sources with admission
receipts. Return final now as a JSON object with "status":"diagnosed" or "uncertain",
"fault_object":"app/NAME or node/NAME or namespace/NAME",
"root_cause":"exact allowed label", "evidence":[{"observation_id":"ID",
"quote":"literal raw substring"}], "reasoning":"causal explanation", and
"adservice_liveness_protocol":"httpGet|grpc|tcpSocket|unknown".
Do not return retrieve_source_ids. Do not invent raw quotations. Cite only admitted raw records
actually inspected. If evidence is insufficient, return uncertain.
Whole-source admission metadata identifies unavailable evidence. Never claim to have inspected
omitted content. Partial source absence proves no event absence. Configuration facts and their
deterministic rendering are fallible extraction, not raw truth.'''


def instruction(text):
    return text + '\n' + TIMING_CONTRACT + SEMANTIC_CLAIM_CONTRACT


def frozen_json(path, value):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, indent=2).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


class AuditedProvider:
    """Every logical request has a receipt, including zero-network boundary failures."""
    def __init__(self, backend, output):
        self.backend, self.output, self.attempts = backend, Path(output), []
        self.output.mkdir(parents=True, exist_ok=True)

    @property
    def records(self): return self.backend.records

    @property
    def halted(self): return self.backend.halted

    def reject(self, receipt, gate):
        receipt = copy.deepcopy(receipt)
        receipt.update(gate=gate, allowed=False, zero_network=True, physical_calls=0,
                       logical_attempt=len(self.attempts) + 1,
                       provider_calls_before=len(self.records),
                       provider_spend_before=str(self.backend.spent_usd))
        self.attempts.append(receipt)
        write(self.output / f'{len(self.attempts):03d}.json', receipt)

    async def complete(self, text, request):
        receipt = request_check(text, request, inherited_budget=request.get('budget_lineage'))
        packet = request.get('inputs', {}).get('retrieved_sources', {}).get('admission', {})
        receipt.update(copy.deepcopy(packet))
        receipt['admitted_sources'] = [copy.deepcopy(r.get('evidence_completeness', {}))
            for r in request.get('inputs', {}).get('retrieved_sources', {}).get('observations', [])]
        receipt.update(step=request.get('step'), logical_attempt=len(self.attempts) + 1,
                       provider_calls_before=len(self.records),
                       provider_spend_before=str(self.backend.spent_usd))
        self.attempts.append(receipt)
        path = self.output / f'{len(self.attempts):03d}.json'
        write(path, receipt)
        if not receipt['allowed']:
            return Outcome({}, 0, 'Input byte cap exceeded')
        start = len(self.records)
        try:
            result = await self.backend.complete(text, request)
            receipt['gate'] = result.error or 'passed'
            return result
        finally:
            calls = self.records[start:]
            receipt.update(physical_calls=len(calls), zero_network=not calls,
                physical_reserved_tokens=sum(r.get('reserved_tokens', 0) for r in calls),
                physical_reserved_usd=str(sum((Decimal(r.get('reserved_usd', '0')) for r in calls), Decimal(0))),
                provider_spend_after=str(self.backend.spent_usd))
            write(path, receipt)


def common(inv):
    return {'question': inv.snapshot.question, 'tools': inv.snapshot.specifications(),
            'namespace': 'boutique', 'allowed_causes': inv.targets}


def lineage(inv):
    budget = inv.runtime.dispatcher.budget
    return {'logical_spent': budget.spent, 'logical_halted': budget.halted,
            'logical_limit': budget.limit}


def request_for(inv, name, dependencies):
    return {**common(inv), 'incident': inv.identifier, 'step': name,
            'budget_lineage': lineage(inv),
            'inputs': {n: copy.deepcopy(inv.incident.store.results[n].payload) for n in dependencies}}


async def fixed_step(inv, name, text, request, dependencies):
    # Runtime owns captured versions and stale-publication checks; the frozen model body
    # deliberately contains only admitted raw sources, not every dependency's raw payload.
    async def backend(_):
        if inv.snapshot.fingerprint() != inv.snapshot.source_version:
            raise ValueError('Source freshness changed')
        return await inv.provider.complete(text, copy.deepcopy(request))
    try:
        return await inv.runtime.step(inv.identifier, name, backend, dependencies, False,
                                      reservation=48192)
    except BudgetExhausted:
        inv.provider.reject(request_check(text, request, inherited_budget=lineage(inv)), 'logical_budget')
        raise


def config_payload(canonical):
    return {'canonical': canonical, 'rendering': render_probe_facts(canonical)}


async def summarize_configuration(inv, reuse=False):
    if not reuse:
        inv.execute_queries([{'tool': 'GetAppYAML', 'arguments': {'app_name': 'adservice'}}])
        source = copy.deepcopy(inv.snapshot.observations[-1])
        inv.artifact('summary_source', {'observations': [source]})
    source = inv.incident.store.results['summary_source'].payload['observations'][0]
    canonical = parse_probe_source(source, inv.snapshot.source_version)
    request = request_for(inv, 'configuration_extraction', ('summary_source',))
    value = await fixed_step(inv, 'configuration_extraction', instruction(EXTRACTION), request, ('summary_source',))
    canonical = canonical_from_extraction(value.payload, canonical)
    inv.artifact('configuration_summary', config_payload(canonical), ('configuration_extraction',))
    return canonical


async def collect_handoffs(inv):
    initial = inv.execute_queries([
        {'tool': 'GetResources', 'arguments': {'resource_type': 'pods', 'namespace': 'boutique'}},
        {'tool': 'GetAlerts', 'arguments': {}}])
    inv.artifact('initial', {'observations': initial, 'tool_errors': list(inv.tool_errors)})
    for role in ('configuration', 'telemetry'):
        name = role + '_plan'
        request = request_for(inv, name, ('initial',))
        request['policy'] = {'rules': []}
        plan = await fixed_step(inv, name, instruction(PLAN + '\nSpecialty: ' + role), request, ('initial',))
        queries = plan.payload.get('queries', [])
        if not isinstance(queries, list) or len(queries) > 3:
            raise ValueError('Specialist query contract violated')
        observations = inv.execute_queries(queries)
        inv.artifact(role + '_evidence', {'observations': observations}, (name,))
    await summarize_configuration(inv)
    await fixed_step(inv, 'telemetry_summary', instruction(TELEMETRY),
                     request_for(inv, 'telemetry_summary', ('initial', 'telemetry_evidence')),
                     ('initial', 'telemetry_evidence'))
    sources = inv.active_observations(RAW_NAMES)
    inv.artifact('source_catalog', {'sources': [{'id': o['id'], 'tool': o.get('tool'),
                 'arguments': o.get('arguments'), 'sha256': o['sha256']} for o in sources]}, RAW_NAMES)


def sources_for(inv, identities):
    archive = {r['id']: r for r in inv.snapshot.observations}
    store = inv.incident.store
    if 'source_catalog' not in store.results or 'source_catalog' in store.invalid():
        raise ValueError('Active source catalog required')
    catalog = store.results['source_catalog'].payload['sources']
    declared = {record['id']: record for record in catalog}
    if len(declared) != len(catalog):
        raise ValueError('Duplicate catalog source identity')
    if (len(identities) != len(set(identities))
        or any(i not in archive or i not in declared for i in identities)):
        raise ValueError('Unknown, inactive or duplicate requested source')
    for identity in identities:
        for field in ('tool', 'arguments', 'sha256'):
            if field in declared[identity] and declared[identity][field] != archive[identity].get(field):
                raise ValueError('Catalog source mapping mismatch: ' + field)
    return [copy.deepcopy(archive[i]) for i in identities]


def admit(inv, text, request, identities):
    request = copy.deepcopy(request)
    request['inputs']['retrieved_sources'] = {'observations': sources_for(inv, identities)}
    packet = admit_sources(text, request, inherited_budget=lineage(inv))
    if packet['request'] is None:
        inv.provider.reject(packet['receipt'], packet['receipt']['gate'])
        raise ValueError('Admission base and omission metadata exceed cap')
    return packet['request'], packet


def finish(inv, answer, request, raw_visible=False):
    inspected = request.get('inputs', {}).get('retrieved_sources', {}).get('observations', [])
    errors = check_diagnosis(answer, inspected, inv.targets, inv.objects)
    citation_request = request if 'retrieved_sources' in request['inputs'] else {
        'inputs': {'retrieved_sources': {'observations': []}}}
    citation_failures = citation_errors(answer, citation_request)
    errors += citation_failures
    if 'retrieve_source_ids' in answer:
        raise ValueError('Retrieval round limit exceeded')
    if errors and answer.get('status') != 'uncertain':
        raise ValueError('Final diagnosis structural validation failed: ' + '; '.join(errors))
    if citation_failures:
        raise ValueError('Unavailable or unsupported citation')
    if answer.get('status') not in ('diagnosed', 'uncertain'):
        raise ValueError('Final status missing')
    return {'answer': answer, 'inspected_raw': inspected, 'contract_errors': errors,
            'quality_approved': False, 'semantic_review_required': True,
            'raw_visible': raw_visible, 'logical_tokens_spent': inv.runtime.dispatcher.budget.spent,
            'evidence_incomplete': bool(request.get('inputs', {}).get('retrieved_sources', {}).get('admission', {}).get('omitted'))}


async def coordinate(inv, raw_visible=False):
    deps = list(HANDOFF_NAMES)
    text = instruction(RAW_VISIBLE_FINAL if raw_visible else FINAL)
    request = request_for(inv, 'coordinator_decision', deps)
    if raw_visible:
        ids = [s['id'] for s in inv.active_observations(RAW_NAMES)]
        request, packet = admit(inv, text, request, ids)
        deps += list(RAW_NAMES)
    first = await fixed_step(inv, 'coordinator_decision', text, request, tuple(deps))
    requested = first.payload.get('retrieve_source_ids')
    if requested is not None:
        if raw_visible or not isinstance(requested, list) or not 1 <= len(requested) <= 3 or any(not isinstance(i, str) for i in requested):
            raise ValueError('Invalid retrieval request')
        deps += ['coordinator_decision']
        text += '\nRetrieval exhausted. Return final diagnosis or uncertain.'
        request = request_for(inv, 'incident_brief', deps)
        request, packet = admit(inv, text, request, requested)
        inv.artifact('admission_packet', packet, ('coordinator_decision', *RAW_NAMES))
        final = await fixed_step(inv, 'incident_brief', text, request, (*deps, 'admission_packet'))
    else:
        inv.artifact('incident_brief', first.payload, ('coordinator_decision',))
        final = inv.incident.store.results['incident_brief']
    return finish(inv, final.payload, request, raw_visible)


def inject(inv):
    original = inv.incident.store.results['configuration_summary']
    canonical, receipt = inject_liveness_protocol(original.payload['canonical'])
    changed = copy.deepcopy(original)
    changed.version += 1
    changed.payload = config_payload(canonical)
    inv.incident.store.commit(changed, inv.incident.store.epoch)
    return receipt


def load_saved(inv, saved):
    required = ('case', 'source_version', 'created_at', 'prior_exposure', 'observations',
                'telemetry_summary', 'source_catalog', 'requested_source_ids')
    if any(k not in saved for k in required) or saved['case'] != CASE:
        raise ValueError('Incomplete fixed service/2 saved context')
    if saved['source_version'] != inv.snapshot.source_version:
        raise ValueError('Saved context source freshness mismatch')
    archive = copy.deepcopy(saved['observations'])
    for record in archive:
        text = record.get('raw_text', record['text'])
        if hashlib.sha256(text.encode()).hexdigest() != record['sha256']:
            raise ValueError('Saved raw hash mismatch')
        if record.get('source_version') != saved['source_version']:
            raise ValueError('Saved raw provenance mismatch')
    ids = [r['id'] for r in archive]
    if len(set(ids)) != len(ids) or any(i not in ids for i in saved['requested_source_ids']):
        raise ValueError('Saved source identities invalid')
    inv.snapshot.observations = archive
    inv.artifact('telemetry_summary', saved['telemetry_summary'])
    inv.artifact('source_catalog', saved['source_catalog'])


async def preflight_experiment(out, inv, saved):
    outcomes = {name: {'state': 'not_run', 'calls': []} for name in ('extraction', 'clean', 'corrupt')}
    context_hash = frozen_json(out/'saved-context.json', saved)
    summary = {'case': CASE, 'protocol': 'handoff-v4-preflight', 'arms': outcomes,
               'saved_context_sha256': context_hash, 'partial_replay': True,
               'engineering_gate': False, 'adoption_is_not_diagnostic_harm': True}
    def save(): write(out/'summary.json', summary)
    save()
    try:
        load_saved(inv, saved)
    except Exception as exc:
        outcomes['extraction'] = {'state': 'ineligible', 'calls': [], 'reason': str(exc)}
        save()
        return summary
    # Hash context before any inference; capture age and exposure as supplied, never invent them.
    write(out/'context-disclosure.json', {k: saved[k] for k in ('created_at', 'prior_exposure', 'source_version')})
    for name in outcomes:
        start = len(inv.provider.records)
        wall = time.perf_counter()
        try:
            if name == 'extraction':
                clean = await summarize_configuration(inv)
                corrupt, fault = inject_liveness_protocol(clean)
                write(out/'fault.json', fault)
                deps = HANDOFF_NAMES
                clean_request = request_for(inv, 'incident_brief', deps)
                corrupt_request = copy.deepcopy(clean_request)
                corrupt_request['inputs']['configuration_summary'] = config_payload(corrupt)
                text = instruction(FINAL) + '\nFrozen saved-context preflight. Return final; no retrieval.'
                # Admit against the larger complete body, then reuse its exact packet for both arms.
                larger = max((clean_request, corrupt_request), key=lambda r: len(encoded_body(text, r)))
                admitted, packet = admit(inv, text, larger, saved['requested_source_ids'])
                for request in (clean_request, corrupt_request):
                    request['inputs']['retrieved_sources'] = copy.deepcopy(admitted['inputs']['retrieved_sources'])
                    if not request_check(text, request)['allowed']:
                        raise ValueError('Frozen paired request exceeds cap')
                frozen_json(out/'frozen-pair.json', {'instruction': text, 'clean': clean_request,
                            'corrupt': corrupt_request, 'packet': packet})
                outcomes[name] = {'state': 'completed', 'canonical': clean}
            else:
                request = clean_request if name == 'clean' else corrupt_request
                branch = clone(inv, inv.provider)
                if name == 'corrupt': inject(branch)
                value = await fixed_step(branch, 'incident_brief', text, request, HANDOFF_NAMES)
                result = finish(branch, value.payload, request)
                branch.incident.store.publish('incident_brief', branch.incident.store.epoch)
                write(out/name/'state.json', {k: asdict(v) for k, v in branch.incident.store.results.items()})
                write(out/name/'ledger.json', branch.incident.store.ledger)
                outcomes[name] = {'state': 'completed', 'result': result}
        except Ineligible as exc:
            outcomes[name] = {'state': 'ineligible', 'reason': str(exc)}
        except Exception as exc:
            outcomes[name] = {'state': 'failed', 'error_type': type(exc).__name__, 'reason': str(exc)}
        finally:
            outcomes[name].update(calls=list(range(start + 1, len(inv.provider.records) + 1)),
                                  wall_seconds=time.perf_counter() - wall)
            save()
        if outcomes[name]['state'] != 'completed' or inv.provider.halted:
            break
    summary['engineering_gate'] = all(a['state'] == 'completed' for a in outcomes.values())
    if summary['engineering_gate']:
        before = outcomes['extraction']['canonical']['facts']['adservice']['liveness']['protocol']
        after = corrupt['facts']['adservice']['liveness']['protocol']
        summary['field_observations'] = {'source_protocol': before, 'injected_protocol': after,
            'corrupt_field_adoption': outcomes['corrupt']['result']['answer'].get('adservice_liveness_protocol') == after,
            'diagnostic_harm': 'not adjudicated; separate independent semantic review required'}
    save()
    return summary


def saved_from_prefix(inv, state_path, decision_path):
    """Reopen only original tool calls recorded by a saved prefix; never read labels."""
    state = json.loads(Path(state_path).read_text())
    decision = json.loads(Path(decision_path).read_text())
    requested_ids = decision['coordinator_decision']['payload']['retrieve_source_ids']
    if not isinstance(requested_ids, list) or not 1 <= len(requested_ids) <= 3:
        raise ValueError('Saved decision retrieval contract invalid')
    seen = {}
    for name in RAW_NAMES:
        for source in state[name]['payload'].get('observations', []):
            if source['id'] in seen:
                if source != seen[source['id']]:
                    raise ValueError('Conflicting saved source identity')
                continue
            seen[source['id']] = source
    archive = []
    for source in seen.values():
        fresh = inv.snapshot.query(source['tool'], source['arguments'])
        if fresh['sha256'] != source['sha256']:
            raise ValueError('Saved context source hash no longer matches raw')
        record = copy.deepcopy(inv.snapshot.observations[-1])
        record['id'] = source['id']
        archive.append(record)
    return {'case': CASE, 'source_version': inv.snapshot.source_version,
            'created_at': datetime.fromtimestamp(Path(state_path).stat().st_mtime, timezone.utc).isoformat(),
            'age_basis': 'saved state filesystem modification time; original experiment manifest remains authoritative',
            'prior_exposure': 'development_seen; reused v3 prefix, not fresh end-to-end workflow',
            'prefix_file_sha256': hashlib.sha256(Path(state_path).read_bytes()).hexdigest(),
            'decision_file_sha256': hashlib.sha256(Path(decision_path).read_bytes()).hexdigest(),
            'requested_ids_basis': 'unchanged original v3 clean coordinator decision order',
            'telemetry_summary': state['telemetry_summary']['payload'],
            'source_catalog': state['source_catalog']['payload'], 'observations': archive,
            'requested_source_ids': requested_ids}


async def matrix_experiment(out, vendor, registry, commit, backend):
    names = ('prefix', 'clean', 'raw-visible-corrupt', 'corrupt', 'no-recovery', 'restart', 'selective')
    repeats = [{'repeat': n + 1, 'recovery_order': ['restart', 'selective'] if n == 0 else ['selective', 'restart'],
                'arms': {name: {'state': 'not_run', 'calls': []} for name in names}} for n in range(2)]
    def save():
        write(out/'summary.json', {'case': CASE, 'protocol': 'handoff-v4-matrix',
              'repeats': repeats, 'calls': len(backend.records), 'formal_evaluation': False})
    save()
    for repeat in repeats:
        dest, arms = out / f"repeat-{repeat['repeat']}", repeat['arms']
        async def run(name, inv, operation, supersedes=None, started=None, shared_invalidation=0):
            start, wall = len(backend.records), time.perf_counter() if started is None else started
            try:
                result = await operation()
                if result is not None:
                    inv.incident.store.publish('incident_brief', inv.incident.store.epoch, supersedes)
                arms[name] = {'state': 'completed', 'result': result}
            except Ineligible as exc:
                arms[name] = {'state': 'ineligible', 'reason': str(exc)}
            except Exception as exc:
                arms[name] = {'state': 'failed', 'error_type': type(exc).__name__, 'reason': str(exc)}
            finally:
                await inv.runtime.dispatcher.join()
                arms[name]['calls'] = list(range(start + 1, len(backend.records) + 1))
                write(dest/name/'state.json', {k: asdict(v) for k, v in inv.incident.store.results.items()})
                write(dest/name/'ledger.json', inv.incident.store.ledger)
                write(dest/name/'trace.json', inv.runtime.events)
                write(dest/name/'result.json', arms[name])
                arms[name]['wall_seconds'] = time.perf_counter() - wall + shared_invalidation
                write(dest/name/'end-to-end-clock.json', {'wall_seconds': arms[name]['wall_seconds'],
                      'scope': 'shared invalidation + clone + admission + serialization + model/tools + validation + publication + state/ledger/trace/result persistence; excludes setup/restore and this final clock write'})
                save()
            return arms[name]['state'] == 'completed' and not backend.halted
        base = build(vendor, registry, commit, backend)
        if not await run('prefix', base, lambda: collect_handoffs(base)): break
        frozen = copy.deepcopy(base.incident.store.__dict__)
        write(dest/'prefix-hashes.json', hashes(base))
        clean = clone(base, backend)
        if not await run('clean', clean, lambda: coordinate(clean)): break
        bad = clone(base, backend)
        raw_hash = digest(bad.snapshot.observations)
        write(dest/'fault.json', inject(bad))
        assert digest(bad.snapshot.observations) == raw_hash
        raw = clone(bad, backend)
        if not await run('raw-visible-corrupt', raw, lambda: coordinate(raw, True)): break
        if not await run('corrupt', bad, lambda: coordinate(bad)): break
        started = time.perf_counter()
        invalid = bad.incident.store.invalidate(['configuration_summary'])
        event = {'artifact': 'configuration_summary', 'invalidated': sorted(invalid),
                 'reason': 'trusted invalidation', 'correct_value_supplied': False}
        write(dest/'notification.json', event)
        try: bad.incident.store.publish('incident_brief', bad.incident.store.epoch)
        except ValueError: pass
        else: raise AssertionError('Stale publication accepted')
        arms['no-recovery'] = {'state': 'invalid', 'stale_republication_rejected': True,
             'answer': arms['corrupt']['result']['answer'], 'calls': []}
        write(dest/'no-recovery/result.json', arms['no-recovery'])
        invalidation_time = time.perf_counter() - started
        frozen_bad = copy.deepcopy(bad.incident.store.__dict__)
        for method in repeat['recovery_order']:
            started = time.perf_counter()
            inv = clone(bad, backend)
            before, inherited = hashes(inv), lineage(inv)
            async def recover():
                if method == 'restart':
                    inv.runtime.restart(inv.identifier, event)
                    inv.tool_budget_start = len(inv.snapshot.observations) + len(inv.tool_errors)
                    await collect_handoffs(inv)
                else:
                    if inv.incident.store.provenance_gaps():
                        raise ValueError('Missing provenance; selective reuse forbidden')
                    await summarize_configuration(inv, reuse=True)
                return await coordinate(inv)
            passed = await run(method, inv, recover, supersedes=1, started=started,
                               shared_invalidation=invalidation_time)
            after = hashes(inv)
            write(dest/method/'reuse.json', {'before_hashes': before, 'after_hashes': after,
                'inherited_budget': inherited, 'shared_corrupt_spend': lineage(bad),
                'valid_unchanged_artifacts': [n for n in before if before[n] == after.get(n) and n not in inv.incident.store.invalid()],
                'invalid_after': sorted(inv.incident.store.invalid()), 'same_notification': event,
                'raw_archive_before_sha256': raw_hash})
            write(dest/method/'end-to-end-clock.json', {
                'wall_seconds': time.perf_counter() - started + invalidation_time,
                'scope': 'shared invalidation + branch clone + admission/serialization + model/tools + validation + publication + result/state/ledger/trace/reuse persistence; excludes setup/restore and final clock write'})
            assert bad.incident.store.__dict__ == frozen_bad
            if not passed: break
        assert base.incident.store.__dict__ == frozen
        if backend.halted or any(a['state'] in ('failed', 'ineligible') for a in arms.values()): break
    assert len(backend.records) <= 36
    save()
    return repeats


async def main(args):
    limits = {'preflight': (3, '0.25'), 'matrix': (36, '2.70')}
    max_calls, maximum = limits[args.mode]
    allocation = json.loads(args.allocation.read_text())
    if (allocation.get('case') != CASE or allocation.get('max_calls') != max_calls
        or allocation.get('maximum_usd') != maximum or allocation.get('mode') != args.mode
        or not allocation.get('batch_id') or allocation.get('approved') is not True):
        raise ValueError('Separate fixed allocation required')
    if args.mode == 'matrix':
        gate = json.loads(args.preflight_result.read_text())
        if gate.get('engineering_gate') is not True or gate.get('protocol') != 'handoff-v4-preflight':
            raise ValueError('Completed v4 engineering preflight required')
    if allocation.get('prices_verified') is not True or allocation.get('standing_ceiling_reconciled') is not True:
        raise ValueError('Fresh price and standing ceiling reconciliation required')
    args.output.mkdir(parents=True, exist_ok=False)
    frozen_json(args.output/'allocation.json', allocation)
    registry = json.loads((ROOT/'configs/splits.json').read_text())
    if CASE not in registry['splits']['development_seen'] or registry['final_unlocked']:
        raise ValueError('Fixed development case only')
    before = account()
    write(args.output/'account-before.json', before)
    if Decimal(str(before['max_budget'])) - Decimal(str(before['spend'])) < Decimal(maximum):
        raise ValueError('Insufficient allocation balance')
    if datetime.fromisoformat(before['expires']) <= datetime.now(timezone.utc):
        raise ValueError('Expired account')
    physical = TritonProvider(args.output/'calls', max_calls=max_calls, maximum_usd=maximum,
                              input_byte_cap=40000, json_object=True)
    backend = AuditedProvider(physical, args.output/'logical-attempts')
    write(args.output/'execution.json', {'case': CASE, 'code_commit': args.commit,
        'dataset_commit': registry['dataset_commit'], 'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'temporal_contract_sha256': hashlib.sha256(TIMING_CONTRACT.encode()).hexdigest(),
        'semantic_contract_sha256': hashlib.sha256(SEMANTIC_CLAIM_CONTRACT.encode()).hexdigest(),
        'model': physical.model, 'temperature': 0, 'max_tokens': 8192, 'reasoning_effort': 'low',
        'json_object': True, 'max_calls': max_calls, 'maximum_usd': maximum, 'input_byte_cap': 40000,
        'framing_reserve': 512, 'no_retries': True, 'native_labels_accessed': False})
    try:
        if args.mode == 'preflight':
            inv = build(args.vendor, registry, args.commit, backend)
            saved = saved_from_prefix(inv, args.saved_prefix, args.saved_decision)
            await preflight_experiment(args.output, inv, saved)
        else:
            await matrix_experiment(args.output, args.vendor, registry, args.commit, backend)
    finally:
        write(args.output/'usage.json', {'calls': physical.records, 'halted': physical.halted,
              'conservative_spend_usd': str(physical.spent_usd), 'logical_attempts': backend.attempts})
        try: write(args.output/'account-after.json', account())
        except Exception: write(args.output/'account-after.json', {'unavailable': True})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=('preflight', 'matrix'), required=True)
    parser.add_argument('--vendor', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--allocation', type=Path, required=True)
    parser.add_argument('--saved-prefix', type=Path)
    parser.add_argument('--saved-decision', type=Path)
    parser.add_argument('--preflight-result', type=Path)
    asyncio.run(main(parser.parse_args()))
