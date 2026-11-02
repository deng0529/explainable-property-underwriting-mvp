"""Submission intake controls and rendering; navigation never calls GPT."""
import json
import time

import streamlit as st

from underwriting.configuration.access import load_settings, require_owner
from underwriting.intake.schema import UI_TO_PROMPT, validate_output
from underwriting.intake.service import source_context, pending, run_intake, retry_save, revalidate_saved
from underwriting.persistence.intake import TursoStore, StoreError
from underwriting.configuration.settings import configuration_metadata
from underwriting.intake.recovery import recover_screening, save_recovery
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JSONSchemaError

STATUS_LABELS = {"found": "Found", "not_provided": "Not provided", "conflicting": "Conflicting",
                 "not_applicable": "Not applicable"}


def settings_for_ui():
    try:
        secrets = st.secrets.to_dict()
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        secrets = {}
    return load_settings(secrets)


def authorised(settings):
    try:
        require_owner(settings, st.session_state.get("intake_owner_proof"))
        return True
    except PermissionError:
        return False


def view_key(case, digest):
    return f"case:{case['case_id']}:{case['submission_version']}:{digest}:intake_view"


def remember_view(case, view):
    if view["source_hash"] and not view["error"]:
        st.session_state[view_key(case, view["source_hash"])] = {
            key: value for key, value in view.items() if key not in {"settings", "store", "pending"}}


def load_view(case, query=False):
    settings = settings_for_ui()
    view = {"settings": settings, "saved": None, "stale": False, "store": None,
            "latest_attempt": None, "error": None, "pending": [], "source_hash": None}
    try:
        digest, pages = source_context(case)
        view["source_hash"] = digest
        if authorised(settings):
            view["pending"] = pending(case["case_id"], case["submission_version"], digest)
        if settings.missing(paid=False):
            return view
        store = TursoStore(settings)
        view["store"] = store
        if not query:
            # Reuse only results explicitly loaded by this browser session. Startup
            # and navigation never query Turso or start intake/recovery work.
            cached = st.session_state.get(view_key(case, digest))
            if cached:
                view.update(cached)
            return view
        from underwriting.intake.screening import current_screening_record
        current = current_screening_record(case,store,digest)
        candidates = [current] if current else []
        view["latest_attempt"] = store.latest(case["case_id"], None, digest, successful=False)
        invalid_saved = False
        for saved in candidates:
            try:
                if not saved.get("result") or not saved["result"].get("basic_information"):
                    invalid_saved = True
                    continue
                check_ids = [item.get("check_id") for item in saved["result"].get("screening_checks", [])]
                complete = len(check_ids) == 6 and set(check_ids) == {f"SC{i:02d}" for i in range(1, 7)} and bool(saved.get("outcome"))
                if complete:
                    validate_output(saved["result"], pages, case["case_id"])
                    if saved.get("output_schema"):
                        Draft202012Validator(saved["output_schema"]).validate(saved["result"])
                    view["saved"] = saved
                else:
                    recovered = recover_screening(saved, pages, case)
                    if authorised(settings):
                        view["saved"] = save_recovery(settings, store, recovered)
                        view["latest_attempt"] = view["saved"]
                    else:
                        view["saved"] = dict(saved, outcome="Not screened yet")
                        view["notice"] = "Saved screening is incomplete. Local-owner recovery is required; no GPT call was made."
                break
            except (ValueError, TypeError, KeyError, JSONSchemaError):
                invalid_saved = True
        if not view["saved"]:
            view["stale"] = bool(store.latest(case["case_id"], None)) and not candidates
            if invalid_saved:
                view["error"] = "Saved analysis could not be validated. Review the stored run before explicitly regenerating; no GPT fallback was made."
    except (OSError, ValueError, StoreError, JSONSchemaError) as error:
        view["error"] = str(error) if isinstance(error, StoreError) else "Source or saved analysis could not be validated. Check the selected source and stored schema."
    if query:
        remember_view(case, view)
    return view


