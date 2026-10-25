"""Opt-in v5 development rules; does not mutate any frozen earlier contract."""
SEMANTIC_CLAIM_CONTRACT = '''
Material-claim evidence contract (development v5):
Preserve the distinction between event occurrence counts, attempted actions, and completed
outcomes. Repeated event records do not alone prove how many restarts or recoveries completed;
require direct outcome evidence before stating a completed-action count.
A Ready condition describes that condition at its recorded scope/time, not whole-system health.
Do not infer global health or absence of other faults from a limited status field.
A supported cause is not necessarily the sole cause. Claim exclusivity only with evidence
ruling out relevant alternatives; otherwise identify the supported cause without exclusivity.
Do not declare other configuration fields, ports, or timing settings correct merely because
a different fault is supported. Such correctness claims require their own evidence.
Event Age, relative ages, and aggregation windows do not prove exact coverage endpoints,
continuous duration, or a complete event history. Preserve uncertainty in chronology.
Omit optional assertions that lack evidence. Retain the supported main diagnosis, its source
quotes, and useful conclusions; do not hide missing support behind blanket uncertainty.
Apply these rules to every narrative sentence, structured claim, and action recommendation.
These generic rules supply no incident-specific values or correct diagnosis.'''


def semantic_receipt(enabled):
    import hashlib
    return {'semantic_v5': enabled,
            'semantic_contract_sha256': hashlib.sha256(SEMANTIC_CLAIM_CONTRACT.encode()).hexdigest() if enabled else None}


class SemanticProvider:
    """Explicitly opt in all calls while preserving the underlying global budget gate."""
    def __init__(self, backend): self.backend = backend
    @property
    def records(self): return self.backend.records
    @property
    def halted(self): return self.backend.halted
    async def complete(self, instruction, request):
        return await self.backend.complete(instruction + SEMANTIC_CLAIM_CONTRACT, request)


def with_semantic_contract(backend, enabled=False):
    return SemanticProvider(backend) if enabled else backend
