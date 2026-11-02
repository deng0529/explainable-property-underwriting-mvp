"""Display one Assessment stage at a time, without loading or calculating data."""
import streamlit as st

from underwriting.ui.beliefs import belief_details
from underwriting.ui.evidence import attribute_details
from underwriting.ui.er import er_results
from underwriting.ui.brb import brb_results


def render_workspace(prefix, attributes, definitions, attribute_key, evidence, beliefs, er, brb=None):
    stage_key = prefix + ":assessment_stage"
    stage = st.session_state.setdefault(stage_key, "Evidence")
    if stage == "ER results":
        st.subheader("Fire and Security ER results")
        er_results(er)
        return
    if stage == "BRB results":
        brb_results(brb)
        return
    selecting_beliefs = stage == "Beliefs"
    left, right = st.columns([22, 78] if selecting_beliefs else [1.35, 1.65], gap="large")
    with left:
        st.subheader("Attributes")
        for group, names in attributes.items():
            st.markdown(f"**{group}**")
            for name in names:
                st.button(name, key=f"{prefix}:select:{name}", width="stretch",
                          type="primary" if st.session_state[attribute_key] == name else "secondary",
                          on_click=select_attribute, args=(attribute_key, name))
    with right:
        selected = st.session_state[attribute_key]
        st.subheader(selected)
        st.caption(definitions[selected])
        if selecting_beliefs:
            belief_details(beliefs, selected.split()[0])
        else:
            attribute_details(evidence, selected.split()[0])


def select_attribute(key, name):
    st.session_state[key] = name


def render_assessment(case, prefix):
    from underwriting.configuration.decision_model import attribute_groups
    from underwriting.ui.evidence import evidence_controls
    from underwriting.ui.beliefs import belief_controls
    from underwriting.ui.er import er_controls
    from underwriting.ui.brb import brb_controls

    # Reuse explicitly loaded Submission state only; no database query on reruns.
    from underwriting.ui.intake import view_key
    from underwriting.assessment.evidence_service import document_hash
    from underwriting.intake.screening import resolve_screening
    try:
        cached = st.session_state.get(view_key(case, document_hash(case)), {})
        if cached.get('saved'):
            st.caption('Screening: ' + resolve_screening(cached['saved'],case)['outcome'])
    except OSError:
        pass
    attribute_key = f"{prefix}:attribute"
    attributes = {
        name: {f"{attribute['id']} {attribute['label']}": attribute["definition"] for attribute in members}
        for name, members in attribute_groups().items()
    }
    definitions = {name: definition for group in attributes.values() for name, definition in group.items()}
    st.session_state.setdefault(attribute_key, next(iter(definitions)))
    from pathlib import Path
    st.html(Path(__file__).parent / "styles" / "assessment.css")
    with st.container(key="assessment_actions"):
        actions = st.columns(4, gap="small")
    evidence_view = evidence_controls(case, prefix, button_slot=actions[0])
    belief_view = belief_controls(case, prefix, evidence_view, button_slot=actions[1])
    er_view = er_controls(case, prefix, belief_view, actions[2])
    brb_view = brb_controls(case, prefix, er_view, actions[3])
    render_workspace(prefix, attributes, definitions, attribute_key, evidence_view, belief_view, er_view, brb_view)
