"""Explicit proposal controls and separate human belief confirmation."""
from decimal import Decimal

import streamlit as st
from underwriting.ui.diagnostics import source_label

from underwriting.configuration.settings import get_config, configuration_metadata
from underwriting.ui.intake import settings_for_ui, authorised
from underwriting.persistence.intake import StoreError
from underwriting.assessment.evidence_service import document_hash
from underwriting.persistence.beliefs import BeliefStore
from underwriting.assessment.belief_service import propose_beliefs, reviewed_set, current_input, pending_beliefs, retry_belief_save, confirm_belief
from underwriting.ui.readiness import evidence_blocker
from underwriting.ui.navigation import activate_stage, stage_button


def active_run(view):
    return view.get("run") if not view.get("stale") else None


def belief_controls(case, prefix, evidence_view, button_slot=None):
    settings = settings_for_ui()
    owner = authorised(settings)
    try:
        digest = document_hash(case)
    except OSError:
        return {"run": None, "reviews": [], "stale": False}
    key = f"{prefix}:{case['submission_version']}:{digest}:beliefs"
    view = st.session_state.setdefault(key, {"run": None, "attempt": None, "reviews": [], "pending": [], "stale": False})
    prerequisite = evidence_blocker(evidence_view)
    if view["run"] and evidence_view.get("run"):
        try:
            _, review_hash, _ = reviewed_set(evidence_view["run"], evidence_view["reviews"])
            from underwriting.assessment.belief_service import reviewed_hash_matches
            view["stale"] = not reviewed_hash_matches(evidence_view["run"],evidence_view["reviews"],view["run"]["reviewed_hash"])
        except ValueError:
            view["stale"] = True
    store = None
    if not settings.missing(paid=False):
        try:
            store = BeliefStore(settings)
        except StoreError as error:
            st.error(str(error))
    def action(regenerate=False):
        if prerequisite:
            (button_slot if button_slot is not None else st).warning(prerequisite)
            return
        if store is None:
            st.error("Cannot load beliefs. Missing configuration: " + ", ".join(settings.missing(paid=False)) if settings.missing(paid=False) else "Cannot load beliefs: database connection is unavailable.")
            return
        expected = view["attempt"]["run_id"] if view["attempt"] else None
        used_gpt = False
        with st.status("Belief proposal progress", expanded=True) as status:
            def report(stage, detail):
                nonlocal used_gpt
                if stage == "GPT":
                    used_gpt = True
                status.update(label="Beliefs · " + stage)
                status.write(stage + ": " + detail)
            try:
                digest_now, evidence, reviewed, review_hash, _ = current_input(case, store)
                # This explicit action also loads the underlying reviewed evidence, never GPT evidence extraction.
                events = store.reviews(evidence["run_id"])
                evidence_view.update(run=evidence, reviews=events, origin="Turso database · no GPT call")
                evidence_key = f"{prefix}:{case['submission_version']}:{digest}:evidence"
                st.session_state[evidence_key] = {"run": evidence, "reviews": events, "attempt": evidence, "pending": [], "origin": "Turso database · no GPT call"}
                view["attempt"] = store.latest_beliefs(case, digest_now, review_hash, successful=False)
                view["pending"] = pending_beliefs(case, digest) if owner else []
                if view["pending"]:
                    raise ValueError("A belief response is retained. Use Retry save without GPT.")
                result = propose_beliefs(settings, case, store, regenerate=regenerate, confirmed=regenerate,
                                        expected_run=expected, progress=report)
                from underwriting.ui.diagnostics import remember
                remember(view,"Beliefs",result,source="generated_response" if used_gpt else "saved_database_result")
                view["attempt"] = result
                if result["status"] == "completed":
                    view.update(run=result, reviews=store.belief_reviews(result["run_id"]), stale=False)
                    activate_stage(st.session_state, prefix, "Beliefs")
                    view["origin"] = "GPT · saved to Turso" if used_gpt else "Turso database · no GPT call"
                    status.update(label="Belief proposals loaded · human confirmation pending", state="complete", expanded=False)
                else:
                    status.update(label="Belief proposal failed or stale", state="error")
                    for issue in result["validation_errors"]:
                        status.write(issue)
                st.session_state[key] = view
                if result["status"] == "completed":
                    st.rerun()
            except (ValueError, PermissionError, OSError, StoreError) as error:
                view["pending"] = pending_beliefs(case, digest) if owner else []
                st.session_state[key] = view
                status.update(label="Belief proposal stopped", state="error")
                st.error(str(error))
    button = button_slot.button if button_slot is not None else st.button
    if stage_button(button_slot if button_slot is not None else st, "Load proposed beliefs", prefix, "Beliefs", permitted=not prerequisite and store is not None, key=f"{prefix}:Propose beliefs", width="stretch",
              help=prerequisite or "Check Turso first for this reviewed evidence; only missing proposals require a GPT request."):
        if prerequisite or not active_run(view):
            action()
    if st.session_state.get(prefix + ":assessment_stage", "Evidence") != "Beliefs":
        return dict(view, settings=settings, store=store, case=case, prerequisite=prerequisite)
    from underwriting.ui.diagnostics import render
    render(view,"Beliefs")
    st.caption("Subjective concern beliefs, not event probabilities or model confidence. Unknown is unassigned belief, not a severity grade.")
    if view["stale"]:
        st.warning("Reviewed evidence changed. Loaded proposals and their confirmations are stale and are not shown as current. Deliberately Load / Propose beliefs again.")
    elif not view["run"]:
        st.caption("Beliefs: Not loaded yet · all five evidence attributes must be reviewed.")
    if view["run"]:
        run = view["run"]
        st.caption("Belief source: " + source_label(view.get("origin", "Loaded in this session; see run provenance"), settings))
        with st.expander("Belief versions and history linkage"):
            st.caption(f"Saved {run['finished_at']} · Run {run['run_id']}")
            if run["configuration"] != configuration_metadata():
                st.caption("Saved with earlier settings. Original beliefs are preserved; no new proposal was requested.")
            st.text("Reviewed evidence: " + run["reviewed_hash"] + " · Evidence run: " + run["evidence_run_id"])
            st.text("Prompt: " + run["prompt"]["version"] + " · " + run["prompt"]["sha256"])
            st.text("Attribute configuration: " + run["attribute_configuration_hash"])
            st.text("Assessment settings: " + run["assessment_settings_hash"])
            st.text("Schema: " + run["schema_version"] + " · Model: " + run["model"])
            st.text("Configuration: " + run["configuration"]["version"] + " · " + run["configuration"]["sha256"])
            if run.get("decision_model"):
                st.text("Decision model: " + run["decision_model"]["version"] + " · " + run["decision_model"]["sha256"])
    if view["attempt"] and view["attempt"]["status"] != "completed":
        with st.expander("Latest belief attempt errors", expanded=True):
            for issue in view["attempt"]["validation_errors"]:
                st.text(issue)
    with st.expander("Regenerate beliefs with GPT"):
        st.caption("A successful regeneration becomes the current proposal set for this reviewed evidence. Earlier records remain in audit history; a failed attempt does not replace the current result.")
        confirmed = st.checkbox("Confirm one paid belief request; preserve earlier proposals", disabled=not owner,
            key=f"{prefix}:{digest}:confirm_beliefs:{view['attempt']['run_id'] if view['attempt'] else 'initial'}")
        if st.button("Regenerate beliefs with GPT", disabled=not owner or not confirmed or store is None or bool(settings.missing()) or bool(prerequisite),
                     key=f"{prefix}:regenerate_beliefs"):
            action(True)
    for run in view["pending"]:
        st.warning("Belief response retained locally; retry saving without a GPT request.")
        if st.button("Retry save · beliefs · no GPT call", disabled=not owner or store is None,
                     key=f"{prefix}:belief_retry:{run['run_id']}"):
            try:
                run = retry_belief_save(settings, store, run)
                view["pending"] = []
                view["attempt"] = run
                if run["status"] == "completed":
                    current = current_input(case, store)
                    view.update(run=run, reviews=store.belief_reviews(run["run_id"]), stale=current[3] != run["reviewed_hash"])
                    view["origin"] = "Retained GPT response · saved to Turso without another GPT call"
                    activate_stage(st.session_state, prefix, "Beliefs")
                st.rerun()
            except (ValueError, OSError, PermissionError, StoreError) as error:
                from underwriting.ui.diagnostics import remember
                remember(view,"Beliefs",error=error)
                st.error(str(error))
    return dict(view, settings=settings, store=store, case=case, prerequisite=prerequisite)


