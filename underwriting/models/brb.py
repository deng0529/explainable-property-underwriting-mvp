"""Pure RIMER-style matching/activation and shared analytical ER aggregation.

No UI, persistence, GPT, insurance identifiers, scalar transformation or training.
Input distributions already use the configured reference states. See docs/brb.md.
Matching/activation checked against desdeo-brb compute_activation_weights at commit
6744efbabeb27fe15dc52c516d86c929030b230e. Explicit deviations (including epsilon)
are documented in THIRD_PARTY_NOTICES.md; no upstream scalar interpolation is used.
"""
import copy
import hashlib
import itertools
import json
import math

from underwriting.models.brb_adapter import combine_activated_rule_conclusions
from underwriting.models.er import ENGINE_VERSION as ER_ENGINE_VERSION

ENGINE_VERSION = "rimer-activation-analytical-er-v1"
METHOD = "rimer_activation_analytical_er"


def _number(value, name):
    try:
        valid = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value >= 0
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(name + ": finite non-negative numeric value required.")
    return value


def _states(values, name):
    if not isinstance(values, list) or not values or any(not isinstance(value, str) or not value for value in values) or len(set(values)) != len(values):
        raise ValueError(name + ": nonempty unique state IDs required.")
    return values


def _distribution(values, states, tolerance, name, complete):
    if not isinstance(values, dict) or set(values) != set(states):
        raise ValueError(name + ": distribution keys must match configured states.")
    masses = [_number(value, name) for value in values.values()]
    total = math.fsum(masses)
    if any(value > 1 for value in masses) or total > 1 + tolerance or (complete and abs(total - 1) > tolerance):
        raise ValueError(name + ": invalid distribution total.")


