"""Assessment evidence selector/detail UI. No automatic assessment loading."""
import streamlit as st
from underwriting.ui.diagnostics import source_label

from underwriting.ui.intake import settings_for_ui, authorised
from underwriting.persistence.intake import StoreError
from underwriting.persistence.evidence import EvidenceStore
from underwriting.assessment.evidence_service import document_hash, find_evidence, retained, retry_evidence_save, review_attribute
from underwriting.ui.navigation import activate_stage, stage_button


def evidence_controls(case, prefix, button_slot=None):
    settings = settings_for_ui()
    owner = authorised(settings)
    try:
        digest = document_hash(case)
    except OSError:
        st.warning("Company PDF unavailable; attribute evidence cannot be loaded.")
        return {"run": None, "reviews": [], "settings": settings, "store": None}
    key = f"{prefix}:{case['submission_version']}:{digest}:evidence"
    view = st.session_state.setdefault(key, {"run": None, "attempt": None, "reviews": [], "pending": []})
    store = None
    if not settings.missing(paid=False):
        try:
            store = EvidenceStore(settings)
        except StoreError as error:
            st.error(str(error))
    def action(regenerate=False):
        expected_run = view["attempt"]["run_id"] if view["attempt"] else None
        used_gpt = False
        with st.status("Attribute evidence progress", expanded=True) as status:
            def report(stage, detail):
                nonlocal used_gpt
                if stage == "GPT":
                    used_gpt = True
                status.update(label="Attribute evidence · " + stage)
                status.write(stage + ": " + detail)
            try:
                # This code runs only in an explicit button handler, never on navigation.
                view["attempt"] = store.latest_evidence(case, digest, successful=False)
                pending_runs = retained(case, digest) if owner else []
                view["pending"] = pending_runs
                if pending_runs:
                    raise ValueError("A response is retained locally. Use Retry save; do not request GPT again.")
                result = find_evidence(settings, case, store, regenerate=regenerate, confirmed=regenerate,
                    expected_run=expected_run, progress=report)
                from underwriting.ui.diagnostics import remember
                remember(view,"Evidence",result,source="generated_response" if used_gpt else "saved_database_result")
                view["attempt"] = result
                if result["status"] == "completed":
                    view["run"] = result
                    activate_stage(st.session_state, prefix, "Evidence")
                    view["origin"] = "GPT · saved to Turso" if used_gpt else "Turso database · no GPT call"
                    view["reviews"] = store.reviews(result["run_id"])
                    status.update(label="Attribute evidence loaded", state="complete", expanded=False)
                else:
                    status.update(label="Evidence validation/request failed", state="error")
                    for issue in result["validation_errors"]:
                        status.write(issue)
                st.session_state[key] = view
            except (ValueError, OSError, PermissionError, StoreError) as error:
                from underwriting.ui.diagnostics import remember
                remember(view,"Evidence",error=error)
                view["pending"] = retained(case, digest) if owner else []
                st.session_state[key] = view
                status.update(label="Attribute evidence failed", state="error")
                st.error(str(error))
    button = button_slot.button if button_slot is not None else st.button
    if stage_button(button_slot if button_slot is not None else st, "Load attribute evidence", prefix, "Evidence", disabled=store is None, key=f"{prefix}:Find evidence",
              width="stretch", help="Check Turso first; if no saved evidence exists, an owner can make one GPT request."):
        if not view["run"]:
            action()
    if st.session_state.get(prefix + ":assessment_stage", "Evidence") != "Evidence":
        return dict(view, settings=settings, store=store, case=case, digest=digest)
    from underwriting.ui.diagnostics import render
    render(view,"Evidence")
    if settings.missing(paid=False):
        st.caption("Missing configuration: " + ", ".join(settings.missing(paid=False)))
    if not view["run"]:
        st.caption("Not loaded yet")
    else:
        run = view["run"]
        st.caption("Evidence source: " + source_label(view.get("origin", "Loaded in this session; see run provenance"), settings))
        summary, reviewers = evidence_review_summary(run, view["reviews"])
        st.caption(summary)
        for name, details in reviewers.items():
            st.caption("Review recorded by " + name + " · " + details["time"] + " · " + ", ".join(sorted(details["attributes"])))
        st.caption("You may review this evidence again." if owner else "Read-only access. An authorised local owner may review this evidence again.")
    if view["attempt"] and view["attempt"]["status"] == "failed":
        with st.expander("Last evidence failure", expanded=True):
            for issue in view["attempt"]["validation_errors"]:
                st.text(issue)
    with st.expander("Regenerate evidence with GPT"):
        st.caption("A successful regeneration becomes the current evidence loaded by this button. Earlier records remain in audit history; a failed attempt does not replace the current result.")
        confirm = st.checkbox("Confirm one paid request for all five attributes; preserve prior versions", disabled=not owner,
                              key=f"{prefix}:{digest}:evidence_regeneration:{view['attempt']['run_id'] if view['attempt'] else 'initial'}")
        if st.button("Regenerate evidence with GPT", disabled=not owner or not confirm or bool(settings.missing()) or store is None,
                     key=f"{prefix}:regenerate_evidence"):
            action(True)
    for run in view["pending"]:
        st.warning("Evidence response retained locally; no automatic GPT retry will be made.")
        if st.button("Retry save · attribute evidence · no GPT call", disabled=not owner or store is None,
                     key=f"{prefix}:evidence_retry:{run['run_id']}"):
            try:
                run = retry_evidence_save(settings, store, run)
                view["pending"] = []
                view["attempt"] = run
                if run["status"] == "completed":
                    view["run"], view["reviews"] = run, store.reviews(run["run_id"])
                    activate_stage(st.session_state, prefix, "Evidence")
                    view["origin"] = "Retained GPT response · saved to Turso without another GPT call"
                st.rerun()
            except (ValueError, OSError, PermissionError, StoreError) as error:
                st.error(str(error))
    return dict(view, settings=settings, store=store, case=case, digest=digest)


