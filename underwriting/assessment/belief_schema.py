"""Decimal-safe belief validation. No ER, weights or BRB inference."""
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import Field

from underwriting.configuration.settings import get_config
from underwriting.intake.schema import Strict
from underwriting.assessment.evidence_schema import attribute_definitions

SCHEMA_VERSION = "attribute-beliefs-v1"


class Support(Strict):
    explanation: str = Field(min_length=1)
    evidence_ids: list[str]


class UnknownBasis(Support):
    gap_ids: list[str]


class ProtectionAspect(Strict):
    aspect: str = Field(min_length=1)
    finding: str = Field(min_length=1)
    evidence_ids: list[str]


class Assessment(Strict):
    attribute_id: str = Field(min_length=1)
    low: float = Field(ge=0, le=1)
    high: float = Field(ge=0, le=1)
    unknown: float = Field(ge=0, le=1)
    assessment_summary: str = Field(min_length=1)
    low_support: Support
    high_support: Support
    unknown_basis: UnknownBasis
    important_concerns: list[str]
    clarification_questions: list[str]
    protection_aspect_findings: list[ProtectionAspect]


class BeliefOutput(Strict):
    assessments: list[Assessment]


def belief_output_schema():
    schema = BeliefOutput.model_json_schema()
    schema["$defs"]["Assessment"]["properties"]["attribute_id"]["enum"] = [attribute["id"] for attribute in attribute_definitions()]
    aspects = sorted({aspect for attribute in attribute_definitions() for aspect in attribute.get("belief_aspects", [])})
    if aspects:
        schema["$defs"]["ProtectionAspect"]["properties"]["aspect"]["enum"] = aspects
    return schema


def validate_values(values, settings=None, check_increment=True):
    settings = settings or get_config()["belief_assessment"]
    try:
        if any(isinstance(values[name], bool) or not isinstance(values[name], (int, float, Decimal)) for name in ("low", "high", "unknown")):
            raise ValueError("Beliefs must be numeric values.")
        decimals = [Decimal(str(values[name])) for name in ("low", "high", "unknown")]
    except (InvalidOperation, TypeError, KeyError):
        raise ValueError("Low, High and Unknown must be valid numeric values.") from None
    if any(not value.is_finite() or value < 0 or value > 1 for value in decimals):
        raise ValueError("Low, High and Unknown must be finite numbers in [0,1].")
    if abs(sum(decimals) - Decimal(1)) > Decimal(str(settings["sum_tolerance"])):
        raise ValueError("Low + High + Unknown must equal 1 within the configured tolerance.")
    if check_increment and any(value % Decimal(str(settings["increment"])) != 0 for value in decimals):
        raise ValueError("Beliefs must follow the configured increment; values were not rounded or normalised.")
    return {name: float(value) for name, value in zip(("low", "high", "unknown"), decimals)}


def validate_beliefs(data, reviewed, settings=None, definitions=None):
    parsed = BeliefOutput.model_validate(data).model_dump()
    assessments = parsed["assessments"]
    evidence = {item["attribute_id"]: item for item in reviewed["attributes"]}
    definitions = definitions if definitions is not None else attribute_definitions()
    expected_ids = {attribute["id"] for attribute in definitions if attribute.get("enabled", True)}
    if len(assessments) != len(expected_ids) or {item["attribute_id"] for item in assessments} != expected_ids or set(evidence) != expected_ids:
        raise ValueError("Proposals and reviewed evidence must contain every configured attribute exactly once.")
    aspect_definitions = {attribute["id"]: attribute.get("belief_aspects", []) for attribute in definitions}
    for item in assessments:
        validate_values(item, settings)
        source = evidence[item["attribute_id"]]
        evidence_ids = {row["evidence_id"] for row in source["evidence_items"]}
        gap_ids = {gap["gap_id"] for gap in source["information_gaps"]}
        for name, component in (("low_support", "low"), ("high_support", "high"), ("unknown_basis", "unknown")):
            support = item[name]
            if not set(support["evidence_ids"]) <= evidence_ids:
                raise ValueError(f"{item['attribute_id']}: unsupported or cross-attribute evidence ID.")
            cited_gaps = support.get("gap_ids", [])
            if not set(cited_gaps) <= gap_ids:
                raise ValueError(f"{item['attribute_id']}: unsupported or cross-attribute gap ID.")
            if item[component] == 0 and (support["evidence_ids"] or cited_gaps):
                raise ValueError(f"{item['attribute_id']}: zero belief must not invent supporting references.")
            if item[component] > 0 and not support["evidence_ids"] and not cited_gaps:
                raise ValueError(f"{item['attribute_id']}: nonzero belief needs relevant evidence or gap references.")
        aspects = item["protection_aspect_findings"]
        required_aspects = aspect_definitions[item["attribute_id"]]
        if required_aspects:
            if len(aspects) != len(required_aspects) or {row["aspect"] for row in aspects} != set(required_aspects):
                raise ValueError("Every configured protection aspect must appear exactly once.")
        elif aspects:
            raise ValueError("This attribute has no configured protection aspects.")
        if any(not set(row["evidence_ids"]) <= evidence_ids for row in aspects):
            raise ValueError("Protection aspect cites an invalid evidence ID.")
    return parsed
