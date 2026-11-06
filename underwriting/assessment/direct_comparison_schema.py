"""Improved direct comparison output, isolated from canonical confirmations."""
from pydantic import Field
from underwriting.intake.schema import Strict
from underwriting.assessment.score_schema import Support,UnresolvedInformation,EvidenceReviewFlag,validate_attribute_set,validate_support
from underwriting.assessment.belief_schema import validate_values
from underwriting.assessment.evidence_schema import attribute_definitions
from underwriting.configuration.settings import get_config

METHOD_ID='direct_comparison_v2'
SCHEMA_VERSION='attribute-direct-comparison-v2'

class DirectAssessment(Strict):
    attribute_id:str=Field(min_length=1)
    low:float=Field(ge=0,le=1,strict=True,allow_inf_nan=False)
    high:float=Field(ge=0,le=1,strict=True,allow_inf_nan=False)
    unknown:float=Field(ge=0,le=1,strict=True,allow_inf_nan=False)
    low_support:Support
    high_support:Support
    unresolved_information:list[UnresolvedInformation]
    allocation_explanation:str=Field(min_length=1)
    evidence_review_flags:list[EvidenceReviewFlag]

class DirectOutput(Strict):
    assessments:list[DirectAssessment]

def direct_output_schema(definitions=None,settings=None):
    definitions=definitions if definitions is not None else attribute_definitions()
    schema=DirectOutput.model_json_schema()
    schema['$defs']['DirectAssessment']['properties']['attribute_id']['enum']=[a['id'] for a in definitions if a.get('enabled',True)]
    return schema

def validate_direct(data,reviewed,definitions=None,settings=None,pages=None):
    definitions=definitions if definitions is not None else attribute_definitions()
    settings=settings or dict(get_config()['belief_comparison'],belief_granularity=get_config()['belief_assessment']['increment'])
    result=DirectOutput.model_validate(data).model_dump();evidence=validate_attribute_set(result['assessments'],reviewed,definitions)
    increment=settings.get('belief_granularity')
    for item in result['assessments']:
        validate_values(item,{'sum_tolerance':settings['distribution_tolerance'],'increment':increment},check_increment=increment is not None)
        validate_support(item,evidence[item['attribute_id']],pages,item)
    return result
