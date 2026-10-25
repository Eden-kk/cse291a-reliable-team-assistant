Reliable Team Assistant
CSE291A Systems for LLMs and AI Agents — Option 5

Versioned procedural memory, dependency-aware recovery and shared-budget execution.
Submitted proposal: docs/submitted-proposal.txt (immutable submission snapshot)
Experiment plan: docs/experiment-plan.txt
Evidence and limitations: docs/experiment-progress.txt
Current progress: docs/status.txt

Setup (Python >=3.11):
  python3 -m venv .venv
  .venv/bin/python -m pip install -r requirements.lock

Offline verification:
  .venv/bin/python -m unittest discover -s tests -v
  PYTHONPATH=src .venv/bin/python -m team_assistant.demo --output runs/my-demo

Frozen project main path (offline; no model calls):
  PYTHONPATH=src .venv/bin/python scripts/build_project_mainpath.py --output output/project-mainpath
This produces the formal comparison, bounded conclusion, final-report skeleton and a compact ZIP.
It does not rerun or modify the frozen formal evidence.

Local benchmark checkout expected at ../preparation/291a-team-agent/vendor/Cloud-OpsBench,
commit ea05dafbc87b5985df8721622d63d4229c2b4224 (MIT).
Core tests do not need a model; snapshot tests use the existing local benchmark and are skipped if absent.
The controlled demo uses simulated outputs/usage and proves integration only.

Real development run (consumes existing Codex subscription; no paid API keys):
  .venv/bin/python scripts/run_development.py --case boutique/runtime/1 --output runs/my-dev
  .venv/bin/python scripts/score_run.py runs/my-dev
Use --policy-file configs/historical-candidate.json to test the historical candidate.
Use --split shakedown only for reserved shakedown IDs; final cases are not accepted by this runner.
Original questions, oracle labels and inference/scoring boundaries are handled by separate code paths.

Controlled wrong-claim repair on a seen case:
  .venv/bin/python scripts/run_recovery.py --output runs/my-recovery
Live three-group revocation window:
  .venv/bin/python scripts/run_multigroup.py --scheduler fair --output runs/my-window
Score each result directory afterwards. Both commands consume model quota; use tmux/caffeinate for long runs.

Architecture:
  cloudops.py       validated read-only snapshots, source hashes, missing/error distinction
  provider.py       recorded isolated Codex CLI requests, usage and rejected-output accounting
  investigation.py  two query-planning specialists and raw-evidence coordinator
  state.py          artifact versions, actual-input dependencies, invalidation, publication history
  memory.py         pinned versions, validation receipt checks, activation and revocation
  dispatch.py       concurrency, token reservations, actual settlement, FIFO/fair aging
  runtime.py        captured inputs, scoped commit validation and external publication validation
  faults.py         explicit experimental corruption; excluded from ordinary runs
  run_gate.py       final-set freeze checks for future evaluation runners

Historical evidence remains outside this repository; do not overwrite it.
Raw run output belongs under runs/ and is not committed. All failures and superseded experiments remain there.
No remote is configured. Public release, production integration and final course submissions are separate work.
Provider implementation reference: https://developers.openai.com/cookbook/examples/codex/build_iterative_repair_loops_with_codex
