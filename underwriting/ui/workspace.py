"""Tab-scoped session resets; persistent records and retained files survive."""
from contextlib import contextmanager

RESET_OPTION = "__reset_workspace__"


@contextmanager
def workspace_scope(state, tab):
    before = set(state)
    try:
        yield
    finally:
        registry = state.setdefault("workspace_keys", {})
        registry[tab] = set(registry.get(tab, set())) | (set(state) - before - {"workspace_keys"})


def reset_workspace(state, tab=None):
    tab = tab or state.get("active_workspace", "Submission")
    registry = state.get("workspace_keys", {})
    for key in registry.get(tab, set()):
        if key in state:
            del state[key]
    registry[tab] = set()
    state["workspace_keys"] = registry
    state["workspace_reset_notice"] = tab


def company_changed(state):
    if state.get("selected_case_id") == RESET_OPTION:
        reset_workspace(state)
        state["selected_case_id"] = state["current_case_id"]
    else:
        state["current_case_id"] = state["selected_case_id"]
