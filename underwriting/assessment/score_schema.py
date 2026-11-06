"""Explicit score plus unresolved belief, with shared evidence support contracts."""
from decimal import Decimal
from typing import Literal
from pydantic import Field,StrictBool
from underwriting.intake.schema import Strict,normalise_quote
from underwriting.configuration.settings import get_config
from underwriting.assessment.evidence_schema import attribute_definitions
from underwriting.assessment.belief_schema import validate_values

METHOD_ID='concern_score_unknown_v2'
MAPPING_VERSION='linear-membership-explicit-unknown-v2'
SCHEMA_VERSION='attribute-score-unknown-v2'

class Support(Strict):
    evidence_ids:list[str]
    explanation:str=Field(min_length=1)

class UnresolvedInformation(Strict):
    issue:str=Field(min_length=1)
    materiality:Literal['material','minor','irrelevant']
    potential_assessment_impact:str=Field(min_length=1)

class EvidenceReviewFlag(Strict):
    issue:str=Field(min_length=1)
    materiality:Literal['material','minor','irrelevant']
    evidence_ids:list[str]
    page:int|None=Field(ge=1,strict=True)
    section:str|None
    exact_quote:str|None

class ScoreAssessment(Strict):
    attribute_id:str=Field(min_length=1)
    assessable:StrictBool
    concern_score:float|None=Field(strict=True,allow_inf_nan=False)
    unknown:float=Field(ge=0,le=1,strict=True,allow_inf_nan=False)
    low_support:Support
    high_support:Support
    unresolved_information:list[UnresolvedInformation]
    score_explanation:str=Field(min_length=1)
    unknown_explanation:str=Field(min_length=1)
    evidence_review_flags:list[EvidenceReviewFlag]

class ScoreOutput(Strict):
    assessments:list[ScoreAssessment]

def method_settings():
    return get_config()['belief_comparison']

def finite_decimal(value,name):
    if isinstance(value,bool) or not isinstance(value,(int,float,Decimal)):
        raise ValueError(name+' must be a finite numeric value.')
    result=Decimal(str(value))
    if not result.is_finite():raise ValueError(name+' must be finite.')
    return result

def score_to_beliefs(assessable,score,unknown,settings=None):
    settings=settings or method_settings()
    if not isinstance(assessable,bool):raise ValueError('Assessability must be boolean.')
    u=finite_decimal(unknown,'Unknown')
    if not 0<=u<=1:raise ValueError('Unknown must be in [0,1].')
    if not assessable:
        if score is not None or u!=1:raise ValueError('Unassessable requires null score and Unknown=1.')
        return {'low':0.0,'high':0.0,'unknown':1.0}
    if u==1:raise ValueError('An assessable score requires Unknown below 1.')
    s=finite_decimal(score,'Concern score')
    minimum=finite_decimal(settings['score_scale']['minimum'],'Score minimum')
    maximum=finite_decimal(settings['score_scale']['maximum'],'Score maximum')
    if minimum>=maximum or not minimum<=s<=maximum:raise ValueError('Concern score outside configured endpoints.')
    values={'low':float((1-u)*(maximum-s)/(maximum-minimum)),
            'high':float((1-u)*(s-minimum)/(maximum-minimum)),'unknown':float(u)}
    validate_values(values,{'sum_tolerance':settings['distribution_tolerance']},check_increment=False)
    return values

def validate_attribute_set(items,reviewed,definitions):
    expected={a['id'] for a in definitions if a.get('enabled',True)}
    evidence={a['attribute_id']:a for a in reviewed['attributes']}
    if len(items)!=len(expected) or {a['attribute_id'] for a in items}!=expected or set(evidence)!=expected or len(reviewed['attributes'])!=len(expected):
        raise ValueError('Comparison must contain every enabled attribute exactly once.')
    return evidence

def validate_support(item,source,pages,beliefs):
    allowed={e['evidence_id'] for e in source['evidence_items']}
    def refs_valid(refs):
        if len(refs)!=len(set(refs)) or not set(refs)<=allowed:
            raise ValueError('Unsupported, duplicate or cross-attribute evidence IDs.')
    for name in ('low','high'):
        refs=item[name+'_support']['evidence_ids'];refs_valid(refs)
        if beliefs[name]>0 and not refs:raise ValueError('Assigned '+name+' support requires reviewed evidence IDs.')
    unresolved=item['unresolved_information'];flags=item['evidence_review_flags']
    if beliefs['unknown']==0 and (any(i['materiality']=='material' for i in unresolved) or any(i['materiality']=='material' for i in flags)):
        raise ValueError('Unknown=0 conflicts with material unresolved information/review flags.')
    if beliefs['unknown']>0 and not any(i['materiality']!='irrelevant' for i in unresolved+flags):
        raise ValueError('Unknown requires relevant unresolved information or review flags.')
    for flag in flags:
        refs_valid(flag['evidence_ids'])
        page,quote=flag['page'],flag['exact_quote']
        if page is None:
            if quote is not None or not flag['evidence_ids']:
                raise ValueError('Unreviewed findings require a source page and exact quotation.')
        elif not pages or page>len(pages) or not quote or not normalise_quote(quote) or normalise_quote(quote) not in normalise_quote(pages[page-1]):
            raise ValueError('Evidence-review flag has an unsupported source page/quotation.')

def score_output_schema(definitions=None,settings=None):
    definitions=definitions if definitions is not None else attribute_definitions();settings=settings or method_settings()
    schema=ScoreOutput.model_json_schema()
    props=schema['$defs']['ScoreAssessment']['properties']
    props['attribute_id']['enum']=[a['id'] for a in definitions if a.get('enabled',True)]
    for variant in props['concern_score']['anyOf']:
        if variant.get('type')=='number':variant.update(minimum=settings['score_scale']['minimum'],maximum=settings['score_scale']['maximum'])
    return schema

def validate_scores(data,reviewed,definitions=None,settings=None,pages=None):
    definitions=definitions if definitions is not None else attribute_definitions();settings=settings or method_settings()
    result=ScoreOutput.model_validate(data).model_dump();evidence=validate_attribute_set(result['assessments'],reviewed,definitions)
    for item in result['assessments']:
        item['beliefs']=score_to_beliefs(item['assessable'],item['concern_score'],item['unknown'],settings)
        validate_support(item,evidence[item['attribute_id']],pages,item['beliefs'])
    return result

def response_only(result):
    return {'assessments':[{k:v for k,v in a.items() if k!='beliefs'} for a in result['assessments']]}
