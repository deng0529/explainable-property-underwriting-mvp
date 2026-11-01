"""Validate declarative ER/BRB scaffolding. No numerical inference lives here."""
import hashlib
import itertools
import json
import math
from decimal import Decimal
from functools import lru_cache


def validate_model(model):
    from underwriting.configuration.settings import ConfigError
    def fail(path, message):
        raise ConfigError(f"decision_model.{path}: {message}")
    def need(obj, name, kind, path):
        if not isinstance(obj, dict) or name not in obj or not isinstance(obj[name], kind) or (kind is int and isinstance(obj[name], bool)):
            fail(f"{path}.{name}".strip("."), "missing or invalid type")
        return obj[name]
    def text(obj, name, path):
        value = need(obj, name, str, path)
        if not value.strip():
            fail(path + "." + name, "non-empty text required")
        return value
    def identifiers(values, path):
        if not values or any(not isinstance(value, str) or not value.strip() for value in values) or len(set(values)) != len(values):
            fail(path, "non-empty unique IDs required")
        return values
    def number(value, path, positive=False):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or (positive and value <= 0):
            fail(path, "finite non-negative number required" if not positive else "positive finite number required")
        return Decimal(str(value))
    def weights(obj, expected, path):
        if not isinstance(obj, dict) or set(obj) != set(expected):
            fail(path, "weight keys must exactly match configured references")
        values = [number(value, path + "." + key) for key, value in obj.items()]
        if sum(values) <= 0:
            fail(path, "total weight must be positive")
    def named_fields(obj, path):
        return text(obj, "field", path), text(obj, "label", path)
    text(model, "model_version", "")
    tolerances = need(model, "tolerances", dict, "")
    tolerance = number(need(tolerances, "distribution_sum", (float, int), "tolerances"), "tolerances.distribution_sum", True)
    if tolerance >= 1:
        fail("tolerances.distribution_sum", "must be smaller than 1")
    if number(need(tolerances, "weight_zero", (float, int), "tolerances"), "tolerances.weight_zero", True) >= 1:
        fail("tolerances.weight_zero", "must be smaller than 1")
    if "er_floating_cleanup" in tolerances and number(tolerances["er_floating_cleanup"], "tolerances.er_floating_cleanup", True) > Decimal("1e-9"):
        fail("tolerances.er_floating_cleanup", "floating cleanup tolerance must be no greater than 1e-9")
    if "assessment_tie" in tolerances and number(tolerances["assessment_tie"], "tolerances.assessment_tie") >= 1:
        fail("tolerances.assessment_tie", "must be smaller than 1")
    grades = need(model, "grades", dict, "")
    order = identifiers(need(grades, "order", list, "grades"), "grades.order")
    definitions = need(grades, "definitions", dict, "grades")
    if set(order) != set(definitions):
        fail("grades", "grade order must contain each defined assigned grade exactly once")
    for identifier, definition in definitions.items():
        text(definition, "label", "grades.definitions." + identifier)
        text(definition, "definition", "grades.definitions." + identifier)
    unassigned = need(grades, "unassigned", dict, "grades")
    named_fields(unassigned, "grades.unassigned")
    text(unassigned, "definition", "grades.unassigned")
    if "unknown" in order:
        fail("grades.order", "Unknown is unassigned belief, not an assigned ER grade")
    attributes_list = need(model, "attributes", list, "")
    identifiers([text(attribute, "id", "attributes") for attribute in attributes_list], "attributes")
    attributes = {attribute["id"]: attribute for attribute in attributes_list}
    if any(":" in identifier for identifier in attributes):
        fail("attributes", "attribute IDs must not use calculation-reference namespace separators")
    for identifier, attribute in attributes.items():
        text(attribute, "label", f"attributes.{identifier}")
        text(attribute, "definition", f"attributes.{identifier}")
        need(attribute, "enabled", bool, f"attributes.{identifier}")
        for key in ("aspects", "belief_aspects"):
            if key in attribute:
                identifiers(need(attribute, key, list, f"attributes.{identifier}"), f"attributes.{identifier}.{key}")
    if not any(attribute["enabled"] for attribute in attributes_list):
        fail("attributes", "at least one attribute must be enabled")
    er = need(model, "er", dict, "")
    er_enabled = need(er, "enabled", bool, "er")
    text(er, "method", "er")
    if er_enabled and er["method"] == "not_selected":
        fail("er.method", "an enabled model needs an explicit method identifier")
    if er.get("normalization_policy") != "divide_by_group_total":
        fail("er.normalization_policy", "supported declarative policy is divide_by_group_total")
    groups = need(er, "groups", dict, "er")
    identifiers(list(groups), "er.groups")
    if any(":" in identifier for identifier in groups):
        fail("er.groups", "group IDs must not contain namespace separators")
    graph = {"er:" + identifier: [] for identifier in groups}
    for identifier, group in groups.items():
        path = "er.groups." + identifier
        text(group, "label", path)
        members = identifiers(need(group, "members", list, path), path + ".members")
        weights(need(group, "importance_weights", dict, path), members, path + ".importance_weights")
        for member in members:
            if member in attributes:
                if er_enabled and not attributes[member]["enabled"]:
                    fail(path + ".members", "an enabled calculation cannot use a disabled attribute")
            elif member.startswith("er:") and member in graph:
                graph["er:" + identifier].append(member)
            else:
                fail(path + ".members", "unknown attribute/group reference")
    brb = need(model, "brb", dict, "")
    text(brb, "id", "brb")
    enabled = need(brb, "enabled", bool, "brb")
    configured = need(brb, "configured", bool, "brb")
    text(brb, "method", "brb")
    if enabled and (not configured or brb["method"] == "not_selected" or not er_enabled):
        fail("brb", "enabled BRB requires configured rules, a selected method and enabled ER sources")
    if enabled:
        if brb["method"] != "rimer_activation_analytical_er":
            fail("brb.method", "unsupported enabled BRB method")
        text(brb, "version", "brb")
        text(brb, "assumptions", "brb")
        threshold = number(need(brb, "active_rule_display_tolerance", (float, int), "brb"), "brb.active_rule_display_tolerance")
        if threshold >= 1:
            fail("brb.active_rule_display_tolerance", "must be smaller than 1")
    if brb.get("adapter") != "unassigned_to_unknown_state":
        fail("brb.adapter", "explicit unassigned_to_unknown_state adapter is required")
    inputs = need(brb, "inputs", dict, "brb")
    identifiers(list(inputs), "brb.inputs")
    node = "brb:" + brb["id"]
    graph[node] = []
    for identifier, input_definition in inputs.items():
        path = "brb.inputs." + identifier
        reference = text(input_definition, "reference", path)
        if reference not in graph or not reference.startswith("er:"):
            fail(path + ".reference", "BRB inputs must reference a configured ER output")
        graph[node].append(reference)
        states = identifiers(need(input_definition, "states", list, path), path + ".states")
        if "unknown" not in states or not set(order) <= set(states):
            fail(path + ".states", "assigned grades and the explicit unknown interface state are required")
        meanings = need(input_definition, "state_definitions", dict, path)
        if set(meanings) != set(states) or any(not isinstance(value, str) or not value.strip() for value in meanings.values()):
            fail(path + ".state_definitions", "define every reference state, including unknown")
    weights(need(brb, "input_attribute_weights", dict, "brb"), inputs, "brb.input_attribute_weights")
    output = need(brb, "output", dict, "brb")
    states = identifiers(need(output, "states", list, "brb.output"), "brb.output.states")
    if not set(order) <= set(states):
        fail("brb.output.states", "assigned output grades must be represented explicitly")
    unknown = need(output, "explicit_unknown", dict, "brb.output")
    residual = need(output, "residual_unassigned", dict, "brb.output")
    unknown_field, unknown_label = named_fields(unknown, "brb.output.explicit_unknown")
    residual_field, residual_label = named_fields(residual, "brb.output.residual_unassigned")
    if unknown.get("state") not in states or unknown.get("state") != "unknown" or unknown_field == residual_field or unknown_label == residual_label:
        fail("brb.output", "explicit Unknown-state belief and residual unassigned belief need different fields and labels")
    rules = need(brb, "rules", list, "brb")
    identifiers([text(rule, "id", "brb.rules") for rule in rules], "brb.rules")
    combinations, active_combinations = set(), set()
    for rule in rules:
        path = "brb.rules." + rule["id"]
        active = need(rule, "enabled", bool, path)
        antecedents = need(rule, "antecedents", dict, path)
        if set(antecedents) != set(inputs) or any(not isinstance(value, str) or value not in inputs[name]["states"] for name, value in antecedents.items()):
            fail(path + ".antecedents", "use every configured input and one of its valid states")
        combination = tuple(antecedents[name] for name in inputs)
        if combination in combinations:
            fail(path + ".antecedents", "duplicate rule combination")
        combinations.add(combination)
        if active:
            active_combinations.add(combination)
        number(need(rule, "rule_weight", (int, float), path), path + ".rule_weight")
        if "conclusion_beliefs" not in rule:
            fail(path + ".conclusion_beliefs", "must be an explicit null or distribution")
        conclusion = rule["conclusion_beliefs"]
        if conclusion is None:
            if enabled or configured:
                fail(path + ".conclusion_beliefs", "null conclusions are permitted only while BRB is disabled")
        else:
            if not isinstance(conclusion, dict) or set(conclusion) != set(states):
                fail(path + ".conclusion_beliefs", "distribution keys must match output states")
            masses = [number(value, path + ".conclusion_beliefs." + name) for name, value in conclusion.items()]
            if any(value > 1 for value in masses) or sum(masses) > 1 + tolerance:
                fail(path + ".conclusion_beliefs", "belief total must not exceed 1 within tolerance")
    if need(brb, "complete_rule_base", bool, "brb") and enabled:
        product = set(itertools.product(*(definition["states"] for definition in inputs.values())))
        if active_combinations != product:
            fail("brb.rules", "enabled complete rule base must cover the Cartesian product")
    if enabled and sum(Decimal(str(rule["rule_weight"])) for rule in rules if rule["enabled"]) <= 0:
        fail("brb.rules", "enabled rules need positive total rule weight")
    visiting, visited = set(), set()
    def walk(identifier):
        if identifier in visiting:
            fail("references", "circular calculation reference")
        if identifier in visited:
            return
        visiting.add(identifier)
        for dependency in graph[identifier]:
            walk(dependency)
        visiting.remove(identifier)
        visited.add(identifier)
    for identifier in graph:
        walk(identifier)
    return model