def latest_review(view, identifier):
    return next((event for event in reversed(view["reviews"]) if event["attribute_id"] == identifier), None)


def evidence_review_summary(run, events):
    identifiers = {item["attribute_id"] for item in run["result"]["attributes"]}
    valid = [event for event in events if event["run_id"] == run["run_id"] and event["source_hash"] == run["source_hash"] and event["attribute_id"] in identifiers]
    latest = {event["attribute_id"]: event for event in valid}
    count = sum(bool(event["confirmed"]) for event in latest.values())
    summary = f"Evidence review {'complete' if count == len(identifiers) else 'pending'} · {count}/{len(identifiers)} attributes reviewed."
    reviewers = {}
    for event in valid:
        if event["confirmed"]:
            name = event.get("reviewer_name") or "Local owner (name not recorded)"
            details = reviewers.setdefault(name, {"time": event["created_at"], "attributes": set()})
            details["time"] = max(details["time"], event["created_at"])
            details["attributes"].add(event["attribute_id"])
    return summary, reviewers


def review_label(view, identifier):
    if not view["run"]:
        return "Not loaded yet"
    if not any(attribute["attribute_id"] == identifier for attribute in view["run"]["result"]["attributes"]):
        return "Not in saved run"
    event = latest_review(view, identifier)
    return "Reviewed" if event and event["confirmed"] else "Pending review"


