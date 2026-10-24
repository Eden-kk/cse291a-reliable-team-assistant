Reliable Team Assistant — Phase1 review release October24,2026
Solo: Yiting Dai. Option5. Local review materials, not a submission receipt.
Report: two body pages plus one timeline appendix. Phase1 deadlineNov4; exact cutoff/upload slot not yet verified. FinalDec10.

Offline reproduction, Python>=3.11:
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
PYTHONPATH=src .venv/bin/python scripts/verify_phase1_package.py
PYTHONPATH=src .venv/bin/python scripts/verify_phase1_release.py
PYTHONPATH=src .venv/bin/python scripts/verify_phase1_oct24.py
First check verifies hashes, older matrix/native scores, controls and current tests. Second retains Oct21 failure/unknown-usage accounting. Third reconciles Oct24 53calls/953990tokens against raw per-arm usage (advanced-prefix copies are not new calls), recomputes scores, verifies all-expiry request equivalence and offline generic-fault replay. No model call, AWS creation or heldout read. Snapshot-dependent tests skip without full pinned benchmark; skips are not passes. Never run live experiment runners merely to verify the archive.

Main report evidence: docs/{cross-case-recovery,recovery-boundaries,all-expiry}-20261024-results.json and corresponding runs. Older Oct8/Oct21 records remain separate; do not pool as independent samples. New generic validation is OFFLINE mechanics only, not TritonAI semantic recovery. Full development suite185passed; extracted-package suite separately recorded in release-verification.txt outside immutable ZIP.

Dataset: https://github.com/LLM4Ops/Cloud-OpsBench at ea05dafbc87b5985df8721622d63d4229c2b4224. MIT license, evaluator code and three seen-case labels bundled in vendor_offline; no heldout content. Full snapshot acquisition: clone official repository and checkout that exact commit, set CLOUDOPS_VENDOR to it only for development tests. Saved observed evidence needed for offline replay is bundled. Native labels are only for post-inference scoring.

Course API: authenticated key currently allows7routes, $50 limit/$0spent, RPM100; expiresNov7. Credential not included. Public GLM5.3/Flash candidate prices fit budget on paper; model quality, charged-price/output-cap behavior and credential renewal still need verification. Historical gpt-6-astra Codex subscription measurements are not TritonAI inference or dollar measurements. Formal launch remains disabled and heldout locked.

MANIFEST.sha256.json names source revision and every staged byte. Historical run manifests retain their original source revision. Local user paths in historical traces are provenance, not credentials. Course-only source exports excluded. Do not upload publicly. AI implementation/analysis/report assistance is disclosed. Previous dated release directories remain unchanged.
