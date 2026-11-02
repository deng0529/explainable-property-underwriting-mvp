"""Session-local workflow prerequisites; no database reads or paid requests."""
from underwriting.reliability.prerequisites import reviewed_evidence, confirmed_values
from underwriting.assessment.er_service import required_members


def evidence_blocker(view):
    run = view.get("run")
    if not run or run.get("status") != "completed":
        return "First click Load attribute evidence, then review and confirm each attribute's evidence."
    if view.get("stale"):
        return "Reload current attribute evidence, then review and confirm it before loading beliefs."
    try:
        reviewed_evidence(run, view.get("reviews", []))
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
    try:
        confirmed_values(run,view.get('reviews',[]),required_members(model),run['source_hash'],run['reviewed_hash'])
    except ValueError as error:
        return str(error)
    return ""
