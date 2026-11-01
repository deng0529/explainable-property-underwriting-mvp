"""Session-local workflow prerequisites; no database reads or paid requests."""
from underwriting.assessment.belief_service import reviewed_set
from underwriting.assessment.er_service import required_members


def evidence_blocker(view):
    run = view.get("run")
    if not run or run.get("status") != "completed":
        return "First click Load attribute evidence, then review and confirm each attribute's evidence."
    if view.get("stale"):
        return "Reload current attribute evidence, then review and confirm it before loading beliefs."
    try:
        reviewed_set(run, view.get("reviews", []))
    except ValueError as error:
        return "First finish the human evidence reviews. " + str(error)
    return ""


def belief_blocker(view, model):
    if view.get("prerequisite"):
        return view["prerequisite"]
    run = view.get("run")
    if not run or run.get("status") != "completed":
        return "First load and review attribute evidence, then click Load proposed beliefs and confirm each attribute's beliefs."
    if view.get("stale"):
        return "Evidence has changed. Load proposed beliefs again and confirm the current attribute beliefs before calculating ER."
    latest = {event["attribute_id"]: event for event in view.get("reviews", [])}
    missing = []
    for identifier in sorted(required_members(model)):
        event = latest.get(identifier)
        if (not event or not event.get("confirmed") or event.get("run_id") != run["run_id"]
                or event.get("source_hash") != run["source_hash"] or event.get("reviewed_hash") != run["reviewed_hash"]):
            missing.append(identifier)
    if missing:
        return "Before calculating ER, confirm the beliefs for: " + ", ".join(missing) + "."
    return ""
