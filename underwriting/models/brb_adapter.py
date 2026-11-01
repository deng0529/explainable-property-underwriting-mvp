"""BRB interfaces to the shared ER engine; no matching or activation inference.

Rule activations must be calculated and normalised by the caller. Attribute
importance weights, antecedent weights and relative rule weights are not accepted
as substitutes. Explicit output Unknown is a state; residual ignorance is separate.
"""
from decimal import Decimal, InvalidOperation

import underwriting.models.er as er_core


def complete_group_distribution(group_result, model_config):
    """Copy the complete numeric ER distribution, including unassigned Unknown.

    Labels, winning components and display rounding are never inputs to this adapter.
    """
    if not isinstance(group_result, dict):
        raise er_core.ERInputError("BRB requires complete numeric ER distributions, not winning labels.")
    grades = model_config["grades"]["order"]
    assigned = group_result.get("assigned_beliefs")
    if not isinstance(assigned, dict) or set(assigned) != set(grades) or "unassigned_belief" not in group_result:
        raise er_core.ERInputError("A complete assigned-grade distribution and unassigned belief are required.")
    distribution = dict(assigned, unknown=group_result["unassigned_belief"])
    try:
        values = [Decimal(str(value)) for value in distribution.values()]
        if any(isinstance(value, (bool, str)) for value in distribution.values()) or any(not value.is_finite() or not 0 <= value <= 1 for value in values):
            raise ValueError
        if abs(sum(values) - 1) > Decimal(str(model_config["tolerances"]["distribution_sum"])):
            raise ValueError
    except (InvalidOperation, ValueError, TypeError):
        raise er_core.ERInputError("The complete group distribution must contain finite numeric beliefs totalling one within tolerance.") from None
    return distribution


def prepare_brb_inputs(er_group_results, model_config):
    """Map ALL ER components to BRB reference states, without running BRB.

    ER unassigned belief becomes the explicit unknown input state, as specified by
    unassigned_to_unknown_state. No information is replaced by a winning label.
    """
    from underwriting.configuration.decision_model import validate_model
    validate_model(model_config)
    inputs = {}
    for identifier, definition in model_config["brb"]["inputs"].items():
        group_id = definition["reference"].removeprefix("er:")
        if group_id not in er_group_results:
            raise er_core.ERInputError("Missing ER group distribution: " + group_id)
        distribution = complete_group_distribution(er_group_results[group_id], model_config)
        if set(definition["states"]) != set(distribution):
            raise er_core.ERInputError("Configured BRB input states do not match the complete ER distribution.")
        inputs[identifier] = {state: distribution[state] for state in definition["states"]}
    return inputs


def combine_activated_rule_conclusions(rule_conclusions, activation_weights, output_states, *, tolerance=1e-12):
    """Aggregate supplied conclusions with calculated activation shares using ER.

    This low-level hook does not select rules, calculate activations, or enable the
    unconfigured application rule base. Each conclusion is a mapping of every
    configured output state (including explicit Unknown) to assigned belief; it may
    be incomplete. Activations must already be finite, nonnegative, and sum to one.
    No second ER formula, weight normalisation or row renormalisation is used here.
    """
    states = list(output_states)
    if not states or any(not isinstance(state, str) or not state for state in states) or len(set(states)) != len(states):
        raise er_core.ERInputError("Output states must be nonempty unique identifiers.")
    conclusions = list(rule_conclusions)
    if any(not isinstance(conclusion, dict) or set(conclusion) != set(states) for conclusion in conclusions):
        raise er_core.ERInputError("Every rule conclusion must contain all configured output states; null conclusions cannot be aggregated.")
    weights = list(activation_weights)
    result = er_core.combine_er([[conclusion[state] for state in states] for conclusion in conclusions], weights, tolerance=tolerance)
    return {"state_beliefs": dict(zip(states, result["assigned_beliefs"])),
            "residual_unassigned_belief": result["unassigned_belief"],
            "activation_weights": weights, "diagnostics": result["diagnostics"]}
