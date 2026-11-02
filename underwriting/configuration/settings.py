"""One safe loader for declarative, non-secret application configuration."""
import hashlib
import json
import math
import os
import re
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import yaml

from underwriting.paths import PROJECT_ROOT

ROOT = PROJECT_ROOT
CONFIG_PATH = ROOT / "config" / "settings.yaml"


class ConfigError(ValueError):
    pass


class UniqueSafeLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader, node, deep=False):
    pairs = loader.construct_pairs(node, deep=deep)
    result = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in result:
            raise ConfigError("duplicate or invalid YAML mapping key.")
        result[key] = value
    return result


UniqueSafeLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def resolve_path(value):
    path = (ROOT / value).resolve()
    if not path.is_relative_to(ROOT):
        raise ConfigError("Configured file paths must stay inside the project root.")
    return path


def validate(data):
    def required(mapping, name, kind, path=""):
        location = f"{path}.{name}".strip(".")
        if not isinstance(mapping, dict) or name not in mapping or not isinstance(mapping[name], kind) or (kind is int and isinstance(mapping[name], bool)):
            raise ConfigError(f"{location}: missing or invalid type.")
        return mapping[name]
    def text(mapping, name, path=""):
        value = required(mapping, name, str, path)
        if not value.strip():
            raise ConfigError(f"{path}.{name}: must be non-empty.")
        return value
    def unique(values, location):
        if not values or len(values) != len(set(values)):
            raise ConfigError(f"{location}: identifiers must be non-empty and unique.")
    if not isinstance(data, dict):
        raise ConfigError("config/settings.yaml: a settings mapping is required.")
    # Credentials belong only in .env or deployed secrets, never in this file.
    forbidden = {"openai_api_key", "turso_database_url", "turso_auth_token", "intake_admin_password", "api_key", "auth_token", "admin_password"}
    def inspect_keys(value):
        if isinstance(value, dict):
            if any(key.lower() in forbidden for key in value):
                raise ConfigError("Credentials must not be stored in config/settings.yaml.")
            for child in value.values():
                inspect_keys(child)
        elif isinstance(value, list):
            for child in value:
                inspect_keys(child)
    inspect_keys(data)
    text(data, "config_version")
    access = required(data, "access", dict)
    if access.get("mode") not in {"local_owner", "public_readonly"}:
        raise ConfigError("access.mode: use local_owner or public_readonly.")
    llm = required(data, "llm", dict)
    model = required(llm, "model", (str, type(None)), "llm")
    if isinstance(model, str) and not model.strip():
        raise ConfigError("llm.model: use a model identifier or null for unconfigured.")
    timeout = required(llm, "timeout_seconds", (int, float), "llm")
    if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
        raise ConfigError("llm.timeout_seconds: must be a positive finite number.")
    if required(llm, "max_retries", int, "llm") != 0:
        raise ConfigError("llm.max_retries: must be 0 at this checkpoint.")
    generation = required(llm, "generation", dict, "llm")
    supported = required(llm, "supported_generation_parameters", list, "llm")
    known = {"temperature", "top_p", "max_output_tokens", "reasoning_effort"}
    if any(not isinstance(name, str) or name not in known for name in supported) or not set(generation) <= set(supported):
        raise ConfigError("llm.generation: only parameters explicitly listed in llm.supported_generation_parameters may be sent.")
    if generation and not model:
        raise ConfigError("llm.model: select a model before configuring generation parameters.")
    for name, value in generation.items():
        if name == "reasoning_effort":
            if value not in {"none", "minimal", "low", "medium", "high", "xhigh"}:
                raise ConfigError("llm.generation.reasoning_effort: invalid value.")
        elif isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ConfigError(f"llm.generation.{name}: invalid numeric value.")
        elif name == "max_output_tokens" and (not isinstance(value, int) or value < 1):
            raise ConfigError("llm.generation.max_output_tokens: positive integer required.")
        elif name == "temperature" and not 0 <= value <= 2:
            raise ConfigError("llm.generation.temperature: must be in [0,2].")
        elif name == "top_p" and not 0 <= value <= 1:
            raise ConfigError("llm.generation.top_p: must be in [0,1].")
    prompts = required(data, "prompts", dict)
    for name in ("system", "user"):
        prompt = required(prompts, name, dict, "prompts")
        path = resolve_path(text(prompt, "path", f"prompts.{name}"))
        text(prompt, "version", f"prompts.{name}")
        if not path.is_file():
            raise ConfigError(f"prompts.{name}.path: file not found.")
    evidence_prompt = required(prompts, "attribute_evidence", dict, "prompts")
    text(evidence_prompt, "version", "prompts.attribute_evidence")
    template = text(evidence_prompt, "template", "prompts.attribute_evidence")
    if set(re.findall(r"{{(\w+)}}", template)) != {"attribute_definitions_from_yaml", "case_id_and_source_version", "page_labelled_original_pdf_text"}:
        raise ConfigError("prompts.attribute_evidence.template: required placeholders are missing or unknown.")
    beliefs_prompt = required(prompts, "attribute_beliefs", dict, "prompts")
    text(beliefs_prompt, "version", "prompts.attribute_beliefs")
    template = text(beliefs_prompt, "template", "prompts.attribute_beliefs")
    if set(re.findall(r"{{(\w+)}}", template)) != {"belief_increment", "attribute_definitions_from_yaml", "case_id_and_source_version", "reviewed_attribute_evidence_json"}:
        raise ConfigError("prompts.attribute_beliefs.template: required placeholders are missing or unknown.")
    decision_prompt = required(prompts, "decision_review", dict, "prompts")
    text(decision_prompt, "version", "prompts.decision_review")
    template = text(decision_prompt, "template", "prompts.decision_review")
    if set(re.findall(r"{{(\w+)}}", template)) != {"configured_routing_guidance", "analysis_snapshot_json"}:
        raise ConfigError("prompts.decision_review.template: required placeholders are missing or unknown.")
    decision = required(data, "decision", dict)
    guidance = required(decision, "routing_guidance", list, "decision")
    if not guidance or any(not isinstance(line, str) or not line.strip() for line in guidance):
        raise ConfigError("decision.routing_guidance: non-empty text entries required.")
    belief_settings = required(data, "belief_assessment", dict)
    for name in ("increment", "sum_tolerance"):
        value = required(belief_settings, name, (int, float), "belief_assessment")
        if isinstance(value, bool) or not math.isfinite(value) or not 0 < value <= 1:
            raise ConfigError(f"belief_assessment.{name}: a finite number in (0,1] is required.")
    if Decimal("1") % Decimal(str(belief_settings["increment"])) != 0 or belief_settings["sum_tolerance"] >= belief_settings["increment"] / 2:
        raise ConfigError("belief_assessment: increment must divide 1 and sum_tolerance must be smaller than half the increment.")
    intake = required(data, "intake", dict)
    text(intake, "schema_version", "intake")
    groups = required(intake, "field_groups", list, "intake")
    labels, identifiers = [], []
    for group in groups:
        labels.append(text(group, "label", "intake.field_groups"))
        text(group, "empty_status", "intake.field_groups")
        for field in required(group, "fields", list, "intake.field_groups"):
            identifier = text(field, "id", "intake.field_groups.fields")
            if not re.fullmatch(r"[a-z][a-z0-9_]*", identifier):
                raise ConfigError("intake.field_groups.fields.id: invalid identifier.")
            identifiers.append(identifier)
            text(field, "label", "intake.field_groups.fields")
            if field.get("type") not in {"text", "repeating_table"}:
                raise ConfigError("intake.field_groups.fields.type: unsupported display type.")
            if field["type"] == "repeating_table":
                columns = required(field, "columns", list, "intake.field_groups.fields")
                if any(not isinstance(column, str) for column in columns):
                    raise ConfigError("intake.field_groups.fields.columns: strings required.")
                unique(columns, "intake.field_groups.fields.columns")
    unique(labels, "intake.field_groups")
    unique(identifiers, "intake.field_groups.fields")
    aliases = required(intake, "field_aliases", dict, "intake")
    if not set(aliases) <= set(identifiers) or any(not isinstance(value, str) for value in aliases.values()):
        raise ConfigError("intake.field_aliases: unknown field identifiers.")
    unique([aliases.get(identifier, identifier) for identifier in identifiers], "intake.field_aliases")
    display = required(intake, "display_groups", dict, "intake")
    displayed = []
    for name, members in display.items():
        if not name or not isinstance(members, list) or any(not isinstance(member, str) for member in members):
            raise ConfigError("intake.display_groups: category lists are required.")
        displayed.extend(members)
    if set(displayed) != set(labels) or len(displayed) != len(labels):
        raise ConfigError("intake.display_groups: each field group must occur exactly once.")
    screen = required(data, "screening", dict)
    text(screen, "policy_version", "screening")
    checks = required(screen, "checks", list, "screening")
    check_ids = [text(check, "id", "screening.checks") for check in checks]
    unique(check_ids, "screening.checks")
    if set(check_ids) != {f"SC{i:02d}" for i in range(1, 7)}:
        raise ConfigError("screening.checks: the approved SC01–SC06 identifiers are required.")
    for check in checks:
        text(check, "name", "screening.checks")
        text(check, "question", "screening.checks")
    selectors = required(screen, "selector_labels", dict, "screening")
    if set(selectors) != set(check_ids) or any(not isinstance(value, str) or not value.strip() for value in selectors.values()):
        raise ConfigError("screening.selector_labels: all check IDs are required.")
    for name, expected in (("check_results", {"Pass", "Fail", "Unknown"}),
                           ("workflow_outcomes", {"Proceed to assessment", "Pause for clarification", "Outside demo scope"})):
        values = required(screen, name, list, "screening")
        if any(not isinstance(value, str) for value in values) or set(values) != expected or len(values) != len(expected):
            raise ConfigError(f"screening.{name}: must match the approved policy.")
    for name in ("scope_failure_checks", "essential_failure_checks"):
        values = required(screen, name, list, "screening")
        if any(not isinstance(value, str) or value not in check_ids for value in values):
            raise ConfigError(f"screening.{name}: unknown check ID.")
    scope = required(screen, "scope", dict, "screening")
    if any(not isinstance(value, str) for value in required(scope, "territories", list, "screening.scope")):
        raise ConfigError("screening.scope.territories: strings required.")
    text(scope, "activity", "screening.scope")
    text(scope, "synthetic_exception_case_id", "screening.scope")
    required(scope, "one_occupied_site", bool, "screening.scope")
    required(scope, "requires_property_damage", bool, "screening.scope")
    policy = {key: value for key, value in screen.items() if key not in {"selector_labels", "approved_policy_hash"}}
    policy_hash = hashlib.sha256(json.dumps(policy, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if text(screen, "approved_policy_hash", "screening") != policy_hash:
        raise ConfigError("screening.approved_policy_hash: policy settings changed; review the matching prompts and policy before updating the approval hash.")
    if "assessment" in data or "overall_rules" in data:
        raise ConfigError("Attribute/ER/BRB definitions belong only in decision_model_path, not competing settings blocks.")
    model_path = resolve_path(text(data, "decision_model_path"))
    from underwriting.configuration.decision_model import load_model
    load_model(model_path)
    app = required(data, "app", dict)
    text(app, "title", "app")
    if not resolve_path(text(app, "case_catalogue_path", "app")).is_file():
        raise ConfigError("app.case_catalogue_path: file not found.")
    if app.get("default_information_category") not in display or app.get("default_screening_check") not in check_ids:
        raise ConfigError("app: invalid default category or screening check.")
    required(app, "document_initially_visible", bool, "app")
    return data


def read_yaml(path):
    try:
        data = yaml.load(Path(path).read_text(encoding="utf-8"), Loader=UniqueSafeLoader)
    except ConfigError as error:
        raise ConfigError(Path(path).name + ": " + str(error)) from None
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        line = f" at line {mark.line + 1}" if mark else ""
        raise ConfigError(Path(path).name + ": malformed YAML" + line + ".") from None
    except OSError:
        raise ConfigError(Path(path).name + ": file unavailable.") from None
    return data


def load_config(path=CONFIG_PATH):
    return validate(read_yaml(path))


@lru_cache(maxsize=2)
def _profile_config(profile):
    data = load_config()
    if profile:
        if profile != 'public_readonly':
            raise ConfigError('UNDERWRITING_PROFILE: only public_readonly is supported.')
        overlay = read_yaml(ROOT / 'config' / 'public_readonly.yaml')
        if overlay != {'access': {'mode': 'public_readonly'}}:
            raise ConfigError('public_readonly.yaml must only set access.mode to public_readonly.')
        data['access']['mode'] = overlay['access']['mode']
    return data


def get_config():
    return _profile_config(os.environ.get('UNDERWRITING_PROFILE', ''))


get_config.cache_clear = _profile_config.cache_clear


def configuration_metadata():
    from underwriting.configuration.decision_model import model_metadata
    data = {"settings": get_config(), "decision_model": model_metadata()}
    digest = hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return {"version": data["settings"]["config_version"], "sha256": digest}
