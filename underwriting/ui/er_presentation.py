"""Display-only summaries of saved ER inputs; no GPT or numerical inference."""
from math import isclose
from decimal import Decimal

from underwriting.models.brb_adapter import complete_group_distribution


def describe_er_result(result, model):
    """Descriptive maximum-component label; never used as an inference input."""
    distribution = complete_group_distribution(result, model)
    # Earlier historical models use their existing distribution tolerance.
    tolerance = Decimal(str(model["tolerances"].get("assessment_tie", model["tolerances"]["distribution_sum"])))
    if not tolerance.is_finite() or not 0 <= tolerance < 1:
        raise ValueError("assessment_tie must be finite and in [0,1).")
    values = {name: Decimal(str(value)) for name, value in distribution.items()}
    largest = max(values.values())
    winners = [name for name, value in values.items() if largest - value <= tolerance]
    label = "Mixed assessment" if len(winners) > 1 else (
        "Insufficient information" if winners[0] == "unknown" else model["grades"]["definitions"][winners[0]]["label"])
    return {"label": label, "winning_belief_degree": float(largest), "largest_components": winners,
            "tie_tolerance": float(tolerance), "full_distribution": distribution}


def group_presentation(run, group_id):
    """Return formatted input rows and weighted-support descriptions.

    Weight times input belief is an input support indicator, not an additive
    attribution of the nonlinear ER output. Never round calculation inputs.
    """
    model = run["decision_model_snapshot"]
    result = run["groups"][group_id]
    labels = {item["id"]: item["label"] for item in model["attributes"]}
    grades = model["grades"]["order"]
    inputs, rows = [], []
    for identifier, weight in result["effective_weights"].items():
        if identifier.startswith("er:"):
            nested = run["groups"][identifier[3:]]
            values = nested["assigned_beliefs"]
            label = nested["label"] + " ER"
        else:
            values = run["confirmed_beliefs"][identifier]
            label = identifier + " " + labels[identifier]
        unknown = result["supplied_unknown"][identifier]
        inputs.append((label, weight, dict(values, unknown=unknown)))
        rows.append({"Attribute": label,
                     **{model["grades"]["definitions"][grade]["label"]: f"{values[grade]:.2f}" for grade in grades},
                     "Unknown": f"{unknown:.2f}", "Weight": f"{weight:.2f}"})
    explanations = []
    for grade in [*grades, "unknown"]:
        scores = [(label, weight * values[grade]) for label, weight, values in inputs]
        maximum = max(score for _, score in scores)
        label = "Unknown" if grade == "unknown" else model["grades"]["definitions"][grade]["label"]
        if maximum == 0:
            explanations.append(f"{label}: no weighted input support." if grade != "unknown" else "Unknown: no unassigned belief in the active inputs.")
            continue
        leaders = [name for name, score in scores if isclose(score, maximum, rel_tol=1e-12, abs_tol=1e-15)]
        tied = " (tied)" if len(leaders) > 1 else ""
        description = "largest weighted information gap" if grade == "unknown" else "strongest weighted input support"
        explanations.append(f"{label}: {description} from {', '.join(leaders)}{tied}.")
    return rows, explanations
