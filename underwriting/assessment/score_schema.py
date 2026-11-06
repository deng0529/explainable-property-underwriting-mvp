"""Concern-score contract and deterministic Low/High membership mapping."""
from decimal import Decimal
from pydantic import Field, StrictBool
from underwriting.intake.schema import Strict
from underwriting.assessment.evidence_schema import attribute_definitions

METHOD_ID="concern_score_v1"
MAPPING_VERSION="linear-low-high-membership-v1"
SCHEMA_VERSION="attribute-concern-scores-v1"

class ScoreAssessment(Strict):
    attribute_id: str = Field(min_length=1)
    assessable: StrictBool
    concern_score: float | None = Field(ge=1,le=5,strict=True,allow_inf_nan=False)
    explanation: str = Field(min_length=1)
    supporting_evidence_ids: list[str]
    missing_information: list[str]
    contradictions: list[str]

class ScoreOutput(Strict):
    assessments: list[ScoreAssessment]

def score_to_beliefs(assessable,score):
    if not isinstance(assessable,bool):
        raise ValueError("Assessability must be boolean.")
    if not assessable:
        if score is not None:
            raise ValueError("Unassessable evidence requires a null concern score.")
        return {"low":0.0,"high":0.0,"unknown":1.0}
    if isinstance(score,bool) or not isinstance(score,(int,float,Decimal)):
        raise ValueError("Assessable evidence requires a finite numeric score in [1,5].")
    value=Decimal(str(score))
    if not value.is_finite() or not 1<=value<=5:
        raise ValueError("Assessable evidence requires a finite numeric score in [1,5].")
    return {"low":float((5-value)/4),"high":float((value-1)/4),"unknown":0.0}

def score_output_schema(definitions=None):
    definitions=definitions if definitions is not None else attribute_definitions()
    schema=ScoreOutput.model_json_schema()
    schema["$defs"]["ScoreAssessment"]["properties"]["attribute_id"]["enum"]=[a["id"] for a in definitions if a.get("enabled",True)]
    return schema

def validate_scores(data,reviewed,definitions=None):
    result=ScoreOutput.model_validate(data).model_dump()
    definitions=definitions if definitions is not None else attribute_definitions()
    expected={a["id"] for a in definitions if a.get("enabled",True)}
    evidence={a["attribute_id"]:a for a in reviewed["attributes"]}
    items=result["assessments"]
    if len(items)!=len(expected) or {a["attribute_id"] for a in items}!=expected or set(evidence)!=expected:
        raise ValueError("Scores must contain every requested attribute exactly once.")
    for item in items:
        item["beliefs"]=score_to_beliefs(item["assessable"],item["concern_score"])
        allowed={a["evidence_id"] for a in evidence[item["attribute_id"]]["evidence_items"]}
        refs=item["supporting_evidence_ids"]
        if len(refs)!=len(set(refs)) or not set(refs)<=allowed:
            raise ValueError("Unsupported, duplicate or cross-attribute score evidence IDs.")
        if item["assessable"] and not refs:
            raise ValueError("An assessable score requires supporting evidence IDs.")
    return result

def response_only(result):
    return {"assessments":[{k:v for k,v in a.items() if k!="beliefs"} for a in result["assessments"]]}
