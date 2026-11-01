"""Explicit ER action and complete group distributions. BRB remains disabled."""
import streamlit as st

from underwriting.ui.intake import settings_for_ui, authorised
from underwriting.persistence.intake import StoreError
from underwriting.configuration.decision_model import get_model, effective_er_model_hash
from underwriting.assessment.evidence_service import document_hash
from underwriting.models.er import ERInputError, ERUndefinedError
from underwriting.assessment.er_service import calculate_er, input_belief_hash, required_members
from underwriting.persistence.er import ERStore
from underwriting.ui.er_presentation import group_presentation, describe_er_result
from underwriting.ui.readiness import belief_blocker
from underwriting.ui.navigation import activate_stage


def er_controls(case, prefix, belief_view, button_slot):
    settings = settings_for_ui()
    model = get_model()
    try:
        digest = document_hash(case)
    except OSError:
        digest = "source-unavailable"
    key = f"{prefix}:{case['submission_version']}:{digest}:er"
    view = st.session_state.setdefault(key, {"run": None, "stale": False})
    prerequisite = belief_blocker(belief_view, model)
    if view["run"]:
        run = view["run"]
        view["stale"] = effective_er_model_hash(run["decision_model_snapshot"]) != effective_er_model_hash(model) or belief_view.get("stale", False)
        proposal = belief_view.get("run")
        if proposal:
            events = {event["attribute_id"]: event for event in belief_view["reviews"]}
            needed = required_members(model)
            if not needed <= set(events):
                view["stale"] = True
            else:
                values = {identifier: events[identifier]["values"] for identifier in sorted(needed)}
                current_hash = input_belief_hash(digest, proposal["reviewed_hash"], proposal["run_id"], values)
                view["stale"] |= current_hash != run["input_belief_hash"]
    if button_slot.button("Calculate Fire / Security ER", disabled=not model["er"]["enabled"],
                          key=f"{prefix}:Calculate assessment", width="stretch", help=prerequisite or "Calculate configured ER groups from current confirmed beliefs; no GPT request."):
        if prerequisite:
            button_slot.warning("Please first load attribute evidence and complete its reviews, then load proposed beliefs and confirm them, then calculate the ER results. " + prerequisite)
        elif settings.missing(paid=False):
            button_slot.error("Cannot calculate ER. Missing configuration: " + ", ".join(settings.missing(paid=False)))
        elif digest == "source-unavailable":
            button_slot.error("Company PDF unavailable. Restore the source document before calculating ER.")
        else:
            try:
                with st.status("ER · checking confirmed inputs, calculating and saving", expanded=True) as status:
                    result = calculate_er(settings, case, ERStore(settings), model)
                    view.update(run=result, stale=False)
                    activate_stage(st.session_state, prefix, "ER results")
                    status.update(label="ER calculated and saved" if authorised(settings) else "Saved ER results loaded", state="complete", expanded=False)
            except (ValueError, ArithmeticError, OSError, PermissionError, StoreError) as error:
                st.error(str(error))
    if prerequisite:
        button_slot.caption(prerequisite)
    return view


def er_results(view):
    model = get_model()
    groups = model["er"]["groups"]
    if view["stale"]:
        st.warning("ER results are stale: confirmed inputs or model changed. Calculate again; old values are not used as current.")
    columns = st.columns(len(groups))
    run = view["run"] if not view["stale"] else None
    for column, (identifier, definition) in zip(columns, groups.items()):
        with column, st.container(border=True):
            st.markdown("**" + definition["label"] + " ER**")
            result = run["groups"].get(identifier) if run else None
            if result:
                rows, explanations = group_presentation(run, identifier)
                st.caption("Human-confirmed attribute inputs")
                st.table(rows)
                st.markdown("**Aggregated result**")
                description = describe_er_result(result, model)
                st.markdown(f"**{description['label']}** · {description['winning_belief_degree']:.2f}")
                if len(description["largest_components"]) > 1:
                    st.caption("Tied largest components within tolerance: " + ", ".join(description["largest_components"]) + ". Degree shown is the largest component.")
            for grade in model["grades"]["order"]:
                st.text(model["grades"]["definitions"][grade]["label"] + ": " + (f"{result['assigned_beliefs'][grade]:.2f}" if result else "—"))
            st.text("Unknown (unassigned): " + (f"{result['unassigned_belief']:.2f}" if result else "—"))
            if result:
                st.markdown("**What drives this result**")
                for explanation in explanations:
                    st.caption(explanation)
                st.caption("Input support compares belief × weight. ER combines inputs nonlinearly; these are not additive contributions to the result.")
            else:
                st.caption("Not calculated" if not view["stale"] else "Stale")
    if run:
        st.caption("Descriptive concern labels are not insurance acceptance decisions. BRB inputs retain the complete unrounded distributions, including Unknown.")
        st.caption("Saved " + run["finished_at"] + " · Values rounded to two decimal places for display; calculations and saved data retain full precision.")
