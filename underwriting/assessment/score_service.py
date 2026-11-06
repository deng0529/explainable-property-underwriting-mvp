"""Two independently saved, display-only comparison methods; canonical beliefs untouched."""
import copy
import hashlib
import json
import re
import threading
import uuid
from underwriting.paths import PROJECT_ROOT
from underwriting.configuration.settings import get_config,configuration_metadata
from underwriting.configuration.access import require_owner
from underwriting.assessment.belief_service import current_input,content_hash,historical_definitions
from underwriting.assessment.belief_schema import validate_beliefs
from underwriting.assessment.comparison_context import freeze_context,snapshot_hash,current_matches,prompt_context,checked_pages
from underwriting.assessment import score_schema,direct_comparison_schema
from underwriting.intake.service import now,structured_request,request_failure
from underwriting.reliability.inputs import generation_allowed,check_request,check_document
from underwriting.reliability.recovery import recover_running
from underwriting.persistence.intake import StoreError

ROOT=PROJECT_ROOT
_guard=threading.Lock()
_locks={}


def spec(method):
    if method=='direct':
        return (direct_comparison_schema,'attribute_direct_comparison','comparison_direct','attribute_direct_comparison')
    if method=='score':return (score_schema,'attribute_concern_scores','scores','attribute_concern_scores')
    raise ValueError('Unknown comparison method.')


def comparison_settings():
    config=get_config()
    return {k:copy.deepcopy(config['belief_comparison'][k]) for k in ('input_version','distribution_tolerance','score_scale')}|{'belief_granularity':config['belief_assessment']['increment']}


def comparison_prompt(case,snapshot,method):
    _,key,_,_=spec(method);definition=get_config()['prompts'][key]
    scale=snapshot['assessment_guidance']['score_scale']
    anchors='\n'.join(f"{a['score']:g} = {a['label']}" for a in scale['anchors'])
    values={'shared_input_context_json':prompt_context(snapshot),'configured_concern_scale':anchors}
    # One substitution only: text from the PDF is never treated as a template.
    prompt=re.sub(r'{{(\w+)}}',lambda match:values[match[1]],definition['template'])
    return prompt,{'version':definition['version'],'sha256':hashlib.sha256(definition['template'].encode()).hexdigest(),
                   'effective_sha256':hashlib.sha256(prompt.encode()).hexdigest()}


def method_identity(settings,snapshot,method):
    schema,key,_,_=spec(method);definition=get_config()['prompts'][key]
    return content_hash({'method_id':schema.METHOD_ID,'schema_version':schema.SCHEMA_VERSION,
        'input_hash':snapshot['input_hash'],'prompt_version':definition['version'],
        'prompt_hash':hashlib.sha256(definition['template'].encode()).hexdigest(),
        'model':settings.model,'generation':dict(settings.generation),'method_settings':comparison_settings(),
        'mapping_version':getattr(schema,'MAPPING_VERSION',None)})


def score_identity(settings,snapshot,definitions=None):
    return method_identity(settings,snapshot,'score')


def validate_snapshot(snapshot):
    if snapshot_hash(snapshot)!=snapshot['input_hash']:
        raise ValueError('Comparison input snapshot failed its integrity check.')
    if {k:v for k,v in snapshot['reviewed_evidence'].items() if k!='evidence_run_id'}!=snapshot['reviewed_material']:
        raise ValueError('Reviewed evidence differs from the frozen comparison material.')
    pages=checked_pages(snapshot['source'],snapshot['case']['source_hash'])
    if snapshot['source']['page_labelled_text']!='\n\n'.join(f'[PAGE {i}]\n{page}' for i,page in enumerate(pages,1)):
        raise ValueError('Page-labelled document text differs from saved pages.')


def validate_result(data,snapshot,method,settings):
    validate_snapshot(snapshot);schema,_,_,_=spec(method)
    validator=direct_comparison_schema.validate_direct if method=='direct' else score_schema.validate_scores
    return validator(data,snapshot['reviewed_evidence'],snapshot['attribute_configuration'],settings,snapshot['source']['pages'])


