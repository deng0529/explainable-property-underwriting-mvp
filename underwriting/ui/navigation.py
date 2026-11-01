"""Programmatic stage changes force a fresh selector, including its highlight."""


def activate_stage(state, prefix, stage):
    if stage not in {"Evidence", "Beliefs", "ER results", "BRB results"}:
        raise ValueError("Unknown Assessment stage.")
    state[prefix + ":assessment_stage"] = stage
    revision = prefix + ":assessment_navigation_revision"
    state[revision] = state.get(revision, 0) + 1
