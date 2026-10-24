Kubernetes liveness timing gate: offline development replay

Source semantics
https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-probes/
periodSeconds defines configured cadence; failureThreshold counts consecutive failures. Neither establishes exact restart timing. A failure threshold of three does not mean three failures within each probe period. No exact restart latency is derived by multiplying settings.

Method and result
All 15 saved handoff packets were replayed without inference. Only raw DescribeResource records present in coordinator inputs were used; the separate review source archive was not used as model-visible evidence. Every original answer and packet SHA256 is retained in results.json. No historic answer, frozen runner, source record or independent review was edited.
Three explicit per-period claims (items 08, 10, 11) are blocked, matching the three previously independently flagged timing narratives. Six other timing narratives are held for review; these include ambiguous "each cycle", general correctness assertions and factual event spans. The remaining six have no detected timing-pattern conflict. This status is NOT an answer-quality pass. Other unsupported health/fix claims remain outside scope.

Safe use and limitations
This is a deterministic lexical development gate, not a general truth oracle, independent model replication or end-to-end correction result. It was developed after observing the saved examples; its 3/3 match is retrospective and cannot estimate unseen error recall. Unsupported phrasing, negation, questions, quotation, missing evidence and conflicting configurations require review. The caller must provide raw records for the intended resource. A model may evade this small vocabulary; a no-finding is not permission to publish. The proposed wording is a separate review suggestion retaining source IDs and quoted configuration, never an automatic rewrite. Future generation must constrain timing statements to cited typed fields, then undergo fresh independently scored evaluation.

Reproduce from repository root
PYTHONPATH=src python -m unittest discover -s tests -p test_probe_timing.py
python scripts/replay_probe_timing.py output/handoff-review-20261024/packets /tmp/probe-timing-replay.json
The output path must not already exist. No network or API required.