def controls(case, prefix, view, document_key, toggle_callback):
    settings = view["settings"]
    is_owner = authorised(settings)
    missing = settings.missing(paid=is_owner)
    previous = view["latest_attempt"]
    confirmation = False
    if previous:
        confirmation = st.checkbox("Confirm a new paid extraction for this source; preserve earlier runs",
            key=f"{prefix}:confirm_regeneration:{previous['run_id']}", disabled=not is_owner)
    with st.container(horizontal=True):
        clicked = st.button("Extract information and screen" if is_owner else "Load saved submission", type="primary",
            disabled=bool(settings.missing(paid=False)) or bool(view["pending"]),
            key=f"{prefix}:extract_and_screen")
        st.button("Hide company document" if st.session_state[document_key] else "View company document",
                  key=f"{prefix}:toggle_document", on_click=toggle_callback,
                  args=(document_key, not st.session_state[document_key]))
    if missing:
        st.caption("Missing configuration: " + ", ".join(missing))
    if not is_owner:
        st.caption("Read-only access. Load saved results and review the document; generation and changes are available only in the local owner app.")
    if view["error"]:
        st.warning(view["error"])
    if view.get("notice"):
        st.info(view["notice"])
    if view["stale"]:
        st.warning("Saved analysis is stale: the source version or hash changed. It is not used for this submission.")
    if view["saved"]:
        saved = view["saved"]
        historical = saved.get("configuration")
        with st.expander("Saved analysis details"):
            st.caption(f"Saved {saved['finished_at']} · Run {saved['run_id']} · {saved['status']}")
            if historical != configuration_metadata():
                st.caption("Saved with earlier settings. Original results are preserved; no new extraction was requested.")
            st.text("Configuration: " + (historical["version"] + " · " + historical["sha256"] if historical else "Not recorded by this earlier run"))
            st.text("Model: " + saved["model"])
            st.text("Schema: " + saved["schema_version"] + " · Policy: " + saved["policy_version"])
            st.text("Source: " + saved["source_version"] + " · " + saved["source_hash"])
            for name, details in saved["prompts"].items():
                st.text(name + " · " + details["version"] + " · " + details["sha256"])
        if saved.get("validation_issues"):
            with st.expander("Review required · source references and data quality"):
                st.caption("Some saved quotations could not be matched to the cited PDF page, or a saved value has an invalid format. Check these against the company document. Saved results have not been changed.")
                for issue in saved["validation_issues"]:
                    st.text(issue)
    if previous and previous["status"] == "failed":
        st.warning("The latest attempt failed; any earlier successful result is preserved.")
        if previous.get("error"):
            st.caption(previous["error"])
    progress_key = f"{prefix}:{view['source_hash']}:progress"
    if previous and previous["status"] == "failed" and previous.get("response_text"):
        with st.expander("Failed response validation details"):
            for issue in previous.get("validation_issues", []):
                st.text(issue)
        if st.button("Revalidate saved response · no GPT call", key=f"{prefix}:revalidate", disabled=not is_owner):
            try:
                recovered = revalidate_saved(settings, case, view["store"], previous)
                updated = dict(view, saved=recovered, latest_attempt=recovered)
                remember_view(case, updated)
                st.rerun()
            except (PermissionError, ValueError, OSError, StoreError) as error:
                st.error(str(error))
    if clicked:
        entries = []
        st.session_state[progress_key] = entries
        started = time.monotonic()
        with st.status("Intake progress", expanded=True) as status:
            def report(stage, detail):
                entries.append(f"{time.monotonic() - started:.1f}s · {stage}: {detail}")
                st.session_state[progress_key] = entries.copy()
                status.write(entries[-1])
                status.update(label=f"Intake progress · {stage}")
            try:
                report("Database", "Connecting to Turso and checking for saved results.")
                checked = load_view(case, query=True)
                if checked["error"]:
                    raise StoreError(checked["error"])
                if checked["saved"] and not confirmation:
                    report("Complete", "Saved results loaded; no GPT request was made.")
                    status.update(label="Saved results loaded", state="complete", expanded=False)
                    st.rerun()
                if not is_owner:
                    report("Read-only", "No saved intake available; extraction and writes are disabled.")
                    status.update(label="Read-only", state="complete")
                    return
                checked_previous = checked["latest_attempt"]
                if checked_previous and (not previous or checked_previous["run_id"] != previous["run_id"]):
                    report("Confirmation", "Prior attempt loaded. Confirm regeneration or revalidate its saved response without GPT.")
                    status.update(label="Awaiting your choice", state="complete")
                    st.rerun()
                result = run_intake(settings, st.session_state.get("intake_owner_proof"), case, view["store"],
                    expected_run=previous["run_id"] if previous else None, confirmed=confirmation, progress=report)
                if result["status"] == "failed":
                    report("Failed", result["error"])
                    status.update(label="Intake failed · see details", state="error")
                    for issue in result.get("validation_issues", []):
                        status.write(issue)
                    remember_view(case, dict(checked, latest_attempt=result))
                else:
                    report("Complete", "Saved to Turso; review issues remain." if result["status"] == "needs_review" else "Validated and saved to Turso.")
                    status.update(label="Saved · review required" if result["status"] == "needs_review" else "Intake complete", state="complete", expanded=False)
                    checked.update(saved=result, latest_attempt=result)
                    remember_view(case, checked)
                    st.rerun()
            except (PermissionError, OSError, ValueError, StoreError) as error:
                failing_stage = entries[-1].split(" · ", 1)[-1].split(":", 1)[0] if entries else "Database"
                report("Failed", f"{failing_stage}: {error}")
                status.update(label=f"Intake failed · {failing_stage}", state="error")
                st.error(str(error))
                st.info("Any received GPT response is retained for a save-only retry. No automatic GPT retry was made.")
    elif st.session_state.get(progress_key):
        with st.expander("Last intake progress"):
            for entry in st.session_state[progress_key]:
                st.text(entry)
    for item in view["pending"]:
        st.warning(f"Run {item['run_id']} is retained locally and has not been saved to Turso.")
        if item["status"] in {"completed", "needs_review", "failed"} or item.get("raw_response"):
            if st.button("Save retained result · no GPT call", key=f"{prefix}:retry_save:{item['run_id']}", disabled=not is_owner or view["store"] is None):
                try:
                    retry_save(settings, st.session_state.get("intake_owner_proof"), view["store"], item, case=case)
                    load_view(case, query=True)
                    st.rerun()
                except (PermissionError, ValueError, OSError, StoreError) as error:
                    st.error(str(error))
        else:
            st.caption("Interrupted request: the raw response/audit is retained if available. Owner recovery is required; no automatic new request.")


def saved_field(view, field):
    if not view["saved"]:
        return None
    aliases = view["saved"].get("configuration_snapshot", {}).get("intake", {}).get("field_aliases", UI_TO_PROMPT)
    identifier = aliases.get(field["id"], field["id"])
    return next((item for item in view["saved"]["result"]["basic_information"] if item["field_id"] == identifier), None)


def value_text(item):
    if item is None or item["value"] is None:
        return "—"
    value = item["value"]
    if isinstance(value, list):
        if not value:
            return "Explicitly reported empty list (see source)"
        return "; ".join(entry["description"] if isinstance(entry, dict) else entry for entry in value)
    return f"{value:,}" if isinstance(value, (float, int)) else value


def show_evidence(items):
    if not items:
        st.markdown("**Source references:** —")
    for item in items:
        st.caption(f"Page {item['page']} · {item['section'] or 'section not stated'} · {item['attribution'] or 'attribution not stated'}")
        st.text(item["quote"])


def selected_check(view, identifier):
    if not view["saved"]:
        return None
    return next((item for item in view["saved"]["result"].get("screening_checks", []) if item["check_id"] == identifier), None)
