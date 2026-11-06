"""Independent saved-first display-only concern scores; never an ER/BRB input."""
import copy
import hashlib
import json
import re
import threading
import uuid
from underwriting.paths import PROJECT_ROOT
from underwriting.configuration.settings import get_config,configuration_metadata
from underwriting.configuration.access import require_owner
from underwriting.assessment.evidence_schema import attribute_definitions,attribute_hash
from underwriting.assessment.belief_service import content_hash,current_input,reviewed_snapshot_hash,propose_beliefs
from underwriting.assessment.score_schema import METHOD_ID,MAPPING_VERSION,SCHEMA_VERSION,score_output_schema,validate_scores,response_only
from underwriting.intake.service import now,structured_request,request_failure
from underwriting.reliability.inputs import generation_allowed,check_request
from underwriting.reliability.recovery import recover_running
from underwriting.persistence.intake import StoreError

ROOT=PROJECT_ROOT
_guard=threading.Lock()
_locks={}

def score_prompt(case,reviewed,definitions=None):
    definition=get_config()['prompts']['attribute_concern_scores']
    values={'attribute_definitions_from_yaml':json.dumps(definitions if definitions is not None else attribute_definitions(),ensure_ascii=False,indent=2),
            'case_id_and_source_version':f"Case ID: {case['case_id']}\nSource version: {case['submission_version']}",
            'reviewed_attribute_evidence_json':json.dumps(reviewed,ensure_ascii=False,indent=2)}
    prompt=re.sub(r'{{(\w+)}}',lambda match:values[match[1]],definition['template'])
    return prompt,{'version':definition['version'],'sha256':hashlib.sha256(definition['template'].encode()).hexdigest(),'effective_sha256':hashlib.sha256(prompt.encode()).hexdigest()}

def score_identity(settings,reviewed,definitions=None):
    definition=get_config()['prompts']['attribute_concern_scores']
    return content_hash({'method_id':METHOD_ID,'mapping_version':MAPPING_VERSION,'schema_version':SCHEMA_VERSION,
        'prompt_version':definition['version'],'prompt_hash':hashlib.sha256(definition['template'].encode()).hexdigest(),
        'model':settings.model,'definitions':definitions if definitions is not None else attribute_definitions(),
        'reviewed_snapshot_hash':reviewed_snapshot_hash(reviewed)})

def retain_score(run):
    from underwriting.reliability.diagnostics import attach
    attach(run,'Score comparison')
    directory=ROOT/'.score_pending';directory.mkdir(exist_ok=True)
    path=directory/(run['run_id']+'.json');temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(run,ensure_ascii=False,allow_nan=False),encoding='utf-8');temporary.replace(path)

def pending_scores(case,digest):
    directory=ROOT/'.score_pending'
    if not directory.exists():return []
    values=[json.loads(p.read_text(encoding='utf-8')) for p in directory.glob('*.json')]
    return [v for v in values if (v['case_id'],v['source_version'],v['source_hash'])==(case['case_id'],case['submission_version'],digest)]

def retry_score_save(settings,store,run):
    require_owner(settings)
    if run['status']=='running':
        def validate_retained(record):
            record.update(result=validate_scores(json.loads(record['response_text']),record['reviewed_evidence'],record['attribute_configuration']),status='completed')
        run=recover_running(run,settings,validate_retained,'Score comparison',lock=_locks.get(run['cache_key']))
        retain_score(run)
    if run['status'] not in {'completed','failed','stale'}:
        raise ValueError('The score request is still running; no automatic new request is allowed.')
    if run['result']:
        expected=validate_scores(response_only(run['result']),run['reviewed_evidence'],run['attribute_configuration'])
        if expected!=run['result']:raise ValueError('Retained score mapping failed integrity validation.')
    run.pop('persistence_error',None)
    try:
        store.initialise_scores();store.save_scores(run)
    except Exception:
        run['persistence_error']=True;retain_score(run);raise
    (ROOT/'.score_pending'/(run['run_id']+'.json')).unlink(missing_ok=True)
    return run

def saved_score(settings,case,store,context):
    digest,_,reviewed,review_hash,_=context
    key=score_identity(settings,reviewed)
    saved=store.latest_scores(case,digest,review_hash,key)
    if saved:
        result=validate_scores(response_only(saved['result']),reviewed,saved['attribute_configuration'])
        if result!=saved['result'] or saved['method_id']!=METHOD_ID or saved['reviewed_snapshot_hash']!=reviewed_snapshot_hash(reviewed):
            raise ValueError('Saved score result failed method/snapshot/mapping validation.')
    return saved,key