def attribute_details(view, identifier):
    if not view["run"]:
        st.caption("Not loaded yet · Load / Find attribute evidence to check saved results first.")
        return
    run = view["run"]
    historical = next((a for a in run["attribute_configuration"] if a["id"] == identifier), None)
    if historical is None:
        st.caption("This attribute is not present in the historical evidence run. Explicit regeneration is required; no GPT request was made.")
        return
    st.caption("Evidence-run definition: " + historical["definition"])
    attribute = next((item for item in run["result"]["attributes"] if item["attribute_id"] == identifier), None)
    if attribute is None:
        st.caption("No evidence for this attribute exists in the saved run.")
        return
    review = latest_review(view, identifier)
    st.caption("AI output · " + review_label(view, identifier))
    aspects = next(a.get("aspects", []) for a in run["attribute_configuration"] if a["id"] == identifier)
    for aspect in (aspects if aspects else [None]):
        if aspect:
            st.markdown("**" + aspect + "**")
        items = [item for item in attribute["evidence_items"] if aspect is None or item["aspect"] == aspect]
        if items:
            rows = [{"Evidence ID": item["evidence_id"], "AI interpreted fact": item["extracted_fact"],
                     "Implication": item["implication"], "Human interpretation": review["corrected_facts"].get(item["evidence_id"], "—") if review else "—"} for item in items]
            st.dataframe(rows, hide_index=True, alt="Attribute facts, evidence implications and separate human interpretations")
            with st.expander("Source quotations and AI relevance" + (" · " + aspect if aspect else "")):
                for item in items:
                    st.caption(f"{item['evidence_id']} · Page {item['page']} · {item['section'] or 'section unavailable'} · {item['source_attribution'] or 'attribution unavailable'}")
                    st.text(item["exact_quote"])
                    st.markdown("**AI relevance:**")
                    st.text(item["relevance_explanation"])
        else:
            st.caption("No evidence items returned" + (" for this aspect." if aspect else "."))
    with st.expander("Information gaps and contradictions"):
        st.markdown("**Information gaps**")
        if not attribute["information_gaps"]:
            st.caption("No gaps identified by this AI response.")
        for gap in attribute["information_gaps"]:
            st.text(f"{gap['gap_id']} · {gap['description']}")
            st.caption(gap["basis"] + " · Related evidence: " + ", ".join(gap["related_evidence_ids"]))
        st.markdown("**Contradictions**")
        if not attribute["contradictions"]:
            st.caption("No contradictions identified by this AI response.")
        for conflict in attribute["contradictions"]:
            st.text(conflict["description"])
            st.caption("Related evidence: " + ", ".join(conflict["related_evidence_ids"]))
    st.markdown("**AI summary**")
    st.text(attribute["ai_summary"])
    owner = authorised(view["settings"])
    with st.expander("Human evidence review"):
        st.caption("Edit interpretations only. Original AI response and source quotations are immutable.")
        with st.form(f"{run['run_id']}:{identifier}:review"):
            reviewer_name = st.text_input("Reviewer name", value=st.session_state.get("assessment_reviewer_name", ""), placeholder="Enter your name",
                key=f"{run['run_id']}:{identifier}:reviewer_name", disabled=not owner, max_chars=100,
                help="Entered by the reviewer for the review record. This is not an authenticated user identity.", persist_state="session")
            facts = {}
            for item in attribute["evidence_items"]:
                initial = review["corrected_facts"].get(item["evidence_id"], item["extracted_fact"]) if review else item["extracted_fact"]
                facts[item["evidence_id"]] = st.text_area("Interpreted fact · " + item["evidence_id"], value=initial, disabled=not owner,
                    key=f"{run['run_id']}:{identifier}:fact:{item['evidence_id']}", persist_state="session")
            notes = st.text_area("Review notes", value=review["notes"] if review else "", disabled=not owner,
                                 key=f"{run['run_id']}:{identifier}:evidence_notes", persist_state="session")
            confirmed = st.checkbox("Confirm this attribute's evidence as reviewed", value=review["confirmed"] if review else False, disabled=not owner,
                                    key=f"{run['run_id']}:{identifier}:evidence_confirmed", persist_state="session")
            submitted = st.form_submit_button("Save evidence review", disabled=not owner)
        if submitted:
            try:
                event = review_attribute(view["settings"], view["store"], run, identifier, facts, notes, confirmed, reviewer_name=reviewer_name)
                st.session_state["assessment_reviewer_name"] = reviewer_name.strip()
                # Cached view and this return value share the review list; preserve every event.
                view["reviews"].append(event)
                st.rerun()
            except (ValueError, PermissionError, StoreError) as error:
                st.error(str(error))
        with st.expander("Human review history"):
            for event in view["reviews"]:
                if event["attribute_id"] == identifier:
                    st.caption((event.get("reviewer_name") or "Local owner (name not recorded)") + " · " + event["created_at"] + " · " + ("Reviewed" if event["confirmed"] else "Pending review"))
                    st.json({"interpreted_facts": event["corrected_facts"], "notes": event["notes"]})
