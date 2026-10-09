Reliable Team Assistant — CSE291A Option 5 Phase 1
Author: Yiting Dai (solo)

STATUS
Local deliverable complete; author review and course submission pending.
Official deadline: October 28, 2026 (Phase 1 code/data 15%, report 10%).
Report: output/pdf/phase1-report.pdf — 2 report pages + 1 timeline appendix page.
Canvas /courses/78326/assignments checked October 8: no Phase 1 upload item visible.
The public course project page and Option 5 specification govern this package; later rubric changes must be checked before submission.

SCOPE
Three seen Cloud-OpsBench incidents; four predeclared intervention conditions.
Two accidental conditions have complete source-integrity, full-restart and selective comparisons (performance prevention completed via a separately recorded retry).
Service-address mutation did not match and is retained as not-injected.
Adversarial clean/damaged trajectories completed with a resisted instruction. Recovery stopped on provider 503; no adversarial recovery success claimed. Retries are bounded and retained.
Offline fake-provider controls cover timeout/unknown usage, stale writes, simulated authorization/publication and tracking CPU. They are not live model safety measurements.
Memory learning, scheduling experiments, broad robustness and final held-out evaluation remain Phase 2 work.

CONTENTS
src/ and scripts/: workflow, fault harness, state/recovery, scoring and report reproduction.
configs/phase1/: frozen interventions; configs/splits.json: seen/learning/validation/final separation.
docs/phase1-matrix-results.json: all measured arms, source fidelity, calls/tools/tokens, partial failures.
docs/phase1-controls.json: offline software controls, synthetic CPU samples and baselines.
runs/phase1-matrix/: raw requests/responses, source observations, manifests, saved states, scores and failures.
runs/phase1-port-corruption-v1 and runs/phase1-integrity-v2: earlier pilot; invalid old verifier explicitly excluded from matrix.
vendor_offline/Cloud-OpsBench: MIT native evaluator/tools code and only the three required case-label directories; no benchmark snapshots or final-case content.
MANIFEST.sha256.json: byte checksums for every bundled file except the checksum index itself.

OFFLINE REPRODUCTION (NO MODEL CALLS)
From the extracted package directory, with Python >=3.11:
  python3 -m venv .venv
  .venv/bin/python -m pip install -r requirements.lock
  .venv/bin/python scripts/verify_phase1_package.py
This checks file hashes, regenerates metrics, re-scores each completed trajectory using bundled native evaluator code, runs controls and unit tests. CPU timings naturally vary.
Snapshot execution tests skip when the external benchmark checkout is absent; unit tests for budget/state/contracts/controls still run. The original development workspace ran all 51 tests with its checkout.
The checksum index checks integrity, not authorship; a signature is not supplied.

Optional PDF regeneration:
  .venv/bin/python -m pip install -r requirements-report.txt
  .venv/bin/python scripts/render_phase1_report.py
Regenerated PDF metadata can change, so use a separate copy if checking original package hashes.

LIVE REPRODUCTION (CONSUMES YOUR MODEL QUOTA)
Install/authenticate the Codex CLI using your own subscription. The adapter uses gpt-6-astra with tools disabled; no API keys are bundled. Current model alias availability and CLI flags may change.
Create the benchmark checkout at ../preparation/291a-team-agent/vendor/Cloud-OpsBench relative to this package:
  git clone --filter=blob:none --no-checkout https://github.com/LLM4Ops/Cloud-OpsBench.git ../preparation/291a-team-agent/vendor/Cloud-OpsBench
  git -C ../preparation/291a-team-agent/vendor/Cloud-OpsBench sparse-checkout init --cone
  git -C ../preparation/291a-team-agent/vendor/Cloud-OpsBench sparse-checkout set agents/cloudops_agent benchmark/boutique/runtime/1 benchmark/boutique/service/1 benchmark/boutique/performance/1 process-label/boutique/runtime/1 process-label/boutique/service/1 process-label/boutique/performance/1
  git -C ../preparation/291a-team-agent/vendor/Cloud-OpsBench checkout ea05dafbc87b5985df8721622d63d4229c2b4224
The runner requires its own clean Git checkout. For an extracted source archive, initialize and commit the provided code before live execution; the new manifest records that commit, not the original history.
  git init
  git add .
  git commit -m 'Import Phase 1 artifact for reproduction'
  .venv/bin/python scripts/run_propagation.py --spec configs/phase1/runtime-port.json --output runs/new-runtime
  .venv/bin/python scripts/score_run.py runs/new-runtime/damaged
Use a new output directory. Run other specs separately; never overwrite original traces. Live results are stochastic. No promise of identical output/latency is made.
Provider failures and unknown usage must remain recorded. The budget charges a 60,000-token reservation when usage is unknown; that is an accounting convention, not measured actual tokens or dollars.

LICENSE AND DATA
Cloud-OpsBench upstream commit and MIT license are in vendor_offline/Cloud-OpsBench. This subset is only for offline scoring. Original snapshot outputs within traces retain their benchmark provenance. Do not interpret predeclared corruption as actual production evidence.
No messages, operational commands, or coursework were submitted externally by this experiment.
