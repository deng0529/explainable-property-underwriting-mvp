"""Explicit ER action and complete group distributions. BRB remains disabled."""
import streamlit as st
from underwriting.ui.diagnostics import remember

from underwriting.ui.intake import settings_for_ui, authorised
from underwriting.configuration.access import local_comparison_settings
from underwriting.configuration.settings import get_config
from underwriting.ui.score_comparison import result_for
from underwriting.persistence.intake import StoreError
from underwriting.configuration.decision_model import get_model, effective_er_model_hash
from underwriting.assessment.evidence_service import document_hash
from underwriting.models.er import ERInputError, ERUndefinedError
from underwriting.assessment.er_service import calculate_er, input_belief_hash, required_members, compatible_input_hashes
from underwriting.persistence.er import ERStore
from underwriting.ui.er_presentation import group_presentation, describe_er_result
from underwriting.ui.readiness import belief_blocker
from underwriting.ui.navigation import activate_stage, stage_button


def er_controls(case, prefix, belief_view, button_slot):
    settings = local_comparison_settings(settings_for_ui())
    direct_mode=get_config()["belief_comparison"]["enabled"] and get_config()["belief_comparison"]["calculation_input_method"]=="direct_ai"
    model = get_model()
    try:
        digest = document_hash(case)
    except OSError:
        digest = "source-unavailable"
    key = f"{prefix}:{case['submission_version']}:{digest}:er"
    view = st.session_state.setdefault(key, {"run": None, "stale": False})
    prerequisite = belief_blocker(belief_view, model)
    if direct_mode:
        direct=belief_view.get('comparison_direct')
        prerequisite=belief_view.get('prerequisite') or ''
        if not all(result_for(belief_view,'direct',identifier) for identifier in required_members(model)):
            prerequisite='First click Load attribute evidence and review it, then Load proposed beliefs to load current Direct AI beliefs.'

    if view["run"]:
        run = view["run"]
        view["stale"] = effective_er_model_hash(run["decision_model_snapshot"]) != effective_er_model_hash(model) or belief_view.get("stale", False)
        proposal = belief_view.get("run")
        if direct_mode:
            from underwriting.assessment.er_service import calculation_input_hash
            values={identifier:{k:result_for(belief_view,"direct",identifier)[k] for k in ("low","high","unknown")} for identifier in required_members(model)} if not prerequisite else {}
            if prerequisite or run.get("input_method")!="direct_ai":view["stale"]=True
            else:
                inputs=(digest,direct["reviewed_hash"],direct["run_id"],values,[],direct["evidence_run_id"])
                view["stale"]=effective_er_model_hash(run["decision_model_snapshot"])!=effective_er_model_hash(model) or calculation_input_hash(inputs,{"method":"direct_ai","comparison_cache_key":direct["cache_key"]}) not in compatible_input_hashes(run)
        elif proposal:
            events = {event["attribute_id"]: event for event in belief_view["reviews"]}
            needed = required_members(model)
            if not needed <= set(events):
                view["stale"] = True
            else:
                values = {identifier: events[identifier]["values"] for identifier in sorted(needed)}
                current_hash = input_belief_hash(digest, proposal["reviewed_hash"], proposal["run_id"], values)
                view["stale"] |= current_hash not in compatible_input_hashes(run)
    owner = authorised(settings)
    if stage_button(button_slot, "Calculate Fire/Security ER" if owner else "Load saved Fire/Security results", prefix, "ER results", permitted=not prerequisite and not settings.missing(paid=False) and digest != "source-unavailable", disabled=not model["er"]["enabled"],
                          key=f"{prefix}:Calculate assessment", width="stretch", help=prerequisite or "Load matching saved results or calculate from current Direct AI beliefs; no GPT request."):
        if prerequisite:
            button_slot.warning(prerequisite)
        elif settings.missing(paid=False):
            button_slot.error("Cannot calculate ER. Missing configuration: " + ", ".join(settings.missing(paid=False)))
        elif digest == "source-unavailable":
            button_slot.error("Company PDF unavailable. Restore the source document before calculating ER.")
        elif not view["run"] or view["stale"]:
            try:
                with st.status("ER · checking Direct AI inputs, loading or calculating and saving" if owner else "ER · loading saved results", expanded=True) as status:
                    result = calculate_er(settings, case, ERStore(settings), model, input_method="direct_ai" if direct_mode else "human_confirmed")
                    view.update(run=result, stale=False)
                    remember(view,"ER",result)
                    activate_stage(st.session_state, prefix, "ER results")
                    status.update(label="Saved ER results loaded" if result.get("display_diagnostics") else "ER calculated and saved", state="complete", expanded=False)
            except (ValueError, ArithmeticError, OSError, PermissionError, StoreError) as error:
                remember(view,"ER",error=error)
                st.error(str(error))
    return view


def er_results(view):
    from underwriting.ui.diagnostics import render
    render(view,"ER")
    model = get_model()
    groups = model["er"]["groups"]
    if view["stale"]:
        st.warning("ER results are stale: belief inputs or model changed. Calculate again; old values are not used as current.")
    columns = st.columns(len(groups))
    run = view["run"] if not view["stale"] else None
    for column, (identifier, definition) in zip(columns, groups.items()):
        with column, st.container(border=True):
            st.markdown("**" + definition["label"] + " ER**")
            result = run["groups"].get(identifier) if run else None
            metric_columns = st.columns(len(model["grades"]["order"]) + 1)
            for metric, grade in zip(metric_columns, model["grades"]["order"]):
                metric.metric(model["grades"]["definitions"][grade]["label"],
                              f"{result['assigned_beliefs'][grade]:.2f}" if result else "—")
            metric_columns[-1].metric("Unknown", f"{result['unassigned_belief']:.2f}" if result else "—")
            if result:
                description = describe_er_result(result, model)
                st.markdown(f"**Most supported: {description['label']}** · {description['winning_belief_degree']:.2f}")
                if len(description["largest_components"]) > 1:
                    st.caption("Tied largest components within tolerance: " + ", ".join(description["largest_components"]) + ". Degree shown is the largest component.")
                rows, explanations = group_presentation(run, identifier)
                rows = [{("Effective weight" if key == "Weight" else "Low" if key == "Low concern" else "High" if key == "High concern" else key): value for key, value in row.items()} for row in rows]
                st.caption("Direct AI attribute inputs · not human-confirmed" if run.get("input_method")=="direct_ai" else "Human-confirmed attribute inputs")
                st.table(rows)
            if result:
                st.markdown("**What drives this result**")
                for explanation in explanations:
                    st.caption(explanation)
                st.caption("Input support compares belief × weight. ER combines inputs nonlinearly; these are not additive contributions to the result.")
                with st.expander("Calculation details"):
                    st.json(result)
            else:
                st.caption("Not calculated" if not view["stale"] else "Stale")
    if run:
        st.caption("Descriptive concern labels are not insurance acceptance decisions. BRB inputs retain the complete unrounded distributions, including Unknown.")
        st.caption("Saved " + run["finished_at"] + " · Values rounded to two decimal places for display; calculations and saved data retain full precision.")
