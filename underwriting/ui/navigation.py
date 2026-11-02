"""Company-scoped Assessment stage selection; no services or calculations."""


def activate_stage(state, prefix, stage):
    if stage not in {"Evidence", "Beliefs", "ER results", "BRB results"}:
        raise ValueError("Unknown Assessment stage.")
    state[prefix + ":assessment_stage"] = stage
    revision = prefix + ":assessment_navigation_revision"
    state[revision] = state.get(revision, 0) + 1


def stage_button(slot, label, prefix, stage, *, permitted=True, **kwargs):
    """Select a permitted stage before rerendering its primary action button."""
    import streamlit as st
    active = st.session_state.get(prefix + ":assessment_stage", "Evidence")
    return slot.button(label, type="primary" if active == stage else "secondary",
                       on_click=select_stage if permitted else None,
                       args=(prefix, stage) if permitted else None,
                       **kwargs)


def select_stage(prefix, stage):
    import streamlit as st
    activate_stage(st.session_state, prefix, stage)
