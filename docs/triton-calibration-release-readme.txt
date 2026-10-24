Reliable Team Assistant — Phase1 review release October24,2026
Solo: Yiting Dai. Option5. TritonAI-calibrated local review release, not a submission receipt.
Report: two body pages plus one timeline appendix. Phase1 deadlineNov4; exact cutoff/upload slot not yet verified. FinalDec10.

Offline reproduction, Python>=3.11:
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
PYTHONPATH=src .venv/bin/python scripts/verify_phase1_package.py
PYTHONPATH=src .venv/bin/python scripts/verify_phase1_release.py
PYTHONPATH=src .venv/bin/python scripts/verify_phase1_oct24.py
First check verifies hashes, older matrix/native scores, controls and current tests. Second retains Oct21 failure/unknown-usage accounting. Third reconciles Oct24 53calls/953990tokens against raw per-arm usage (advanced-prefix copies are not new calls), recomputes scores, verifies all-expiry request equivalence and offline generic-fault replay. No model call, AWS creation or heldout read. Snapshot-dependent tests skip without full pinned benchmark; skips are not passes. Never run live experiment runners merely to verify the archive.

Main report evidence: docs/{cross-case-recovery,recovery-boundaries,all-expiry}-20261024-results.json and corresponding runs. Older Oct8/Oct21 records remain separate; do not pool as independent samples. Generic mechanics plus12 real TritonAI diagnosis calls are included; no full-workflow semantic recovery benefit is established. Full development suite185passed; extracted-package suite separately recorded in release-verification.txt outside immutable ZIP.

Dataset: https://github.com/LLM4Ops/Cloud-OpsBench at ea05dafbc87b5985df8721622d63d4229c2b4224. MIT license, evaluator code and three seen-case labels bundled in vendor_offline; no heldout content. Full snapshot acquisition: clone official repository and checkout that exact commit, set CLOUDOPS_VENDOR to it only for development tests. Saved observed evidence needed for offline replay is bundled. Native labels are only for post-inference scoring.

Course API:12 calls/126439tokens/$0.14692735 settled charge; balance$49.85307265, expiryNov7. v1 six truncated answers remain failures. v2 low/8192returns six JSON objects, but performance root/independent-evidence issues and0/2injected-port adoption remain. See docs/triton-calibration-20261024.txt and its semantic audit/scores. No full workflow publication measured. Formal protocol v1 is not launchable under its old output cap; amendment and remaining gates are in experiment-plan.txt. No credentials are included.

Additional offline checks for the new evidence:
PYTHONPATH=src .venv/bin/python scripts/summarize_triton_calibration.py > /tmp/triton-summary.json
cmp /tmp/triton-summary.json docs/triton-calibration-20261024-results.json
PYTHONPATH=src .venv/bin/python scripts/score_triton_calibration.py > /tmp/triton-scores.json
cmp /tmp/triton-scores.json docs/triton-calibration-20261024-scores.json
These validate all12 frozen request hashes/receipts, recompute usage/cost summaries and native/contract scores after the saved evidence review. Review is not blinded. Raw remote input and runner copies preserve each configuration, including the older runner's exact code.


MANIFEST.sha256.json names source revision and every staged byte. Historical run manifests retain their original source revision. Local user paths in historical traces are provenance, not credentials. Course-only source exports excluded. Do not upload publicly. AI implementation/analysis/report assistance is disclosed. Previous dated release directories remain unchanged.
