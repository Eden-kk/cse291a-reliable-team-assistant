# Reliable Team Assistant

CSE 291A Systems for LLMs and AI Agents, Option 5. This solo project studies whether dependency-aware selective recovery can preserve supported incident responses while reducing recomputation after a trusted partial invalidation.

## Phase 1 result

The frozen Phase 1 evaluation contains three Cloud-OpsBench cases with two repeats per case. Under partial invalidation, selective recovery and full restart each passed 3 of 4 eligible strict checks. Selective recovery used 4 rather than 12 new model calls and 33,260 rather than 64,902 incremental tokens, a 48.8% reduction. Under the all-expiry negative control, both methods required 18 calls, showing that selective recovery falls back to restart-equivalent work when no artifact is safe to reuse.

These results support bounded cost proportionality and conservative fallback. They do not establish paired quality equivalence, higher diagnosis accuracy, natural fault prevalence, or repair of an organically propagated wrong final diagnosis.

## Repository map

- `src/team_assistant/`: runtime, versioned artifacts, dependency tracking, invalidation, recovery, dispatch, provenance, and evaluation logic.
- `tests/`: unit and integration tests.
- `configs/`: frozen experiment and evaluation configurations.
- `scripts/build_project_mainpath.py`: rebuilds the reviewer-facing aggregate from saved evidence without model calls.
- `scripts/verify_formal_release.py`: verifies hashes, retained slots, saved scores, calls, tokens, and cost offline.
- `docs/project-mainpath-20261025.txt`: frozen research question, evidence, conclusion, and limits.
- `phase1/Phase1-Report.pdf`: two-page report plus one-page timeline appendix.
- `phase1/Phase1-Evidence.zip`: frozen code/data evidence bundle with its own manifest and offline verification instructions.

## Offline verification

Python 3.11 or newer is recommended.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m unittest discover -s tests -v
PYTHONPATH=src .venv/bin/python scripts/build_project_mainpath.py \
  --output output/project-mainpath

# Verify the immutable Phase 1 evidence in a clean extraction.
mkdir -p /tmp/cse291a-phase1
unzip -q phase1/Phase1-Evidence.zip -d /tmp/cse291a-phase1
cd /tmp/cse291a-phase1
python3 scripts/verify_formal_release.py
```

The verification path is offline and makes no model calls. The frozen formal run used the course TritonAI service; credentials are not included. Raw evidence retains failures, ineligible slots, and negative controls rather than filtering them out.

## Data and provenance

The benchmark source is Cloud-OpsBench at commit `ea05dafbc87b5985df8721622d63d4229c2b4224` under its MIT license. The Phase 1 evidence archive contains the saved inputs, outputs, score receipts, cost ledger, manifests, and verification tooling needed to audit the reported result. It contains no API key or personal paid-account credential.

## Integrity

Frozen submission hashes:

- `Phase1-Report.pdf`: `b53e451038e120ae1a0bb202f2f34af7de8c0872be2973652de064f13b3c7cfd`
- `Phase1-Evidence.zip`: `6580af4ee063d077a73d718f1b1a7138552aef12708fa467457a0f0b94c2e8a6`

