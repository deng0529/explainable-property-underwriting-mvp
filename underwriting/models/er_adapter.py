"""Configuration-to-core adapter. No API, database or Streamlit dependencies."""
from decimal import Decimal

from underwriting.configuration.decision_model import validate_model
import underwriting.models.er as er_core
from underwriting.models.er import METHOD_ID, ERInputError


def aggregate_configured_group(group_id, beliefs_by_attribute_id, model_config):
    validate_model(model_config)
    er = model_config["er"]
    if not er["enabled"] or er["method"] != METHOD_ID:
        raise ERInputError("ER is disabled or its configured method is not supported by this engine.")
    if group_id not in er["groups"]:
        raise ERInputError("Unknown configured ER group.")
    grades = model_config["grades"]["order"]
    tolerance = Decimal(str(model_config["tolerances"]["distribution_sum"]))
    cleanup = model_config["tolerances"].get("er_floating_cleanup", 1e-12)
    group = er["groups"][group_id]
    members = group["members"]
    relative = {identifier: Decimal(str(group["importance_weights"][identifier])) for identifier in members}
    total = sum(relative.values())
    effective = {identifier: float(relative[identifier] / total) for identifier in members}
    if any(relative[identifier] > 0 and effective[identifier] == 0 for identifier in members):
        raise ERInputError("A positive relative importance weight underflows when normalised; rescale/review the configuration.")
    matrix, supplied_unknown = [], {}
    for identifier in members:
        if identifier.startswith("er:"):
            nested = aggregate_configured_group(identifier[3:], beliefs_by_attribute_id, model_config)
            values = dict(nested["assigned_beliefs"], unknown=nested["unassigned_belief"])
        else:
            if identifier not in beliefs_by_attribute_id:
                raise ERInputError(f"Missing confirmed assessment for {identifier} (including zero-weight members).")
            values = beliefs_by_attribute_id[identifier]
        try:
            if any(isinstance(values[grade], (bool, str)) for grade in grades):
                raise ValueError
            row = [Decimal(str(values[grade])) for grade in grades]
            aliases = [name for name in ("unknown", model_config["grades"]["unassigned"]["field"]) if name in values]
            if not aliases:
                raise ERInputError(f"{identifier}: supplied Unknown/unassigned belief is required.")
            unknowns = [Decimal(str(values[name])) for name in aliases]
            if any(isinstance(values[name], (bool, str)) for name in aliases) or any(not value.is_finite() or value < 0 or value > 1 for value in row + unknowns):
                raise ValueError
            remainder = Decimal(1) - sum(row)
            if any(abs(value - remainder) > tolerance for value in unknowns):
                raise ERInputError(f"{identifier}: supplied Unknown disagrees with the assigned remainder.")
            supplied_unknown[identifier] = float(unknowns[0])
            matrix.append([float(value) for value in row])
        except (KeyError, TypeError, ValueError, ArithmeticError) as error:
            if isinstance(error, ERInputError):
                raise
            raise ERInputError(f"{identifier}: invalid/missing finite grade values.") from None
    result = er_core.combine_er(matrix, [effective[identifier] for identifier in members], tolerance=cleanup)
    return {"group_id": group_id, "label": group["label"], "grade_order": grades,
            "assigned_beliefs": dict(zip(grades, result["assigned_beliefs"])),
            "unassigned_belief": result["unassigned_belief"], "unassigned_label": model_config["grades"]["unassigned"]["label"],
            "relative_importance_weights": {identifier: float(value) for identifier, value in relative.items()},
            "effective_weights": effective, "supplied_unknown": supplied_unknown, "diagnostics": result["diagnostics"]}
