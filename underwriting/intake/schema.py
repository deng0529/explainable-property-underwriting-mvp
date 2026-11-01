"""Typed model output and independent source/policy validation. No model calls."""
import calendar
import hashlib
import json
import re
import unicodedata
from datetime import date
from typing import Literal, Union

from pydantic import BaseModel, ConfigDict, Field, create_model

from underwriting.configuration.access import ROOT
from underwriting.configuration.settings import get_config

SCHEMA_VERSION = get_config()["intake"]["schema_version"]
POLICY_VERSION = get_config()["screening"]["policy_version"]
UI_TO_PROMPT = get_config()["intake"]["field_aliases"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Evidence(Strict):
    page: int = Field(ge=1)
    section: str | None
    quote: str = Field(min_length=1)
    attribution: str | None


class Loss(Strict):
    loss_date: str | None
    description: str | None
    amount_paid: float | None = Field(ge=0)
    outstanding_amount: float | None = Field(ge=0)
    remedial_action: str | None
    evidence: list[Evidence]


class CompletenessItem(Strict):
    description: str = Field(min_length=1)
    origin: Literal["explicit_in_source", "unfilled_required_field"]
    evidence: list[Evidence]


class Check(Strict):
    check_id: Literal["SC01", "SC02", "SC03", "SC04", "SC05", "SC06"]
    result: Literal["pass", "fail", "unknown"]
    reason: str = Field(min_length=1)
    supporting_field_ids: list[str]
    evidence: list[Evidence]


class ReviewFlag(Strict):
    issue: str = Field(min_length=1)
    information_required: str = Field(min_length=1)
    evidence: list[Evidence]


def fields_definition():
    groups = get_config()["intake"]["field_groups"]
    return [field for group in groups for field in group["fields"]]


NUMERIC = {"annual_turnover", "employee_count", "site_count", "total_floor_area",
           "buildings_reinstatement_value", "general_contents_value", "computer_equipment_value",
           "current_stock_value", "maximum_stock_value", "requested_bi_amount", "maximum_indemnity_period"}
MONEY = {name for name in NUMERIC if name.endswith("_value")} | {"annual_turnover", "requested_bi_amount"}
INTEGER = {"employee_count", "site_count", "year_established"}
COMPLETENESS = {"missing_information", "conflicting_information", "referenced_documents_not_supplied"}


def output_type():
    variants = []
    for field in fields_definition():
        identifier = UI_TO_PROMPT.get(field["id"], field["id"])
        value_type = str
        if identifier in NUMERIC:
            value_type = int if identifier in INTEGER else float
        if identifier == "year_established":
            value_type = int
        if identifier == "previous_losses":
            value_type = list[Loss]
        if identifier in COMPLETENESS:
            value_type = list[CompletenessItem]
        if identifier in {"requested_insurance_sections", "additional_cover_requests"}:
            value_type = str | list[str]
        variants.append(create_model(
            "Field_" + identifier, __base__=Strict,
            field_id=(Literal[identifier], ...), value=(value_type | None, ...),
            unit=(str | None, ...),
            status=(Literal["found", "not_provided", "conflicting", "not_applicable"], ...),
            evidence=(list[Evidence], ...), note=(str | None, ...)))
    return create_model("SubmissionIntake", __base__=Strict,
                        basic_information=(list[Union[tuple(variants)]], ...),
                        screening_checks=(list[Check], ...),
                        non_blocking_review_flags=(list[ReviewFlag], ...))


IntakeOutput = output_type()


def normalise_quote(value):
    # NFKC, soft-hyphen removal and whitespace collapse only. No fuzzy or case matching.
    return " ".join(unicodedata.normalize("NFKC", value).replace("\u00ad", "").split())


def valid_partial_date(value):
    if re.fullmatch(r"\d{4}", value):
        return 1 <= int(value) <= 9999
    if re.fullmatch(r"\d{4}-\d{2}", value):
        calendar.monthrange(int(value[:4]), int(value[5:]))
        return True
    date.fromisoformat(value)
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value))