def pending_directory(method):
    return ROOT/('.comparison_direct_pending' if method=='direct' else '.score_pending')


def retain(run):
    from underwriting.reliability.diagnostics import attach
    attach(run,'Belief comparison')
    directory=pending_directory(run['comparison_method']);directory.mkdir(exist_ok=True)
    path=directory/(run['run_id']+'.json');temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(run,ensure_ascii=False,allow_nan=False),encoding='utf-8');temp.replace(path)


def pending_comparisons(case,digest,method):
    directory=pending_directory(method)
    if not directory.exists():return []
    values=[json.loads(p.read_text(encoding='utf-8')) for p in directory.glob('*.json')]
    return [v for v in values if (v['case_id'],v['source_version'],v['source_hash'])==(case['case_id'],case['submission_version'],digest)]


def pending_scores(case,digest):return pending_comparisons(case,digest,'score')


def retry_comparison_save(settings,store,run):
    require_owner(settings)
    if run.get('comparison_method') not in {'direct','score'}:
        raise ValueError('Historical score response retained in its original format; owner review is required.')
    method=run['comparison_method'];schema,_,suffix,_=spec(method)
    if run['method_id']!=schema.METHOD_ID:raise ValueError('Historical method version requires owner review.')
    snapshot=run['input_snapshot'];validate_snapshot(snapshot)
    def validate_received(record):
        record.update(result=validate_result(json.loads(record['response_text']),snapshot,method,record['method_settings']),status='validated')
    if run['status']=='running':
        run=recover_running(run,settings,validate_received,'Belief comparison',lock=_locks.get(run['scope_key']))
        retain(run)
    try:
        if run['status']=='validated':
            case={'case_id':run['case_id'],'submission_version':run['source_version'],'document_path':run['document_path']}
            run.update(status='completed' if current_matches(case,store,snapshot) else 'stale',validation_errors=[])
        if run['status'] not in {'completed','failed','stale'}:raise ValueError('Comparison request may still be running. No automatic GPT retry.')
        if run['result']:
            raw=score_schema.response_only(run['result']) if method=='score' else run['result']
            if validate_result(raw,snapshot,method,run['method_settings'])!=run['result']:
                raise ValueError('Saved comparison values failed validation.')
        run.pop('persistence_error',None)
        getattr(store,'initialise_'+suffix)();getattr(store,'save_'+suffix)(run)
    except Exception:
        run['persistence_error']=True;retain(run);raise
    (pending_directory(method)/(run['run_id']+'.json')).unlink(missing_ok=True)
    return run


def retry_score_save(settings,store,run):return retry_comparison_save(settings,store,run)


def latest(store,case,snapshot,method,key=None,successful=True):
    _,_,suffix,_=spec(method)
    return getattr(store,'latest_'+suffix)(case,snapshot['source']['source_hash'],snapshot['upstream']['reviewed_hash'],key,successful=successful)


def saved_method(settings,case,store,snapshot,method):
    schema,_,_,_=spec(method);key=method_identity(settings,snapshot,method)
    run=latest(store,case,snapshot,method,key)
    if run:
        if run.get('method_id')!=schema.METHOD_ID or run.get('input_hash')!=snapshot['input_hash']:
            raise ValueError('Saved comparison method/input version is inconsistent.')
        raw=score_schema.response_only(run['result']) if method=='score' else run['result']
        if validate_result(raw,snapshot,method,run['method_settings'])!=run['result']:
            raise ValueError('Saved comparison values are inconsistent.')
    return run,key


