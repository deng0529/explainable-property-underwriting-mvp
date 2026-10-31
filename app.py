"""Three-tab framework and company document review only."""
from pathlib import Path

import streamlit as st

from case_catalogue import load_cases
from source_document import read_source, render_pages

ATTRIBUTES = {
    "Fire": {
        "F1 Combustible storage": "Combustible stock, packaging, storage arrangements and housekeeping relevant to fire concern.",
        "F2 Ignition controls": "Electrical installations, charging, smoking and hot-work controls relevant to ignition concern.",
        "F3 Fire protection": "Compartmentation, fire detection and suppression. Individual findings and conflicting indications will be preserved separately.",
    },
    "Security": {
        "S1 Access protection": "Physical entry protection, perimeter arrangements, key management and access procedures.",
        "S2 Intrusion detection": "Intruder alarm coverage, monitoring, maintenance and response arrangements.",
    },
}
DEFINITIONS = {name: definition for group in ATTRIBUTES.values() for name, definition in group.items()}


def choose_attribute(state_key, attribute):
    st.session_state[state_key] = attribute


def change_page(state_key, offset):
    st.session_state[state_key] += offset


def future_button(label, key):
    st.button(label, disabled=True, key=key, help="Not implemented yet")


def belief_fields(label, prefix):
    st.markdown(f"**{label}**")
    for column, grade in zip(st.columns(3), ("Low", "High", "Unknown")):
        column.text_input(grade, value="", disabled=True, key=f"{prefix}:{grade}")


st.set_page_config(page_title="Explainable property underwriting", layout="wide")
st.title("Explainable property underwriting")
try:
    cases = load_cases()
except (OSError, ValueError, KeyError, TypeError) as error:
    st.error(f"Case catalogue unavailable: {error}")
    st.stop()
case_id = st.selectbox("Company", list(cases), format_func=lambda key: cases[key]["company_name"],
                      key="selected_case_id")
case = cases[case_id]
prefix = f"case:{case_id}"
page_key, attribute_key = f"{prefix}:pdf_page", f"{prefix}:attribute"
st.session_state.setdefault(page_key, 1)
st.session_state.setdefault(attribute_key, next(iter(DEFINITIONS)))
st.caption(f"{case_id} · Submission v{case['submission_version']} · {case['status']}")
st.caption("Synthetic demonstration case; all company and submission details are fictional.")

submission, assessment, decision = st.tabs(["Submission", "Assessment", "Decision"])

with submission:
    st.markdown("**Prepared summary — not AI extracted**")
    st.markdown(case["prepared_summary"])
    st.caption(case["summary_reference"])
    document, information = st.columns([2.2, 1], gap="large")
    with document:
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
    with information:
        st.subheader("Basic information")
        st.caption("Extraction · Not implemented yet")
        st.text_area("Company, site and requested cover", value="", disabled=True,
                     key=f"{prefix}:basic_information")
        future_button("Extract basic information", f"{prefix}:extract")
        st.subheader("Screening")
        st.caption("Not implemented yet · No screening outcome")
        st.text_area("Screening proposal", value="", disabled=True, key=f"{prefix}:screening")
        future_button("Run screening", f"{prefix}:run_screening")

with assessment:
    with st.container(horizontal=True):
        for action in ("Find evidence", "Propose beliefs", "Calculate assessment"):
            future_button(action, f"{prefix}:{action}")
    st.caption("Not implemented yet · Assessment values are blank; no review or calculation has occurred.")
    attributes, details = st.columns([1.35, 1.65], gap="large")
    with attributes:
        st.subheader("Attributes")
        for group, names in ATTRIBUTES.items():
            st.markdown(f"**{group}**")
            headings = st.columns([3.6, 0.7, 0.7, 1.1, 1.7], gap="small")
            for col, heading in zip(headings, ("Attribute", "Low", "High", "Unknown", "Review")):
                col.caption(heading)
            for name in names:
                row = st.columns([3.6, 0.7, 0.7, 1.1, 1.7], gap="small")
                row[0].button(name, key=f"{prefix}:select:{name}",
                              type="primary" if st.session_state[attribute_key] == name else "secondary",
                              on_click=choose_attribute, args=(attribute_key, name), width="stretch")
                # Numerical cells remain empty; no scores have been proposed.
                row[4].caption("Not reviewed")
    with details:
        selected = st.session_state[attribute_key]
        st.subheader(selected)
        st.caption(DEFINITIONS[selected])
        detail_prefix = f"{prefix}:{selected}"
        for label in ("Source quotations and page references", "Facts", "Missing or conflicting information", "AI explanation"):
            st.text_area(label, value="", disabled=True, height=70, key=f"{detail_prefix}:{label}")
        with st.expander("Proposed and human-confirmed beliefs", expanded=True):
            belief_fields("Proposed beliefs", f"{detail_prefix}:proposed")
            belief_fields("Human-confirmed beliefs", f"{detail_prefix}:confirmed")
            with st.container(horizontal=True):
                future_button("Edit evidence / beliefs", f"{detail_prefix}:edit")
                future_button("Confirm beliefs", f"{detail_prefix}:confirm")
    st.subheader("Assessment results")
    for col, label in zip(st.columns(3), ("Fire ER", "Security ER", "Overall BRB assessment")):
        with col, st.container(border=True):
            belief_fields(label, f"{prefix}:result:{label}")
            st.caption("Not calculated")
    with st.expander("Model settings · weights, rules and calculation details"):
        st.markdown("**Planned weights:** Fire attributes 1/3 each; Security attributes 1/2 each.")
        st.markdown("**Rules:** Nine top-level combinations are envisaged. Rule conclusions and rule weights require review; no rule base is implemented.")
        st.markdown("**Calculation details:** The ER formulation must be specified and verified. Top-level Unknown treatment and the BRB inference formulation remain to be specified.")
        st.caption("Unknown represents unassigned belief at the attribute/group layer. No adapter or inference calculation is implemented.")

with decision:
    st.subheader("Decision review")
    concerns, missing = st.columns(2)
    concerns.text_area("Key concerns", value="", disabled=True, key=f"{prefix}:concerns")
    missing.text_area("Missing information", value="", disabled=True, key=f"{prefix}:missing")
    st.selectbox("Next action", ["Request information", "Refer for specialist review", "Continue underwriting review"],
                 index=None, disabled=True, key=f"{prefix}:next_action")
    st.text_area("Decision notes", value="", disabled=True, key=f"{prefix}:decision_notes")
    with st.container(horizontal=True):
        future_button("Save Decision", f"{prefix}:save")
        future_button("Export", f"{prefix}:export")
    st.caption("Not implemented yet · No decision has been recorded.")
