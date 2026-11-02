"""Submission-only presentation: lazy sections and compact, escaped detail tables."""
import streamlit as st

from underwriting.ui.intake import saved_field, value_text, show_evidence, selected_check, STATUS_LABELS
from underwriting.ui.submission_tables import show_table


def set_session_value(key, value):
    st.session_state[key] = value


def change_page(key, offset):
    st.session_state[key] += offset


def render_submission(case, prefix, configuration):
    from underwriting.ui.intake import load_view, controls
    from underwriting.ui.submission_review import review_controls

    with st.container(key="submission_presentation"):
        page_key, document_key = f"{prefix}:pdf_page", f"{prefix}:document_visible"
        st.session_state.setdefault(page_key, 1)
        st.session_state.setdefault(document_key, False)
        view = load_view(case)
        controls(case, prefix, view, document_key, set_session_value)
        render_document(case, prefix, document_key, page_key)
        basic, screening = st.tabs(["Basic information", "Screening"],
                                   key=f"{prefix}:submission_section", on_change="rerun")
        with basic:
            if basic.open:
                render_basic_information(case, prefix, configuration, view)
        with screening:
            if screening.open:
                render_screening(case, prefix, configuration, view)
        with st.expander("Underwriter confirmation and review"):
            review_controls(case, view)


def render_basic_information(case, prefix, configuration, view):
    from underwriting.ui.submission_review import basic_review_notes

    groups = {group["label"]: group for group in configuration["intake"]["field_groups"]}
    categories = configuration["intake"]["display_groups"]
    with st.container(key="submission_basic_workspace"):
        selector, content = st.columns([22, 78], gap="medium", wrap=True)
        with selector:
            category = st.radio("Information category", list(categories),
                                index=list(categories).index(configuration["app"]["default_information_category"]),
                                key=f"{prefix}:information_category", persist_state="session", label_visibility="collapsed")
        with content:
            st.subheader(category)
            selected_groups = [groups[label] for label in categories[category]]
            rows, losses = [], []
            for group in selected_groups:
                for field in group["fields"]:
                    extracted = saved_field(view, field)
                    status = STATUS_LABELS[extracted["status"]] if extracted else group["empty_status"]
                    if field["type"] == "repeating_table":
                        records = extracted["value"] if extracted and isinstance(extracted["value"], list) else []
                        value = f"{len(records)} loss record(s); see below" if records else (
                            "Explicitly reported no loss records; see sources" if extracted and extracted["value"] == [] else "—")
                        losses.append((field, extracted, records))
                    else:
                        value = value_text(extracted)
                        if extracted and extracted["value"] is not None and extracted.get("unit"):
                            value += " " + extracted["unit"]
                    rows.append([field["label"], value, status])
            show_table(["Field", "Value", "Status"], rows, label=category + " basic information")
            for field, extracted, records in losses:
                st.markdown("**" + field["label"] + "**")
                keys = ["loss_date", "description", "amount_paid", "outstanding_amount", "remedial_action"]
                show_table(field["columns"], [[record.get(key) for key in keys] for record in records],
                           label=field["label"], kind="losses")
                if extracted and extracted.get("unit"):
                    st.caption("Currency: " + extracted["unit"])
            with st.expander("Sources and details"):
                for group in selected_groups:
                    for field in group["fields"]:
                        extracted = saved_field(view, field)
                        status = STATUS_LABELS[extracted["status"]] if extracted else group["empty_status"]
                        st.caption(field["label"] + " · " + status)
                        if not extracted:
                            st.markdown("**Quotation:** — · **Reference:** —")
                            continue
                        show_evidence(extracted["evidence"])
                        if extracted.get("unit"):
                            st.text("Unit: " + extracted["unit"])
                        if extracted.get("note"):
                            st.text(extracted["note"])
                        if field["type"] == "repeating_table" and extracted["value"]:
                            for record in extracted["value"]:
                                show_evidence(record["evidence"])
                        if field["id"] in {"missing_information", "conflicting_information", "referenced_documents_not_supplied"} and extracted["value"]:
                            for item in extracted["value"]:
                                st.text(f"{item['description']} ({item['origin']})")
                                show_evidence(item["evidence"])
    with st.expander("Underwriter notes · basic information"):
        basic_review_notes(case, view)


