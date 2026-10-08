"""Output validity and literal evidence provenance, independent of case answers."""
import re


def compact(text):
    return re.sub(r"\s+", " ", str(text)).strip().lower()


def check_diagnosis(answer, observations, targets, valid_objects):
    errors = []
    if answer.get("status") == "uncertain":
        return ["uncertain: not a successful diagnosis"]
    if answer.get("status") != "diagnosed":
        errors.append("status must be diagnosed or uncertain")
    cause = answer.get("root_cause")
    if not isinstance(cause, str):
        errors.append("root_cause must be a string")
        cause = ""
    subject = answer.get("fault_object", "")
    if not isinstance(subject, str) or subject not in valid_objects:
        errors.append("unobserved/invalid object identifier")
        subject = ""
    if cause not in targets or not subject.startswith(targets.get(cause, "invalid") + "/"):
        errors.append("root-cause/object-scope contract violation")
    if subject.startswith("namespace/") and subject != "namespace/boutique":
        errors.append("unobserved namespace")
    by_id = {o["id"]: o for o in observations}
    evidence = answer.get("evidence", [])
    if not isinstance(evidence, list):
        return errors + ["evidence must be a list"]
    if len(evidence) < 2:
        errors.append("at least two supporting observations required")
    seen = set()
    for claim in evidence:
        if not isinstance(claim, dict):
            errors.append("malformed evidence claim")
            continue
        observation_id, raw_quote = claim.get("observation_id"), claim.get("quote")
        if not isinstance(observation_id, str) or not isinstance(raw_quote, str):
            errors.append("evidence ID and quote must be strings")
            continue
        obs = by_id.get(observation_id)
        quote = compact(raw_quote)
        if obs is None or len(quote) < 8 or quote not in compact(obs["text"]):
            errors.append("evidence quote does not resolve to visible observation")
        else:
            seen.add((obs["tool"], repr(sorted(obs["arguments"].items())), obs["sha256"]))
    if len(seen) < 2:
        errors.append("support must cite distinct tool observations")
    return errors
