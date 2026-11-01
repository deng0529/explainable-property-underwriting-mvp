"""Future decision-recording controls; deliberately disabled in version 0.1.0."""
import streamlit as st


def future_button(label, key):
    st.button(label, disabled=True, key=key, help="Not implemented yet")


def render_decision(case, prefix):
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