def proposal_for(view, identifier):
    run = active_run(view)
    return next((a for a in run["result"]["assessments"] if a["attribute_id"] == identifier), None) if run else None


def confirmed_for(view, identifier):
    if not active_run(view):
        return None
    return next((event for event in reversed(view["reviews"]) if event["attribute_id"] == identifier), None)


def belief_status(view, identifier):
    if view.get("stale"):
        return "Stale"
    if not active_run(view):
        return "Not loaded yet"
    return "Confirmed" if confirmed_for(view, identifier) else "Pending confirmation"


def display_distribution(label, values):
    st.markdown("**" + label + "**")
    for column, name in zip(st.columns(3), ("low", "high", "unknown")):
        column.markdown(name.title())
        column.text(f"{values[name]:.2f}" if values else "—")


def belief_details(view, identifier):
    proposal = proposal_for(view, identifier)
    event = confirmed_for(view, identifier)
    display_distribution("AI-proposed beliefs", proposal)
    display_distribution("Human-confirmed beliefs", event["values"] if event else None)
    st.caption(belief_status(view, identifier))
    if not proposal:
        return
    st.text(proposal["assessment_summary"])
    with st.expander("Belief justification and concerns"):
        for key, label in (("low_support", "Low support"), ("high_support", "High support"), ("unknown_basis", "Unknown basis")):
            st.markdown("**" + label + "**")
            st.text(proposal[key]["explanation"])
            st.caption("Evidence IDs: " + ", ".join(proposal[key]["evidence_ids"]) + " · Gap IDs: " + ", ".join(proposal[key].get("gap_ids", [])))
        st.markdown("**Important concerns**")
        for value in proposal["important_concerns"]:
            st.text(value)
        st.markdown("**Clarification questions**")
        for value in proposal["clarification_questions"]:
            st.text(value)
        if proposal["protection_aspect_findings"]:
            st.dataframe(proposal["protection_aspect_findings"], hide_index=True, alt="F3 compartmentation, detection and suppression findings without subattribute scores")
    owner = authorised(view["settings"])
    run = view["run"]
    key = f"{run['run_id']}:{identifier}:human_belief"
    mode = st.radio("Human assessment", ["Accept AI proposal", "Edit Low and High"], disabled=not owner, key=key + ":mode", persist_state="session")
    low, high = proposal["low"], proposal["high"]
    reason = ""
    if mode == "Edit Low and High":
        step = get_config()["belief_assessment"]["increment"]
        low_col, high_col = st.columns(2)
        low = low_col.number_input("Human Low", min_value=0.0, max_value=1.0, value=float(low), step=float(step), format="%.10f", disabled=not owner, key=key + ":low", persist_state="session")
        high = high_col.number_input("Human High", min_value=0.0, max_value=1.0, value=float(high), step=float(step), format="%.10f", disabled=not owner, key=key + ":high", persist_state="session")
        st.text("Calculated Unknown: " + str(Decimal(1) - Decimal(str(low)) - Decimal(str(high))))
        reason = st.text_area("Reason for belief edits", disabled=not owner, key=key + ":reason", persist_state="session")
    confirmed = st.checkbox("Confirm this attribute assessment", disabled=not owner, key=key + ":confirm", persist_state="session")
    if st.button("Confirm beliefs", disabled=not owner or not confirmed, key=key + ":save"):
        try:
            saved = confirm_belief(view["settings"], view["case"], view["store"], run, identifier,
                                  "accept" if mode == "Accept AI proposal" else "edit", low, high, reason)
            view["reviews"].append(saved)
            st.rerun()
        except (ValueError, OSError, PermissionError, StoreError) as error:
            if isinstance(error, ValueError) and "stale" in str(error).lower():
                case = view["case"]
                cache_key = f"case:{case['case_id']}:{case['submission_version']}:{run['source_hash']}:beliefs"
                if cache_key in st.session_state:
                    st.session_state[cache_key]["stale"] = True
                    st.rerun()
            st.error(str(error))
    with st.expander("Human belief review history"):
        for event in view["reviews"]:
            if event["attribute_id"] == identifier:
                st.caption(event["created_at"] + " · " + event["mode"])
                st.json({"confirmed_values": event["values"], "reason": event["reason"]})
