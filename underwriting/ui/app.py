"""Streamlit composition root with validated configuration and tab-scoped state."""
import streamlit as st


def main():
    from underwriting.configuration.settings import get_config, ConfigError

    st.set_page_config(page_title="Explainable Property Underwriting", layout="wide")
    try:
        configuration = get_config()
    except ConfigError as error:
        st.error(str(error))
        st.stop()

    # Load schema-dependent features only after configuration validation succeeds.
    from underwriting.configuration.cases import load_cases
    from underwriting.ui.workspace import company_changed, workspace_scope, RESET_OPTION
    from underwriting.ui.submission import render_submission
    from underwriting.ui.assessment import render_assessment
    from underwriting.ui.decision import render_decision
    from underwriting.ui.overview import render_overview
    from underwriting.ui.submission_tables import apply_styles

    apply_styles()
    st.title(configuration["app"]["title"])
    st.caption('Version 2 · Research MVP')
    with st.expander('About this MVP'):
        st.write('Evidence-grounded property underwriting review with human oversight. This demonstration assesses Fire and Security concerns only; other perils and underwriting factors are outside the current model. It does not accept or decline insurance.')
        st.write('The attribute hierarchy, weights and rules are configurable, providing a foundation for extending the prototype after implementation and validation.')
    if configuration['services']['mode']=='offline':
        st.caption('Offline development · Version 2 local SQLite only · no external database connection or GPT generation. Saved local fixtures remain reviewable.')
    if configuration['services']['mode']=='turso_readonly':
        from underwriting.configuration.access import comparison_available
        if comparison_available(None):
            st.caption('Local comparison experiment · Load proposed beliefs may call GPT for missing methods and save separate proposals. Other workflows use saved results.')
        else:
            st.caption('Saved results · existing Turso database · read-only access · no GPT calls or database changes.')
    try:
        cases = load_cases()
    except (OSError, ValueError, KeyError, TypeError) as error:
        st.error(f"Case catalogue unavailable: {error}")
        st.stop()
    st.session_state.setdefault("selected_case_id", next(iter(cases)))
    case_id = st.selectbox("Company", [RESET_OPTION, *cases],
                          format_func=lambda key: "Reset workspace" if key == RESET_OPTION else cases[key]["company_name"],
                          key="selected_case_id", on_change=company_changed, args=(st.session_state,),
                          help="Reset workspace clears only the active tab. Saved records and the other tabs are preserved.")
    st.session_state["current_case_id"] = case_id
    reset_tab = st.session_state.pop("workspace_reset_notice", None)
    if reset_tab:
        st.success(f"{reset_tab} reset. Saved records and other tabs are unchanged.")
    case = cases[case_id]
    prefix = f"case:{case_id}"
    st.caption(f"{case_id} · Submission v{case['submission_version']} · {case['status']}")
    st.caption("Synthetic demonstration case; all company and submission details are fictional.")
    submission, assessment, decision, overview = st.tabs(["Submission", "Assessment", "Decision", "System overview"], key="active_workspace", on_change="rerun")
    with submission, workspace_scope(st.session_state, "Submission"):
        render_submission(case, prefix, configuration)
    with assessment, workspace_scope(st.session_state, "Assessment"):
        render_assessment(case, prefix)
    with decision, workspace_scope(st.session_state, "Decision"):
        render_decision(case, prefix)
    if overview.open:
        with overview, workspace_scope(st.session_state, "System overview"):
            render_overview(case, prefix)
