"""Display one Assessment stage at a time, without loading or calculating data."""
import streamlit as st

from underwriting.ui.beliefs import belief_details, proposal_for, confirmed_for, belief_status
from underwriting.ui.evidence import attribute_details
from underwriting.ui.er import er_results
from underwriting.ui.brb import brb_results


def render_workspace(prefix, attributes, definitions, attribute_key, evidence, beliefs, er, brb=None):
    stage_key = prefix + ":assessment_stage"
    stages = ["Evidence"]
    if beliefs.get("run") and not beliefs.get("stale"):
        stages.append("Beliefs")
    if er.get("run"):
        stages.append("ER results")
    if brb and brb.get("run"):
        stages.append("BRB results")
    if st.session_state.get(stage_key) not in stages:
        st.session_state[stage_key] = "Evidence"
    if len(stages) > 1:
        desired = st.session_state[stage_key]
        revision = st.session_state.get(prefix + ":assessment_navigation_revision", 0)
        stage = st.segmented_control("Assessment view", stages, default=desired,
                                     key=f"{prefix}:assessment_view:{revision}") or desired
        st.session_state[stage_key] = stage
    else:
        stage = "Evidence"
    if stage == "ER results":
        st.subheader("Fire and Security ER results")
        er_results(er)
        return
    if stage == "BRB results":
        brb_results(brb)
        return
    selecting_beliefs = stage == "Beliefs"
    left, right = st.columns([1.35, 1.65], gap="large")
    with left:
        st.subheader("Attributes")
        for group, names in attributes.items():
            st.markdown(f"**{group}**")
            if selecting_beliefs:
                for column, heading in zip(st.columns([3.6, .8, .8, .9, 1.7]), ("Attribute", "Low", "High", "Unknown", "Confirmation")):
                    column.caption(heading)
            for name in names:
                identifier = name.split()[0]
                row = st.columns([3.6, .8, .8, .9, 1.7]) if selecting_beliefs else [st]
                row[0].button(name, key=f"{prefix}:select:{name}", width="stretch",
                              type="primary" if st.session_state[attribute_key] == name else "secondary",
                              on_click=select_attribute, args=(attribute_key, name))
                if selecting_beliefs:
                    confirmed = confirmed_for(beliefs, identifier)
                    values = confirmed["values"] if confirmed else proposal_for(beliefs, identifier)
                    for column, grade in zip(row[1:4], ("low", "high", "unknown")):
                        column.caption(f"{values[grade]:.2f}" if values else "—")
                    row[4].caption(belief_status(beliefs, identifier))
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

    attribute_key = f"{prefix}:attribute"
    attributes = {
        name: {f"{attribute['id']} {attribute['label']}": attribute["definition"] for attribute in members}
        for name, members in attribute_groups().items()
    }
    definitions = {name: definition for group in attributes.values() for name, definition in group.items()}
    st.session_state.setdefault(attribute_key, next(iter(definitions)))
    actions = st.columns(4, gap="small")
    evidence_view = evidence_controls(case, prefix, button_slot=actions[0])
    belief_view = belief_controls(case, prefix, evidence_view, button_slot=actions[1])
    er_view = er_controls(case, prefix, belief_view, actions[2])
    brb_view = brb_controls(case, prefix, er_view, actions[3])
    st.caption("1. Load evidence → review and confirm evidence. 2. Load beliefs → confirm beliefs. 3. Calculate Fire / Security ER. 4. Calculate overall BRB assessment.")
    render_workspace(prefix, attributes, definitions, attribute_key, evidence_view, belief_view, er_view, brb_view)
