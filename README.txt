Reliable Team Assistant
CSE291A Systems for LLMs and AI Agents — Option 5

Formal implementation of versioned procedural learning, cross-incident recovery and shared-budget scheduling.
Submitted proposal: docs/submitted-proposal.txt
Detailed experiment plan: docs/experiment-plan.txt
Progress and next steps: docs/status.txt

Development (Python >=3.11, standard library core):
  .venv/bin/python -m unittest discover -s tests -v
  PYTHONPATH=src .venv/bin/python -m team_assistant.demo --output runs/startup

The demo uses an explicitly controlled backend. It verifies software integration, not model accuracy or production latency.
No cloud instance or paid API is started by tests/demo. Live providers and benchmark inference are explicit opt-in steps.

Historical evidence is outside this repository at ../preparation/291a-team-agent/.
Do not modify its frozen datasets/results. Formal runs belong under runs/ and must capture config, code and dataset hashes.
Dataset paths, secrets, raw third-party data and run output are not committed.
No remote repository is configured; publishing needs a separate request.
