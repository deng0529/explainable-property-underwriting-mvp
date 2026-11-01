"""Deterministic explanations from a saved BRB trace; no inference or services."""
from math import isclose


def input_support_summary(result, config):
    """Compare input patterns, without claiming additive output attribution."""
    lines = []
    for state in config["output"]["states"]:
        candidates = [(identifier, values[state] * result["relative_attribute_weights"][identifier])
                      for identifier, values in result["input_distributions"].items() if state in values]
        if not candidates:
            continue
        largest = max(score for _, score in candidates)
        if largest == 0:
            lines.append(state.title() + ": no weight-adjusted input support.")
            continue
        leaders = [identifier for identifier, score in candidates if score > 0 and isclose(score, largest, rel_tol=1e-12, abs_tol=0)]
        details = "; ".join(f"{identifier.title()} (belief {result['input_distributions'][identifier][state]:.2f}, relative weight {result['relative_attribute_weights'][identifier]:.2f})" for identifier in leaders)
        lines.append(state.title() + (": strongest input support from " if len(leaders) == 1 else ": tied input support from ") + details + ".")
    return lines


def strongest_rule_summary(result):
    active = [rule for rule in result["rules"] if rule["activation_weight"] > 0]
    if not active:
        return "No matching rule produced an overall result."
    largest = max(rule["activation_weight"] for rule in active)
    leaders = [rule for rule in active if isclose(rule["activation_weight"], largest, rel_tol=1e-12, abs_tol=0)]
    if len(leaders) > 1:
        return "Most active rules: " + ", ".join(rule["rule_id"] for rule in leaders) + f" ({largest * 100:.2f}% each). Their configured conclusion distributions are combined using ER."
    rule = leaders[0]
    matches = " and ".join(state.title() + " " + identifier.title() for identifier, state in rule["antecedents"].items())
    conclusions = ", ".join(f"{state.title()} {value:.2f}" for state, value in rule["conclusion_beliefs"].items())
    return f"Most active rule: {rule['rule_id']} ({largest * 100:.2f}%). It matches {matches}. Its configured conclusion is {conclusions}."


def activation_summary(result):
    enabled = [rule for rule in result["rules"] if rule["enabled"]]
    count = sum(rule["activation_weight"] > 0 for rule in enabled)
    if count and count == len(enabled):
        if all(weight > 0 for weight in result["relative_attribute_weights"].values()) and all(value > 0 for values in result["input_distributions"].values() for value in values.values()):
            return f"All {count} rules are active because each input has nonzero belief in every reference state. Every antecedent combination therefore has a positive match."
        return f"All {count} enabled rules have positive matching strength. Zero-weight inputs do not restrict matching."
    return f"{count} of {len(enabled)} enabled rules have positive activation. Rules with zero matching strength are inactive."