def infer_brb(input_distributions, brb_config, *, distribution_tolerance=1e-6, er_tolerance=1e-12):
    """Infer from complete state distributions without interpolation or rounding.

    Attribute weights are divided by their MAXIMUM, not by their sum. Supplied
    rule weights multiply products of matching degrees to these relative powers.
    Log-scaled normalisation avoids treating numerical underflow as no matches.
    All activations (not just display-selected ones) enter the shared ER core.
    """
    config = brb_config
    if not config.get("enabled") or not config.get("configured") or config.get("method") != METHOD:
        raise ValueError("BRB is disabled, unconfigured, or its method is unsupported.")
    if not isinstance(config.get("version"), str) or not config["version"].strip() or config.get("adapter") != "unassigned_to_unknown_state":
        raise ValueError("An explicit BRB version and supported input adapter are required.")
    if not 0 < _number(distribution_tolerance, "distribution_tolerance") < 1:
        raise ValueError("distribution_tolerance must be in (0,1).")
    definitions = config.get("inputs")
    if not isinstance(definitions, dict) or not definitions or not isinstance(input_distributions, dict) or set(input_distributions) != set(definitions):
        raise ValueError("Input distributions must match all configured input IDs.")
    for identifier, definition in definitions.items():
        states = _states(definition["states"], "input states")
        _distribution(input_distributions[identifier], states, distribution_tolerance, identifier, True)
    weights = config.get("input_attribute_weights")
    if not isinstance(weights, dict) or set(weights) != set(definitions):
        raise ValueError("Antecedent attribute-weight keys must match configured inputs.")
    maximum = max(_number(value, "attribute weight") for value in weights.values())
    if maximum == 0:
        raise ValueError("All-zero antecedent attribute weights are undefined.")
    relative = {key: value / maximum for key, value in weights.items()}
    states = _states(config["output"]["states"], "output states")
    rules = config.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ValueError("A nonempty rule base is required.")
    ids, combinations, enabled_combinations, trace, logs = set(), set(), set(), [], []
    for rule in rules:
        identifier = rule.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in ids:
            raise ValueError("Rule IDs must be nonempty and unique.")
        ids.add(identifier)
        antecedents = rule.get("antecedents")
        if not isinstance(antecedents, dict) or set(antecedents) != set(definitions) or any(antecedents[key] not in definitions[key]["states"] for key in definitions):
            raise ValueError("Rule antecedents must reference every configured input and valid state.")
        combination = tuple(antecedents[key] for key in definitions)
        if combination in combinations:
            raise ValueError("Duplicate rule antecedent combination.")
        combinations.add(combination)
        if not isinstance(rule.get("enabled"), bool):
            raise ValueError("Rule enabled status must be boolean.")
        if rule["enabled"]:
            enabled_combinations.add(combination)
        weight = _number(rule.get("rule_weight"), "rule weight")
        _distribution(rule.get("conclusion_beliefs"), states, distribution_tolerance, identifier + " conclusions", False)
        matching = {key: input_distributions[key][antecedents[key]] for key in definitions}
        factors = {key: (1.0 if relative[key] == 0 else matching[key] ** relative[key]) for key in definitions}
        matched = rule["enabled"] and weight > 0 and all(matching[key] > 0 or relative[key] == 0 for key in definitions)
        log_strength = math.log(weight) + math.fsum(relative[key] * math.log(matching[key]) for key in definitions if relative[key] > 0) if matched else -math.inf
        strength = weight * math.prod(factors.values()) if rule["enabled"] else 0.0
        trace.append({"rule_id": identifier, "enabled": rule["enabled"], "antecedents": dict(antecedents),
                      "matching_degrees": matching, "relative_attribute_weights": dict(relative), "matching_factors": factors,
                      "rule_weight": weight, "strength": strength, "log_strength": log_strength if matched else None,
                      "activation_weight": 0.0, "conclusion_beliefs": dict(rule["conclusion_beliefs"])})
        logs.append(log_strength)
    if config.get("complete_rule_base"):
        product = set(itertools.product(*(definition["states"] for definition in definitions.values())))
        if enabled_combinations != product:
            raise ValueError("Enabled complete rule base must cover the Cartesian product exactly.")
    if not any(rule["enabled"] and rule["rule_weight"] > 0 for rule in rules):
        raise ValueError("Enabled rule weights must have a positive total.")
    result = {"status": "no_matching_rules", "input_distributions": copy.deepcopy(input_distributions),
              "adapter_information": {"method": config.get("adapter"), "unknown_meaning": "Explicit insufficient-information state; lower-level ER remainder is mapped here."},
              "attribute_weights": dict(weights), "relative_attribute_weights": relative, "rules": trace,
              "state_beliefs": None, "residual_unassigned": None, "engine_version": ENGINE_VERSION,
              "er_engine_version": ER_ENGINE_VERSION, "method": METHOD, "brb_version": config.get("version"),
              "configuration_hash": hashlib.sha256(json.dumps(config, sort_keys=True, allow_nan=False).encode()).hexdigest()}
    peak = max(logs)
    if peak == -math.inf:
        result["message"] = "No matching rules. No overall prediction was produced."
        return result
    scaled = [math.exp(value - peak) if value != -math.inf else 0.0 for value in logs]
    total = math.fsum(scaled)
    activations = [value / total for value in scaled]
    for row, activation in zip(trace, activations):
        row["activation_weight"] = activation
    combined = combine_activated_rule_conclusions([rule["conclusion_beliefs"] for rule in rules], activations, states, tolerance=er_tolerance)
    if abs(math.fsum(combined["state_beliefs"].values()) + combined["residual_unassigned_belief"] - 1) > distribution_tolerance:
        raise ArithmeticError("BRB output mass accounting failed.")
    result.update(status="completed", state_beliefs=combined["state_beliefs"],
                  residual_unassigned=combined["residual_unassigned_belief"], er_diagnostics=combined["diagnostics"],
                  numerical_underflow_rules=[row["rule_id"] for row in trace if row["log_strength"] is not None and row["strength"] == 0],
                  activation_underflow_rules=[row["rule_id"] for row in trace if row["log_strength"] is not None and row["activation_weight"] == 0])
    return result