def load_model(path):
    from underwriting.configuration.settings import read_yaml
    return validate_model(read_yaml(path))


@lru_cache(maxsize=1)
def get_model():
    from underwriting.configuration.settings import get_config, resolve_path
    return load_model(resolve_path(get_config()["decision_model_path"]))


def model_metadata():
    model = get_model()
    digest = effective_model_hash(model)
    return {"version": model["model_version"], "sha256": digest}


def effective_model_hash(model):
    return hashlib.sha256(json.dumps(model, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def effective_er_model_hash(model):
    """ER dependencies only: changing BRB rules must not require recalculating ER."""
    subset = {key: model[key] for key in ("grades", "attributes", "er")}
    subset["tolerances"] = {key: model["tolerances"][key] for key in ("distribution_sum", "er_floating_cleanup") if key in model["tolerances"]}
    return effective_model_hash(subset)


def attributes(enabled_only=True):
    return [attribute for attribute in get_model()["attributes"] if not enabled_only or attribute["enabled"]]


def attribute_groups():
    """UI group membership from the model; labels/definitions have one source."""
    definitions = {attribute["id"]: attribute for attribute in attributes()}
    groups, used = {}, set()
    for group in get_model()["er"]["groups"].values():
        members = [definitions[identifier] for identifier in group["members"] if identifier in definitions]
        if members:
            groups[group["label"]] = members
            used.update(attribute["id"] for attribute in members)
    remaining = [attribute for identifier, attribute in definitions.items() if identifier not in used]
    if remaining:
        groups["Other attributes"] = remaining
    return groups