def propose_method(settings,case,store,snapshot,method,request=None,progress=None,regenerate=False,confirmed=False,expected_run=None):
    snapshot=copy.deepcopy(snapshot);report=progress or (lambda stage,detail:None)
    saved,key=saved_method(settings,case,store,snapshot,method)
    if saved and not regenerate:return dict(saved,result_source='database')
    schema,_,suffix,request_name=spec(method)
    scope=content_hash([method,case['case_id'],case['submission_version'],snapshot['source']['source_hash'],key])
    with _guard:lock=_locks.setdefault(scope,threading.Lock())
    if not lock.acquire(blocking=False):raise ValueError('This comparison method is already running.')
    try:
        saved,_=saved_method(settings,case,store,snapshot,method)
        if saved and not regenerate:return dict(saved,result_source='database')
        if settings.service_mode=='turso_readonly':
            older=latest(store,case,snapshot,method)
            if older:return dict(older,result_source='database',comparison_stale=True)
        require_owner(settings)
        from underwriting.reliability.prerequisites import assessment_entry
        assessment_entry(case,store,snapshot['source']['source_hash'])
        if not current_matches(case,store,snapshot):raise ValueError('Shared input changed. Reload both methods before generating.')
        previous=latest(store,case,snapshot,method,key,successful=False)
        if regenerate and (not confirmed or (previous['run_id'] if previous else None)!=expected_run):
            raise PermissionError('Explicitly confirm this method retry against its current attempt.')
        if previous and not regenerate:
            if method=='score' and previous.get('status')=='failed' and previous.get('response_text'):
                # The explicit Load action can recover an already-paid response.
                # A validation error remains a failure, never a generation fallback.
                try:return dict(revalidate_score_response(settings,case,store,previous),result_source='retained_response')
                except ValueError as error:
                    return dict(previous,result_source='database',validation_errors=[str(error)])
            return dict(previous,result_source='database')
        retained=[v for v in pending_comparisons(case,snapshot['source']['source_hash'],method) if v.get('cache_key')==key]
        if retained:return dict(retained[-1],result_source='retained_response',save_pending=True)
        generation_allowed(settings,request is not None)
        if settings.missing():raise ValueError('Missing configuration: '+', '.join(settings.missing()))
        prompt,metadata=comparison_prompt(case,snapshot,method)
        check_document(snapshot['source']['pages'],settings.model)
        size=check_request('',prompt,settings.model)
        identifier=uuid.uuid4().hex
        getattr(store,'initialise_'+suffix)()
        if not store.claim(content_hash([scope,previous['run_id'] if previous else None]),identifier,None):
            raise ValueError('This method request was already reserved. No duplicate GPT call.')
        method_config=comparison_settings()
        output_schema=schema.direct_output_schema(snapshot['attribute_configuration'],method_config) if method=='direct' else schema.score_output_schema(snapshot['attribute_configuration'],method_config)
        run={'run_id':identifier,'case_id':case['case_id'],'source_version':case['submission_version'],'source_hash':snapshot['source']['source_hash'],
            'document_path':case['document_path'],'comparison_method':method,'method_id':schema.METHOD_ID,'schema_version':schema.SCHEMA_VERSION,
            'mapping_version':getattr(schema,'MAPPING_VERSION',None),'scope_key':scope,'cache_key':key,'input_hash':snapshot['input_hash'],
            'input_snapshot':snapshot,'reviewed_hash':snapshot['upstream']['reviewed_hash'],
            'reviewed_snapshot_hash':snapshot['upstream']['reviewed_snapshot_hash'],'reviewed_evidence':snapshot['reviewed_evidence'],
            'evidence_run_id':snapshot['upstream']['evidence_run_id'],'evidence_review_versions':snapshot['upstream']['evidence_review_versions'],
            'attribute_configuration':snapshot['attribute_configuration'],'method_settings':method_config,
            'prompt':metadata,'prompt_snapshot':copy.deepcopy(get_config()['prompts'][spec(method)[1]]),
            'configuration':configuration_metadata(),'requested_model':settings.model,'model':settings.model,'generation_settings':dict(settings.generation),
            'started_at':now(),'finished_at':None,'timeout_seconds':settings.timeout_seconds,'input_size':size,
            'status':'running','raw_response':None,'response_text':None,'usage':None,'result':None,'validation_errors':[]}
        retain(run)
        try:
            report('GPT '+method,'One independent '+method+' request for all enabled attributes against the shared full-document input.')
            raw,text=(request or structured_request)(settings,'',prompt,output_schema,request_name)
            run.update(raw_response=raw,response_text=text,usage=raw.get('usage'),model=raw.get('model',settings.model));retain(run)
            if raw.get('status','completed')!='completed' or not text:raise ValueError('Incomplete response or refusal.')
            run.update(result=validate_result(json.loads(text),snapshot,method,method_config),status='validated')
            run.update(status='completed' if current_matches(case,store,snapshot) else 'stale')
        except StoreError:
            run.update(status='validated' if run['result'] else 'failed',validation_errors=['Saved-input recheck unavailable; retry save without repeating GPT.'])
        except Exception as error:
            run.update(status='failed',validation_errors=[str(error)[:4000]] if run['raw_response'] and isinstance(error,ValueError) else [request_failure(error,settings.timeout_seconds)['message']])
        run['finished_at']=now();retain(run)
        report('Save '+method,'Saving this method separately; canonical confirmations are untouched.')
        try:result=retry_comparison_save(settings,store,run)
        except StoreError:return dict(run,result_source='retained_response',save_pending=True)
        return dict(result,result_source='generated')
    finally:lock.release()