def propose_scores(settings,case,store,context,request=None,progress=None,regenerate=False,confirmed=False,expected_run=None):
    report=progress or (lambda stage,detail:None)
    context=copy.deepcopy(context)
    digest,evidence,reviewed,review_hash,versions=context
    saved,key=saved_score(settings,case,store,context)
    if saved and not regenerate:return dict(saved,result_source='database')
    with _guard:lock=_locks.setdefault(key,threading.Lock())
    if not lock.acquire(blocking=False):raise ValueError('A score comparison for these inputs is already running.')
    try:
        # Recheck under the lock: another request may have finished just now.
        saved,_=saved_score(settings,case,store,context)
        if saved and not regenerate:return dict(saved,result_source='database')
        if settings.service_mode=='turso_readonly':
            older=store.latest_scores(case,digest,review_hash)
            if older:return dict(older,result_source='database',comparison_stale=True)
        require_owner(settings)
        from underwriting.reliability.prerequisites import assessment_entry
        assessment_entry(case,store,digest)
        current=current_input(case,store)
        if current[0]!=digest or current[3]!=review_hash or reviewed_snapshot_hash(current[2])!=reviewed_snapshot_hash(reviewed):
            raise ValueError('Reviewed evidence changed; reload both proposed-belief methods before generation.')
        previous=store.latest_scores(case,digest,review_hash,key,successful=False)
        if regenerate and (not confirmed or (previous['run_id'] if previous else None)!=expected_run):
            raise PermissionError('Confirm score-only regeneration against the current attempt first.')
        if previous and not regenerate:return dict(previous,result_source='database')
        if pending_scores(case,digest):raise ValueError('A score response is retained. Retry save without another GPT call.')
        generation_allowed(settings,request is not None)
        if settings.missing():raise ValueError('Missing configuration: '+', '.join(settings.missing()))
        definitions=copy.deepcopy(attribute_definitions())
        prompt,metadata=score_prompt(case,reviewed,definitions)
        size=check_request('',prompt,settings.model)
        identifier=uuid.uuid4().hex
        store.initialise_scores()
        generation=content_hash([METHOD_ID,case['case_id'],case['submission_version'],digest,key,previous['run_id'] if previous else None])
        if not store.claim(generation,identifier,None):raise ValueError('This score request was already reserved; no duplicate GPT request.')
        run={'run_id':identifier,'case_id':case['case_id'],'source_version':case['submission_version'],'source_hash':digest,
             'method_id':METHOD_ID,'mapping_version':MAPPING_VERSION,'schema_version':SCHEMA_VERSION,'cache_key':key,
             'reviewed_hash':review_hash,'reviewed_snapshot_hash':reviewed_snapshot_hash(reviewed),'reviewed_evidence':reviewed,
             'evidence_run_id':evidence['run_id'],'evidence_review_versions':versions,'attribute_configuration':definitions,
             'attribute_configuration_hash':attribute_hash(),'prompt':metadata,'configuration':configuration_metadata(),'model':settings.model,
             'started_at':now(),'finished_at':None,'timeout_seconds':settings.timeout_seconds,'input_size':size,
             'status':'running','raw_response':None,'response_text':None,'usage':None,'result':None,'validation_errors':[]}
        retain_score(run)
        try:
            report('GPT score method','One separate concern-score request for all enabled attributes.')
            raw,text=(request or structured_request)(settings,'',prompt,score_output_schema(definitions),'attribute_concern_scores')
            run.update(raw_response=raw,response_text=text,usage=raw.get('usage'),model=raw.get('model',settings.model))
            retain_score(run)
            if raw.get('status','completed')!='completed' or not text:raise ValueError('Incomplete score response or refusal.')
            run['result']=validate_scores(json.loads(text),reviewed,definitions)
            after=current_input(case,store)
            run['status']='completed' if after[0]==digest and after[3]==review_hash and reviewed_snapshot_hash(after[2])==reviewed_snapshot_hash(reviewed) else 'stale'
            if run['status']=='stale':run['validation_errors']=['Reviewed evidence changed during score generation.']
        except Exception as error:
            run.update(status='failed',validation_errors=[str(error)[:4000]] if run['raw_response'] and isinstance(error,ValueError) else [request_failure(error,settings.timeout_seconds)['message']])
        run['finished_at']=now();retain_score(run)
        report('Save score method','Saving the separate score response and deterministic membership distribution.')
        result=retry_score_save(settings,store,run)
        return dict(result,result_source='generated')
    finally:lock.release()

def compare_beliefs(settings,case,store,request_a=None,request_b=None,progress=None,context=None):
    """Freeze once, preflight both stores, and independently reuse/generate methods."""
    frozen=copy.deepcopy(context if context is not None else current_input(case,store))
    digest,_,reviewed,review_hash,_=frozen
    # A database failure in either lookup blocks new generation, never becomes a miss.
    store.latest_beliefs(case,digest,review_hash)
    saved_score(settings,case,store,frozen)
    result={'direct':None,'score':None,'direct_error':None,'score_error':None,'snapshot_hash':reviewed_snapshot_hash(reviewed)}
    try:
        result['direct']=propose_beliefs(settings,case,store,request=request_a,progress=progress,reviewed_input=frozen)
    except (PermissionError,ValueError) as error:result['direct_error']=str(error)
    try:
        result['score']=propose_scores(settings,case,store,frozen,request=request_b,progress=progress)
    except (PermissionError,ValueError,StoreError) as error:result['score_error']=str(error)
    return result
