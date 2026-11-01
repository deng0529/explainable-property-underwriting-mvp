"""Submission document, basic-information and screening presentation."""
import streamlit as st


def set_session_value(key, value):
    st.session_state[key] = value


def change_page(key, offset):
    st.session_state[key] += offset


def render_submission(case, prefix, configuration):
    from pathlib import Path
    from underwriting.documents.pdf import read_source, render_pages
    from underwriting.ui.intake import load_view, controls, saved_field, value_text, show_evidence, selected_check, STATUS_LABELS
    from underwriting.ui.submission_review import basic_review_notes, screening_review_notes, review_controls

    page_key = f"{prefix}:pdf_page"
    st.session_state.setdefault(page_key, 1)
    document_key = f"{prefix}:document_visible"
    st.session_state.setdefault(document_key, configuration["app"]["document_initially_visible"])
    intake_view = load_view(case)
    controls(case, prefix, intake_view, document_key, set_session_value)
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

    st.subheader("Basic information")
    field_schema = {"groups": configuration["intake"]["field_groups"]}
    groups = {group["label"]: group for group in field_schema["groups"]}
    # Presentation mapping only: the underlying schema and field IDs are unchanged.
    categories = configuration["intake"]["display_groups"]
    selector, content = st.columns([1, 3], gap="large")
    with selector:
        category = st.radio("Information category", list(categories), index=list(categories).index(configuration["app"]["default_information_category"]), key=f"{prefix}:information_category",
                            persist_state="session", label_visibility="collapsed")
    with content:
        st.markdown(f"**{category}**")
        selected_groups = [groups[label] for label in categories[category]]
        st.caption("Saved extraction" if intake_view["saved"] else selected_groups[0]["empty_status"])
        label_column, value_column = st.columns([2, 3])
        label_column.caption("Field")
        value_column.caption("Value")
        for group in selected_groups:
            for field in group["fields"]:
                extracted = saved_field(intake_view, field)
                status = STATUS_LABELS[extracted["status"]] if extracted else group["empty_status"]
                if field["type"] == "repeating_table":
                    st.markdown(f"**{field['label']}**")
                    records = extracted["value"] if extracted and isinstance(extracted["value"], list) else []
                    keys = ["loss_date", "description", "amount_paid", "outstanding_amount", "remedial_action"]
                    rows = [{label: (record.get(key) if record.get(key) is not None else "—")
                             for label, key in zip(field["columns"], keys)} for record in records]
                    st.dataframe(rows if rows else {label: [] for label in field["columns"]}, hide_index=True,
                                 alt="Previous loss records from the saved submission extraction")
                    st.caption(status)
                    if extracted and extracted.get("unit"):
                        st.caption("Currency: " + extracted["unit"])
                    if extracted and extracted["value"] == []:
                        st.caption("Explicitly reported no loss records; see evidence and note.")
                else:
                    label_column, value_column = st.columns([2, 3])
                    label_column.markdown(field["label"])
                    value_column.text(value_text(extracted) + (" " + extracted["unit"] if extracted and extracted["unit"] else ""))
                    if extracted:
                        value_column.caption(status)
                        if extracted["status"] == "conflicting":
                            value_column.warning(extracted["note"] or "Conflicting source statements; review required.")
        with st.expander("Source quotations and references"):
            for group in selected_groups:
                for field in group["fields"]:
                    extracted = saved_field(intake_view, field)
                    st.caption(f"{field['label']} · {STATUS_LABELS[extracted['status']] if extracted else group['empty_status']}")
                    if extracted:
                        show_evidence(extracted["evidence"])
                        if extracted["note"]:
                            st.text(extracted["note"])
                        if field["type"] == "repeating_table" and extracted["value"]:
                            for record in extracted["value"]:
                                show_evidence(record["evidence"])
                        if field["id"] in {"missing_information", "conflicting_information", "referenced_documents_not_supplied"} and extracted["value"]:
                            for item in extracted["value"]:
                                st.text(f"{item['description']} ({item['origin']})")
                                show_evidence(item["evidence"])
                    else:
                        st.markdown("**Quotation:** — · **Reference:** —")

    basic_review_notes(case, intake_view)
    st.divider()
    st.subheader("Screening")
    saved = intake_view["saved"]
    outcome = saved["outcome"] if saved else "Not screened yet"
    st.markdown(f"**Outcome:** {outcome}")
    if saved and saved.get("validation_issues"):
        st.caption("Review required: saved source references or value formats need checking. See Review required above for the specific issues.")
    st.caption("MVP screening rules, not Protector’s acceptance rules. A pass permits further assessment; it does not accept the insurance.")
    screening_config = configuration["screening"]
    checks = {check["id"]: check for check in screening_config["checks"]}
    check_labels = screening_config["selector_labels"]
    selector, content = st.columns([1, 3], gap="large")
    with selector:
        check_id = st.radio("Screening check", list(checks), index=list(checks).index(configuration["app"]["default_screening_check"]), format_func=check_labels.get,
                            key=f"{prefix}:screening_check", persist_state="session",
                            label_visibility="collapsed")
    with content:
        check = checks[check_id]
        st.markdown(f"**{check['id']} — {check['name']}**")
        st.caption(check["question"])
        observation = selected_check(intake_view, check_id)
        st.markdown(f"**Result:** {observation['result'].title() if observation else 'Not screened yet'}")
        st.markdown("**Reason:**")
        st.text(observation["reason"] if observation else "—")
        with st.expander("Screening source references"):
            show_evidence(observation["evidence"] if observation else [])
        screening_review_notes(case, intake_view, check_id)
    with st.expander("Review flags and information required"):
        st.markdown("**Non-blocking review flags**")
        if saved:
            flags = saved["result"]["non_blocking_review_flags"]
            if not flags:
                st.caption("No non-blocking flags reported by this extraction.")
            for flag in flags:
                st.text(flag["issue"])
                show_evidence(flag["evidence"])
        else:
            st.caption("Not assessed yet")
        st.markdown("**Information required**")
        if saved:
            for flag in saved["result"]["non_blocking_review_flags"]:
                st.text(flag["information_required"])
        else:
            st.caption("Not assessed yet")
    review_controls(case, intake_view)
    with st.expander("Future screening result definitions"):
        st.caption("Permitted check results after screening: " + " / ".join(screening_config["check_results"]))
        st.caption("Overall workflow outcomes after screening: " + " / ".join(screening_config["workflow_outcomes"]))
