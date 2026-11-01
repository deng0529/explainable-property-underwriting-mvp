"""Attribute evidence schema; no beliefs, grades or calculations."""
import hashlib
import json
from typing import Literal

from pydantic import Field

from underwriting.configuration.decision_model import attributes as configured_attributes
from underwriting.intake.schema import Strict, normalise_quote

EVIDENCE_SCHEMA_VERSION = "attribute-evidence-v1"
TEXT_EXTRACTION_VERSION = "pymupdf-page-text-v1"


class EvidenceItem(Strict):
    evidence_id: str = Field(min_length=1)
    page: int = Field(ge=1)
    section: str | None
    exact_quote: str = Field(min_length=1)
    source_attribution: str | None
    extracted_fact: str = Field(min_length=1)
    aspect: str = Field(min_length=1)
    implication: Literal["favourable", "adverse", "contextual"]
    relevance_explanation: str = Field(min_length=1)


class Gap(Strict):
    gap_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    basis: Literal["explicit_in_source", "not_found_in_supplied_material"]
    related_evidence_ids: list[str]


class Contradiction(Strict):
    description: str = Field(min_length=1)
    related_evidence_ids: list[str] = Field(min_length=1)


class Attribute(Strict):
    attribute_id: str = Field(min_length=1)
    evidence_items: list[EvidenceItem]
    information_gaps: list[Gap]
    contradictions: list[Contradiction]
    ai_summary: str = Field(min_length=1)


class EvidenceOutput(Strict):
    attributes: list[Attribute]


def attribute_definitions():
    return configured_attributes()


def evidence_output_schema():
    schema = EvidenceOutput.model_json_schema()
    schema["$defs"]["Attribute"]["properties"]["attribute_id"]["enum"] = [attribute["id"] for attribute in attribute_definitions()]
    return schema


def attribute_hash():
    return hashlib.sha256(json.dumps(attribute_definitions(), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def validate_evidence(data, pages, definitions=None):
    parsed = EvidenceOutput.model_validate(data).model_dump()
    attributes = parsed["attributes"]
    expected = definitions if definitions is not None else attribute_definitions()
    expected_ids = {attribute["id"] for attribute in expected if attribute.get("enabled", True)}
    if len(attributes) != len(expected_ids) or {a["attribute_id"] for a in attributes} != expected_ids:
        raise ValueError("Every configured attribute must appear exactly once: " + ", ".join(sorted(expected_ids)))
    identifiers = [item["evidence_id"] for a in attributes for item in a["evidence_items"]]
    gaps = [gap["gap_id"] for a in attributes for gap in a["information_gaps"]]
    if len(set(identifiers + gaps)) != len(identifiers + gaps):
        raise ValueError("Evidence and gap IDs must be globally unique.")
    aspects_by_id = {attribute["id"]: attribute.get("aspects", []) for attribute in expected}
    errors = []
    for attribute in attributes:
        own = {item["evidence_id"] for item in attribute["evidence_items"]}
        if not own and not attribute["information_gaps"]:
            errors.append(f"{attribute['attribute_id']}: no evidence found requires an information gap.")
        for item in attribute["evidence_items"]:
            page = item["page"]
            quote = normalise_quote(item["exact_quote"])
            if page > len(pages) or not quote or quote not in normalise_quote(pages[page - 1]):
                errors.append(f"{item['evidence_id']}: quotation does not match cited page {page}.")
            aspects = aspects_by_id[attribute["attribute_id"]]
            if aspects and item["aspect"] not in aspects:
                errors.append(f"{item['evidence_id']}: aspect must match the attribute's configured aspects.")
        for issue in attribute["information_gaps"] + attribute["contradictions"]:
            if not set(issue["related_evidence_ids"]) <= own:
                errors.append(f"{attribute['attribute_id']}: related evidence IDs must exist within this attribute.")
    if errors:
        raise ValueError("\n".join(errors))
    return parsed
