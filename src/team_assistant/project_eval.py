"""Offline aggregation for the project's frozen recovery evidence."""

from __future__ import annotations

from typing import Any


def _completed_cost(formal: dict[str, Any], arm: str) -> dict[str, float | int]:
    rows = [
        row
        for row in formal["recovery_costs"]
        if row["arm"] == arm and row["state"] == "completed"
    ]
    return {
        "units": len(rows),
        "calls": sum(row["calls"] for row in rows),
        "tokens": sum(row["tokens"] for row in rows),
        "seconds": sum(row["seconds"] for row in rows),
    }


def _reduction(baseline: float, candidate: float) -> float:
    if baseline <= 0:
        raise ValueError("baseline must be positive")
    return 100.0 * (baseline - candidate) / baseline


def summarize_formal(formal: dict[str, Any]) -> dict[str, Any]:
    """Summarize the frozen formal-v2 evidence without rescoring or inference."""
    if formal["units"] != formal["injected_units"] + formal["not_injected_units"]:
        raise ValueError("formal unit counts are inconsistent")

    restart = _completed_cost(formal, "restart")
    selective = _completed_cost(formal, "selective")
    expiry_restart = _completed_cost(formal, "expiry-restart")
    expiry_selective = _completed_cost(formal, "expiry-selective")
    eligible = formal["injected_units"]

    if restart["units"] != eligible or selective["units"] != eligible:
        raise ValueError("partial-invalidation arms do not cover every eligible unit")
    if expiry_restart["units"] != formal["units"] or expiry_selective["units"] != formal["units"]:
        raise ValueError("all-expiry arms do not cover every assigned unit")

    scores = formal["arm_scores"]
    return {
        "design": {
            "independent_cases": 3,
            "repeats_per_case": 2,
            "assigned_units": formal["units"],
            "partial_invalidation_eligible_units": eligible,
            "not_injected_units_retained": formal["not_injected_units"],
        },
        "partial_invalidation": {
            "restart_strict_pass": scores["restart"]["strict_pass"],
            "selective_strict_pass": scores["selective"]["strict_pass"],
            "strict_pass_denominator": eligible,
            "restart": restart,
            "selective": selective,
            "call_reduction_percent": _reduction(restart["calls"], selective["calls"]),
            "token_reduction_percent": _reduction(restart["tokens"], selective["tokens"]),
            "observed_request_time_reduction_percent": _reduction(
                restart["seconds"], selective["seconds"]
            ),
            "paired_quality_equivalence_established": False,
        },
        "all_expiry": {
            "restart_strict_pass": scores["expiry-restart"]["strict_pass"],
            "selective_strict_pass": scores["expiry-selective"]["strict_pass"],
            "strict_pass_denominator": formal["units"],
            "restart": expiry_restart,
            "selective": expiry_selective,
            "same_call_count": expiry_restart["calls"] == expiry_selective["calls"],
        },
        "accounting": {
            "saved_model_calls": formal["calls"],
            "saved_tokens": formal["tokens"],
            "provider_errors": formal["api_errors"],
            "formal_billed_usd": formal["header_cost_sum"],
        },
        "limitations": list(formal["limitations"]),
    }


def summarize_seeded(
    seeded: dict[str, Any],
    usage: dict[str, Any],
    selective_reuse: dict[str, Any],
    review: dict[str, Any],
) -> dict[str, Any]:
    """Summarize the conditional seeded handoff as mechanism evidence only."""
    calls = {call["call"]: call for call in usage["calls"]}
    restart_ids = seeded["arms"]["restart"]["calls"]
    selective_ids = seeded["arms"]["selective"]["calls"]

    def tokens(ids: list[int]) -> int:
        return sum(calls[call_id]["tokens"] for call_id in ids)

    restart_tokens = tokens(restart_ids)
    selective_tokens = tokens(selective_ids)
    aggregate = review["aggregate"]
    if aggregate["parsed_packet_outputs"] != 2:
        raise ValueError("seeded review does not cover both completed outputs")
    return {
        "track": seeded["track"],
        "formal_evaluation": seeded["formal_evaluation"],
        "no_recovery_state": seeded["arms"]["no-recovery"]["state"],
        "reviewed_main_diagnoses_supported": aggregate["main_diagnosis_supported"],
        "reviewed_actions_supported": aggregate["action_supported"],
        "reviewed_outputs": aggregate["parsed_packet_outputs"],
        "review_scope": review["gate"]["gate_scope"],
        "restart_calls": len(restart_ids),
        "selective_calls": len(selective_ids),
        "restart_tokens": restart_tokens,
        "selective_tokens": selective_tokens,
        "token_reduction_percent": _reduction(restart_tokens, selective_tokens),
        "selective_retained_artifacts": len(selective_reuse["valid_unchanged_artifacts"]),
        "interpretation": "Conditional mechanism check; not evidence of organic fault prevalence.",
    }


def build_summary(
    formal: dict[str, Any],
    seeded: dict[str, Any],
    usage: dict[str, Any],
    selective_reuse: dict[str, Any],
    seeded_review: dict[str, Any],
) -> dict[str, Any]:
    formal_summary = summarize_formal(formal)
    seeded_summary = summarize_seeded(seeded, usage, selective_reuse, seeded_review)
    return {
        "formal": formal_summary,
        "seeded_conditional": seeded_summary,
        "conclusion": (
            "Selective recovery is conditionally useful. Under partial invalidation, it matched "
            "restart's observed eligible strict-pass count while reducing incremental tokens; "
            "when all prior work expired, it used the same number of calls as restart. The "
            "evidence supports cost proportionality and conservative fallback, but does not "
            "establish higher diagnosis accuracy, paired quality equivalence, natural fault "
            "prevalence, or repair of an organically propagated wrong final diagnosis."
        ),
    }
