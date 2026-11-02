"""Content identity for reuse; audit/run identity remains in the original snapshot."""
from decimal import Decimal
from underwriting.assessment.belief_service import content_hash

VERSION='decision-content-v1'
AUDIT_KEYS={'run_id','event_id','evidence_run_id','belief_run_id','er_run_id','created_at','finished_at','started_at',
            'reviewed_hash','input_belief_hash','er_result_hash','source_version','reviewer','reviewer_name','reviewer_context'}


def canonical_content(value):
    """Normalize formatting/order, never round beliefs or infer equivalent prose."""
    if isinstance(value,dict):
        return {key:canonical_content(child) for key,child in value.items() if key not in AUDIT_KEYS}
    if isinstance(value,list):
        result=[canonical_content(child) for child in value]
        for key in ('attribute_id','field_id','check_id','evidence_id','gap_id','rule_id','conflict_id'):
            if result and all(isinstance(child,dict) and key in child for child in result):
                return sorted(result,key=lambda child:str(child[key]))
        return result
    if isinstance(value,str):
        return ' '.join(value.split())
    if isinstance(value,(int,float,Decimal)) and not isinstance(value,bool):
        number=Decimal(str(value))
        if not number.is_finite():
            raise ValueError('Non-finite values cannot identify reusable Decision content.')
        return {'numeric_value':format(number.normalize(),'f')}
    return value


def latest_material_reviews(events, fields):
    latest={}
    for event in events:
        latest[event['attribute_id']]={field:event.get(field) for field in fields}
    return latest


def review_content(snapshot):
    submission=snapshot['submission']
    screening=submission.get('screening_resolution',{})
    override=screening.get('override')
    history=submission.get('human_reviews',[])
    latest=history[0] if history else {}
    # Repeated confirmations/reviewer labels are audit events, not new company facts.
    material_submission_review={key:latest.get(key) for key in ('basic_information_notes','reason','recipient') if latest.get(key)}
    notes={key:value for key,value in latest.get('screening_notes',{}).items() if value and value.strip()}
    if notes:
        material_submission_review['screening_notes']=notes
    evidence=snapshot['evidence'];beliefs=snapshot['beliefs']
    er=snapshot['er'];brb=snapshot['brb']
    return {'identity':{'case_id':snapshot['company']['case_id'],'submission_version':snapshot['source']['submission_version'],
                        'company_name':snapshot['company'].get('company_name'),'document_hash':snapshot['source']['sha256']},
            'submission':{'data':submission['extraction_and_screening'],'resolved_screening':submission['outcome'],
                          'validation_concerns':sorted(submission.get('validation_issues',[])),
                          'override':{key:override.get(key) for key in ('outcome','reason','clarification_needed')} if override else None,
                          'human_review':material_submission_review},
            'evidence':{'original':evidence['ai_original'],'human_reviewed':evidence['human_reviewed']},
            'beliefs':{'proposed':beliefs['ai_proposals'],'confirmed':beliefs['human_confirmed'],
                       'human_reasons':latest_material_reviews(beliefs.get('review_events',[]),('values','reason','confirmed'))},
            'er':{'groups':{identifier:{key:value for key,value in group.items() if key!='diagnostics'} for identifier,group in er['groups'].items()},
                  'engine':er['engine_version'],'model':er['decision_model_snapshot']},
            'brb':{'result':brb['result'],'engine':brb['engine_version'],'model':brb['decision_model_snapshot']}}


def review_content_hash(snapshot):
    return content_hash({'version':VERSION,'content':canonical_content(review_content(snapshot))})


def same_review_content(left,right):
    return review_content_hash(left)==review_content_hash(right)