def render_screening(case, prefix, configuration, view):
    from underwriting.ui.submission_review import screening_review_notes

    saved = view["saved"]
    from underwriting.intake.screening import resolve_screening
    resolution = resolve_screening(saved,case)
    st.markdown("**Outcome:** " + resolution['outcome'])
    if resolution.get('record_id'):
        st.caption('Screening record: ' + resolution['record_id'] + ' · ' + resolution['record_version'])
    if resolution.get('override'):
        st.warning('Explicit human screening override: ' + resolution['override']['outcome'] + ' · ' + resolution['override']['reason'])
    if resolution['outcome'] in {'Pause for clarification','Outside demo scope'}:
        for item in resolution['reasons']:
            st.warning(item['check_id'] + ': ' + item['reason'] + ' Clarification needed: ' + item['clarification_needed'])
    if saved and saved.get("validation_issues"):
        st.caption("Data-validation concerns remain for human review and Decision follow-up; they do not silently override the six screening checks.")
    screening = configuration["screening"]
    checks = {check["id"]: check for check in screening["checks"]}
    with st.container(key="submission_screening_workspace"):
        selector, content = st.columns([22, 78], gap="medium", wrap=True)
        with selector:
            identifier = st.radio("Screening check", list(checks),
                                  index=list(checks).index(configuration["app"]["default_screening_check"]),
                                  format_func=screening["selector_labels"].get, key=f"{prefix}:screening_check",
                                  persist_state="session", label_visibility="collapsed")
        with content:
            check = checks[identifier]
            observation = selected_check(view, identifier)
            rows = [["Check ID and name", check["id"] + " — " + check["name"]],
                    ["Screening question", check["question"]],
                    ["Result", observation["result"].title() if observation else "Not screened yet"],
                    ["Reason", observation["reason"] if observation else None]]
            if observation and observation.get("information_required"):
                rows.append(["Information required", observation["information_required"]])
            show_table(["Item", "Details"], rows, label=identifier + " screening details", kind="checks")
            with st.expander("Sources and details"):
                show_evidence(observation["evidence"] if observation else [])
            with st.expander("Underwriter notes · " + identifier):
                screening_review_notes(case, view, identifier)
    with st.expander("Review flags and information required"):
        st.markdown("**Non-blocking review flags**")
        if not saved:
            st.caption("Not assessed yet")
        else:
            flags = saved["result"]["non_blocking_review_flags"]
            if not flags:
                st.caption("No non-blocking flags reported by this extraction.")
            for flag in flags:
                st.text(flag["issue"])
                show_evidence(flag["evidence"])
        st.markdown("**Information required**")
        if saved:
            for flag in saved["result"]["non_blocking_review_flags"]:
                st.text(flag["information_required"])
        else:
            st.caption("Not assessed yet")
    with st.expander("Screening scope and result definitions"):
        st.caption("MVP screening rules, not Protector’s acceptance rules. A pass permits further assessment; it does not accept the insurance.")
        st.caption("Permitted check results after screening: " + " / ".join(screening["check_results"]))
        st.caption("Overall workflow outcomes after screening: " + " / ".join(screening["workflow_outcomes"]))


def render_document(case, prefix, document_key, page_key):
    from pathlib import Path
    from underwriting.documents.pdf import read_source, render_pages

    if st.session_state[document_key]:
        st.subheader("Company document")
        try:
            pdf_bytes = read_source(case["document_path"])
        except OSError as error:
            st.error(f"Source PDF unavailable. Restore {case['document_path']}. Details: {error}")
        else:
            st.download_button("Download original PDF", data=pdf_bytes,
                               file_name=Path(case["document_path"]).name,
                               mime="application/pdf", key=f"{prefix}:download")
            try:
                previews = render_pages(pdf_bytes)
                if not previews:
                    raise ValueError("The document contains no pages.")
                if len(previews) != case.get("expected_pages", len(previews)):
                    st.warning(f"Found {len(previews)} pages; check the submission version.")
                st.session_state[page_key] = min(max(st.session_state[page_key], 1), len(previews))
                previous, position, following = st.columns([1, 1, 1], vertical_alignment="center")
                previous.button("Previous page", key=f"{prefix}:previous_page",
                                disabled=st.session_state[page_key] == 1,
                                on_click=change_page, args=(page_key, -1))
                position.caption(f"Page {st.session_state[page_key]} of {len(previews)}")
                following.button("Next page", key=f"{prefix}:next_page",
                                 disabled=st.session_state[page_key] == len(previews),
                                 on_click=change_page, args=(page_key, 1))
                st.image(previews[st.session_state[page_key] - 1], width="stretch",
                         alt=f"{case['company_name']} submission, page {st.session_state[page_key]}")
            except Exception as error:
                st.error(f"PDF previews unavailable. Download the original PDF to review it. Details: {error}")
