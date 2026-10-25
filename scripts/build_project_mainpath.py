"""Build the Phase 2/final-report skeleton from saved evidence only."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from team_assistant.project_eval import build_summary


def read(path: Path) -> dict:
    return json.loads(path.read_text())


def render_report(summary: dict) -> str:
    formal = summary["formal"]
    partial = formal["partial_invalidation"]
    expiry = formal["all_expiry"]
    seeded = summary["seeded_conditional"]
    return f"""# Reliable Team Assistant: Final Report Skeleton

## 1. Problem and research question

Team assistants reuse evidence and intermediate work across an incident. When a trusted event invalidates part of that state, should the system restart every agent or recompute only the dependency closure? This project tests whether dependency-aware selective recovery can preserve supported outputs while reducing recomputation.

## 2. System and safety contract

The system records artifact versions and actual consumed-input dependencies, invalidates transitive dependents, rejects obsolete writes, and publishes a correction linked to the prior answer. Reuse is allowed only for artifacts whose sources and dependencies remain valid. Missing provenance or complete expiry triggers conservative restart. Tools are read-only and publication is locally simulated.

```mermaid
flowchart LR
    S[Versioned read-only sources] --> P[Specialist artifacts]
    P --> C[Coordinator answer]
    C --> U[Versioned publication]
    N[Trusted invalidation] --> D[Dependency closure]
    D -->|Independent valid work remains| R[Selective recomputation]
    D -->|All work invalid or provenance incomplete| F[Full restart]
    R --> V[Evidence and publication validation]
    F --> V
    V --> U2[Linked corrective publication]
```

## 3. Evaluation protocol

The frozen formal evaluation contains {formal['design']['independent_cases']} Cloud-OpsBench cases, two repeats each, and {formal['design']['assigned_units']} assigned units. Partial invalidation was successfully injected in {formal['design']['partial_invalidation_eligible_units']} units; the other {formal['design']['not_injected_units_retained']} remain in the denominator and are not retargeted. The comparison reports correctness before savings and retains failures, uncertainty, and ineligible conditions.

## 4. Core results

| Condition | Restart | Selective | Interpretation |
|---|---:|---:|---|
| Partial invalidation strict pass | {partial['restart_strict_pass']}/{partial['strict_pass_denominator']} | {partial['selective_strict_pass']}/{partial['strict_pass_denominator']} | Same observed count; failures differ, so equivalence is not established |
| Partial invalidation calls | {partial['restart']['calls']} | {partial['selective']['calls']} | {partial['call_reduction_percent']:.1f}% fewer |
| Partial invalidation tokens | {partial['restart']['tokens']:,} | {partial['selective']['tokens']:,} | {partial['token_reduction_percent']:.1f}% fewer |
| All-expiry strict pass | {expiry['restart_strict_pass']}/{expiry['strict_pass_denominator']} | {expiry['selective_strict_pass']}/{expiry['strict_pass_denominator']} | Same observed count |
| All-expiry calls | {expiry['restart']['calls']} | {expiry['selective']['calls']} | Selective recovery degenerates to restart-equivalent work |

The seeded conditional handoff supplies a mechanism check rather than a formal prevalence estimate. Both restart and selective recovery produced supported answers; selective recovery retained {seeded['selective_retained_artifacts']} valid artifacts and used {seeded['selective_tokens']:,} rather than {seeded['restart_tokens']:,} tokens ({seeded['token_reduction_percent']:.1f}% fewer).

## 5. Conclusion

{summary['conclusion']}

## 6. Limitations and final-report work

- Three independent cases are too few for a general quality claim.
- The input-only semantic review is AI-assisted and not an independent human adjudication.
- No formal case propagated an incorrect final diagnosis, so correction of that failure mode remains unproven.
- Observed request time is descriptive and does not establish a production latency guarantee.
- Course acceptance of Cloud-OpsBench still requires explicit confirmation or preserved approval evidence.

Before final submission, render the architecture diagram, add one quality/cost figure generated from `results.csv`, apply the course-required format, and add references plus any later instructor-approved evaluation. Do not rerun or overwrite the frozen formal evidence.
"""


def write_csv(path: Path, summary: dict) -> None:
    formal = summary["formal"]
    rows = []
    for condition, key_a, key_b in (
        ("partial_invalidation", "restart", "selective"),
        ("all_expiry", "restart", "selective"),
    ):
        block = formal[condition]
        for arm in (key_a, key_b):
            cost = block[arm]
            rows.append(
                {
                    "condition": condition,
                    "arm": arm,
                    "units": cost["units"],
                    "strict_pass": block[f"{arm}_strict_pass"],
                    "strict_pass_denominator": block["strict_pass_denominator"],
                    "calls": cost["calls"],
                    "tokens": cost["tokens"],
                    "seconds": f"{cost['seconds']:.6f}",
                }
            )
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def build(output: Path) -> dict:
    formal = read(ROOT / "docs/formal-results-20261024.json")
    seeded_root = ROOT / "runs/seeded-v3-20261024/seeded-handoff-v3-results"
    summary = build_summary(
        formal,
        read(seeded_root / "summary.json"),
        read(seeded_root / "usage.json"),
        read(seeded_root / "selective/reuse.json"),
        read(ROOT / "output/seeded-v3-semantic-review-20261024/review.json"),
    )
    output.mkdir(parents=True, exist_ok=True)
    (output / "results.json").write_text(json.dumps(summary, indent=2) + "\n")
    write_csv(output / "results.csv", summary)
    (output / "conclusion.txt").write_text(summary["conclusion"] + "\n")
    (output / "final-report-skeleton.md").write_text(render_report(summary))
    (output / "REPRODUCE.txt").write_text(
        "Offline only; no provider calls are made.\n\n"
        "PYTHONPATH=src .venv/bin/python scripts/build_project_mainpath.py "
        "--output output/project-mainpath\n"
    )
    archive = output / "project-mainpath.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name in ("results.json", "results.csv", "conclusion.txt", "final-report-skeleton.md", "REPRODUCE.txt"):
            bundle.write(output / name, name)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "output/project-mainpath")
    args = parser.parse_args()
    summary = build(args.output)
    print(json.dumps({"output": str(args.output), "conclusion": summary["conclusion"]}, indent=2))


if __name__ == "__main__":
    main()