def propose_scores(settings,case,store,context,**kwargs):
    snapshot=context if isinstance(context,dict) else freeze_context(settings,case,store,context)
    return propose_method(settings,case,store,snapshot,'score',**kwargs)


def compare_beliefs(settings,case,store,request_a=None,request_b=None,progress=None,context=None):
    snapshot=freeze_context(settings,case,store,context)
    # Complete DB lookups before either paid request. DB errors never become misses.
    canonical=store.latest_beliefs(case,snapshot['source']['source_hash'],snapshot['upstream']['reviewed_hash'])
    canonical_error=None
    if canonical:
        try:validate_beliefs(canonical['result'],canonical['reviewed_evidence'],canonical['assessment_settings'],definitions=historical_definitions(canonical))
        except ValueError as error:canonical_error=str(error);canonical=None
    result={'canonical_error':canonical_error,'direct':None,'score':None,'canonical':canonical,'direct_error':None,'score_error':None,'snapshot_hash':snapshot['input_hash'],'input_snapshot':snapshot}
    invalid={}
    for method in ('direct','score'):
        try:saved_method(settings,case,store,snapshot,method)
        except ValueError as error:invalid[method]=str(error)
    for method,request in (('direct',request_a),('score',request_b)):
        if method in invalid:result[method+'_error']=invalid[method];continue
        try:result[method]=propose_method(settings,case,store,snapshot,method,request=request,progress=progress)
        except (PermissionError,ValueError,StoreError) as error:result[method+'_error']=str(error)
    return result


def revalidate_score_response(settings,case,store,run):
    """Explicit save-only recovery; append a new record without calling GPT."""
    require_owner(settings)
    if run.get('comparison_method')!='score' or run.get('status')!='failed' or not run.get('response_text'):
        raise ValueError('A retained failed score response is required.')
    snapshot=run['input_snapshot'];validate_snapshot(snapshot)
    if not current_matches(case,store,snapshot):raise ValueError('Saved score input is stale; load current reviewed evidence first.')
    if run['cache_key']!=method_identity(settings,snapshot,'score'):raise ValueError('Score method settings changed; historical response preserved.')
    result=validate_result(json.loads(run['response_text']),snapshot,'score',run['method_settings'])
    recovered=copy.deepcopy(run)
    recovered.update(run_id=uuid.uuid4().hex,recovered_from_run_id=run['run_id'],
        validation_version='score-interpolation-support-v2.1',result=result,status='validated',
        finished_at=now(),validation_errors=[],result_source='retained_response')
    retain(recovered)
    return retry_comparison_save(settings,store,recovered)