def validate_output(data, pages, case_id):
    parsed = IntakeOutput.model_validate(data)
    result = parsed.model_dump()
    expected = {UI_TO_PROMPT.get(f["id"], f["id"]) for f in fields_definition()}
    identifiers = [field["field_id"] for field in result["basic_information"]]
    if set(identifiers) != expected or len(identifiers) != len(expected):
        raise ValueError("Every predefined field must occur exactly once.")
    check_ids = [check["check_id"] for check in result["screening_checks"]]
    if set(check_ids) != {f"SC{i:02}" for i in range(1, 7)} or len(check_ids) != 6:
        raise ValueError("SC01–SC06 must occur exactly once.")
    issues = []
    def evidence(items, label):
        for item in items:
            page = item["page"]
            if page > len(pages) or not normalise_quote(item["quote"]) or normalise_quote(item["quote"]) not in normalise_quote(pages[page - 1]):
                issues.append(f"{label}: unsupported quotation or page reference; review required.")
    def visit(value, label):
        if isinstance(value, dict):
            if "evidence" in value:
                evidence(value["evidence"], label)
            for key, nested in value.items():
                if key != "evidence":
                    visit(nested, label)
        elif isinstance(value, list):
            for nested in value:
                visit(nested, label)
    for field in result["basic_information"]:
        identifier, value, status = field["field_id"], field["value"], field["status"]
        visit(field, identifier)
        if status in {"not_provided", "conflicting"} and value is not None:
            raise ValueError(f"{identifier}: missing/conflicting values must be null.")
        if status == "found" and (value is None or value == ""):
            raise ValueError(f"{identifier}: found requires a value.")
        if status in {"found", "not_applicable"} and not field["evidence"]:
            # Completeness gaps inferred from unfilled required fields have their own provenance.
            nested_loss_evidence = identifier == "previous_losses" and isinstance(value, list) and bool(value) and all(loss["evidence"] for loss in value)
            if not (identifier in COMPLETENESS and isinstance(value, list)) and not nested_loss_evidence:
                issues.append(f"{identifier}: supporting evidence required.")
        if status == "conflicting" and (not field["note"] or len(field["evidence"]) < 2):
            issues.append(f"{identifier}: competing statements need a note and both sources.")
        if identifier in NUMERIC and value is not None and value < 0:
            raise ValueError(f"{identifier}: negative quantity.")
        if value is not None and identifier in MONEY and not re.fullmatch(r"[A-Z]{3}", field["unit"] or ""):
            raise ValueError(f"{identifier}: ISO currency unit required.")
        if value is not None and identifier == "total_floor_area" and field["unit"] not in {"m²", "m2", "sq ft", "ft²"}:
            raise ValueError("total_floor_area: recognised area unit required.")
        if value is not None and identifier == "maximum_indemnity_period" and field["unit"] not in {"months", "years"}:
            raise ValueError("maximum_indemnity_period: months or years required.")
        if identifier in {"proposed_insurance_start_date", "proposed_insurance_end_date", "submission_date"} and value:
            if not valid_partial_date(value):
                raise ValueError(f"{identifier}: invalid ISO date.")
        if identifier == "previous_losses" and value is not None:
            if any(loss["amount_paid"] is not None or loss["outstanding_amount"] is not None for loss in value) and not re.fullmatch(r"[A-Z]{3}", field["unit"] or ""):
                issues.append("previous_losses: currency unit is missing or invalid; monetary records require review. Original amounts and unit are unchanged.")
            for loss in value:
                if loss["loss_date"]:
                    try:
                        valid_date = valid_partial_date(loss["loss_date"])
                    except (ValueError, OverflowError):
                        valid_date = False
                    if not valid_date:
                        issues.append("previous_losses: loss date is not ISO formatted; review required. Original date text is unchanged.")
                if not loss["evidence"]:
                    issues.append("previous_losses: each record requires source evidence.")
        if identifier in COMPLETENESS and value is not None:
            for item in value:
                if item["origin"] == "explicit_in_source" and not item["evidence"]:
                    issues.append(f"{identifier}: source-identified gap requires evidence.")
    fields = {field["field_id"]: field for field in result["basic_information"]}
    relevant = {"SC01": ["company_name", "site_address"], "SC02": ["country_or_territory"],
                "SC03": ["business_description"], "SC04": ["site_count", "occupancy"],
                "SC05": ["requested_insurance_sections"]}
    for check in result["screening_checks"]:
        visit(check, check["check_id"])
        if not set(check["supporting_field_ids"]) <= expected:
            raise ValueError("Unknown supporting field ID.")
        if check["result"] != "unknown" and not check["evidence"]:
            issues.append(f"{check['check_id']}: pass/fail requires source evidence.")
        if check["result"] == "pass":
            required = relevant.get(check["check_id"], [])
            if any(fields[name]["status"] in {"not_provided", "conflicting"} for name in required):
                issues.append(f"{check['check_id']}: pass is inconsistent with essential missing/conflicting fields.")
    checks = {check["check_id"]: check for check in result["screening_checks"]}
    if checks["SC06"]["result"] == "pass" and any(checks[f"SC{i:02}"]["result"] == "unknown" for i in range(1, 6)):
        issues.append("SC06: quality pass conflicts with unknown essential checks.")
    for flag in result["non_blocking_review_flags"]:
        visit(flag, "Review flag")
    # The approved prompt exception is populated only for this exact case; it never supplies identifiers.
    return result, sorted(set(issues))


def derive_outcome(checks, issues=()):
    values = {check["check_id"]: check["result"] for check in checks}
    policy = get_config()["screening"]
    if any(values[key] == "fail" and not any(key in issue for issue in issues)
           for key in policy["scope_failure_checks"]):
        return "Outside demo scope"
    if issues or "unknown" in values.values() or any(values[key] == "fail" for key in policy["essential_failure_checks"]):
        return "Pause for clarification"
    return "Proceed to assessment"


def schema_hash():
    return hashlib.sha256(json.dumps(IntakeOutput.model_json_schema(), sort_keys=True).encode()).hexdigest()
