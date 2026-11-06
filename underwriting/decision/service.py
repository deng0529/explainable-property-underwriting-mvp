"""Explicit saved-first decision review and owner-only immutable human decisions."""
import copy
import hashlib
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from underwriting.paths import PROJECT_ROOT
from underwriting.configuration.settings import get_config, configuration_metadata
from underwriting.configuration.access import require_owner
from underwriting.assessment.belief_service import content_hash
from underwriting.intake.service import now, structured_request, request_failure
from underwriting.decision.content import review_content_hash, same_review_content, VERSION as CONTENT_VERSION
from underwriting.decision.snapshot import assemble_snapshot, snapshot_hash, check_submission_ready, DecisionPrerequisiteError
from underwriting.decision.schema import validate_review, review_output_schema, validate_screening_narrative, ACTIONS

from underwriting.persistence.intake import reuse_read_connection

ROOT = PROJECT_ROOT
_guard = threading.Lock()
_locks = {}


def prompt_for(snapshot):
    definition = get_config()['prompts']['decision_review']
    values = {'configured_routing_guidance': '\n'.join(get_config()['decision']['routing_guidance']),
              'analysis_snapshot_json': json.dumps(snapshot, ensure_ascii=False, allow_nan=False, indent=2)}
    text = re.sub(r'{{(\w+)}}', lambda match: values[match[1]], definition['template'])
    return text, {'version': definition['version'], 'sha256': content_hash({'system':definition['system'],'template':definition['template']}),
                  'effective_sha256': content_hash({'system':definition['system'],'user':text}), 'hash_format':'full-messages-v2'}


def retain(run):
    from underwriting.reliability.diagnostics import attach
    attach(run,"Decision")
    directory = ROOT / '.decision_pending'
    directory.mkdir(exist_ok=True)
    path = directory / (run['review_run_id'] + '.json')
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(run, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def pending(case):
    directory = ROOT / '.decision_pending'
    values = []
    for path in directory.glob('*.json'):
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
            if (value['company_id'], value['submission_id']) == (case['case_id'], case['submission_version']):
                values.append(value)
        except (ValueError, KeyError, OSError):
            raise ValueError('A retained decision response could not be read; resolve it before a new paid request.') from None
    return sorted(values, key=lambda run: run['created_at'])


def retry_save(settings, store, run):
    require_owner(settings)
    run = copy.deepcopy(run)
    if run['status'] == 'running':
        elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(run['created_at'])).total_seconds()
        if elapsed <= settings.timeout_seconds + 60:
            raise ValueError('This decision request may still be running. Wait for completion or its timeout before recovery; do not repeat GPT.')
        try:
            if not run['raw_response'] or run['raw_response'].get('status', 'completed') != 'completed' or not run.get('response_text'):
                raise ValueError('Interrupted or incomplete response; no automatic retry was made.')
            run['review'] = validate_review(json.loads(run['response_text']), run['input_snapshot'])
            run['status'] = 'succeeded'
        except ValueError:
            run.update(status='failed', validation_errors=['Retained response requires review or explicit regeneration.'], error_message='Interrupted or invalid response.')
        run['completed_at'] = now()
        retain(run)
    if snapshot_hash(run['input_snapshot']) != run['input_snapshot_hash']:
        raise ValueError('Retained review snapshot integrity check failed.')
    if run['status'] == 'succeeded':
        validate_review(run['review'], run['input_snapshot'])
    from underwriting.reliability.diagnostics import attach
    run.pop('persistence_error',None)
    attach(run,'Decision')
    try:
        store.initialise_decision()
        store.save_decision_review(run)
    except Exception:
        run['persistence_error']=True
        retain(run)
        raise
    (ROOT / '.decision_pending' / (run['review_run_id'] + '.json')).unlink(missing_ok=True)
    return run


def review_matches_settings(run,settings):
    definition=get_config()['prompts']['decision_review']
    prompt_hash=content_hash({'system':definition['system'],'template':definition['template']})
    return (run['prompt']['version']==definition['version'] and run['prompt']['sha256']==prompt_hash
            and run.get('requested_model',run['model_identifier'])==settings.model
            and run['generation_settings']==dict(settings.generation))


def resolve_current_review(case, store, snapshot):
    digest=snapshot_hash(snapshot)
    exact=store.reviews_for_snapshot(case,digest)
    seen=set()
    # Legacy full snapshots remain immutable. Their content key is computed on
    # read, so unchanged reruns can reuse them without migration or another GPT call.
    for pass_number in range(2):
        candidates=exact if pass_number==0 else store.review_runs_for_submission(case)
        for run in candidates:
            if run['review_run_id'] in seen:
                continue
            seen.add(run['review_run_id'])
            try:
                validate_saved(run,case)
                validate_screening_narrative(run['review'],snapshot['submission']['outcome'])
                if same_review_content(run['input_snapshot'],snapshot) and review_matches_settings(run,store.settings):
                    return run
            except ValueError:
                continue
    return None


def latest_content_attempt(case,store,snapshot):
    for run in store.review_runs_for_submission(case,successful=False):
        if snapshot_hash(run['input_snapshot'])==run['input_snapshot_hash'] and same_review_content(run['input_snapshot'],snapshot) and review_matches_settings(run,store.settings):
            return run
    return None


@reuse_read_connection
def load_generate(settings, case, store, *, regenerate=False, confirmed=False, expected_run=None, request=None, progress=None, on_snapshot=None):
    report = progress or (lambda stage, message: None)
    report('Prerequisites', 'Checking saved Submission screening before any GPT request or write.')
    check_submission_ready(case, store)
    report('Prerequisites', 'Checking reviewed evidence, configured belief inputs and current saved Fire/Security ER and BRB results.')
    try:
        snapshot = assemble_snapshot(case, store)
    except ValueError as error:
        raise DecisionPrerequisiteError('Decision review is not ready: ' + str(error) + ' Complete the required saved Assessment stage first. No GPT request was made.') from None
    if on_snapshot is not None:
        on_snapshot(snapshot)
    report('Prerequisites', 'Saved Submission and Assessment records found in Turso. Earlier tabs do not need to be opened in this session.')
    if snapshot['submission']['outcome'] == 'Pause for clarification':
        report('Screening', 'Saved screening is paused. This review can identify concerns and request clarification or senior review; continuation is not permitted.')
    report('Database', 'Checking for a matching saved decision review before considering GPT.')
    digest = snapshot_hash(snapshot)
    content_digest=review_content_hash(snapshot)
    request_version=content_hash({'prompt':get_config()['prompts']['decision_review'],'model':settings.model,'generation':dict(settings.generation)})
    scope = (case['case_id'], case['submission_version'], content_digest, request_version)
    with _guard:
        lock = _locks.setdefault(scope, threading.Lock())
    if not lock.acquire(blocking=False):
        raise ValueError('A decision review for these inputs is already running.')
    try:
        saved = resolve_current_review(case,store,snapshot)
        if saved and not regenerate:
            try:
                validate_saved(saved, case)
                validate_screening_narrative(saved['review'],snapshot['submission']['outcome'])
            except ValueError:
                return saved, 'Historical review · inconsistent screening narrative; explicit regeneration required'
            if not same_review_content(saved['input_snapshot'],snapshot):
                raise ValueError('Saved decision review does not match current substantive content.')
            report('Database','Matching saved Decision review found. Loading its saved results; no GPT request will be made.')
            if settings.service_mode!='turso_readonly':
                from underwriting.decision.reports import attempt_report
                attempt_report(settings,case,store,saved,report)
            # Read-only viewers fetch the saved PDF on download, not before the
            # review appears. This avoids another complete upstream scan here.
            return saved, 'Turso database · no GPT call'
        require_owner(settings)
        if regenerate and not confirmed:
            raise PermissionError('Confirm the paid decision-review regeneration request.')
        previous = latest_content_attempt(case,store,snapshot)
        previous_id = previous['review_run_id'] if previous else None
        if regenerate and expected_run != previous_id:
            raise ValueError('Review history changed. Reload before confirming regeneration.')
        if previous and not regenerate:
            return previous, 'Turso database · previous unsuccessful/interrupted attempt; retry save or explicitly regenerate'
        if pending(case):
            raise ValueError('A decision response is retained locally. Retry save without calling GPT again.')
        from underwriting.reliability.inputs import generation_allowed
        generation_allowed(settings,request is not None)
        if settings.missing():
            raise ValueError('Missing configuration: ' + ', '.join(settings.missing()))
        check_submission_ready(case, store)
        if snapshot_hash(assemble_snapshot(case, store)) != digest:
            raise DecisionPrerequisiteError('Upstream records changed during the readiness check. Load again; no GPT request was made.')
        prompt, prompt_metadata = prompt_for(snapshot)
        from underwriting.reliability.inputs import check_request
        input_size=check_request(get_config()['prompts']['decision_review']['system'],prompt,settings.model)
        output_schema = review_output_schema(snapshot)
        identifier = uuid.uuid4().hex
        store.initialise_decision()
        if not store.claim(content_hash(['decision-review', *scope, previous_id]), identifier, None):
            raise ValueError('This decision request is already reserved. No duplicate GPT call was made.')
        run = {'review_run_id': identifier, 'company_id': case['case_id'], 'submission_id': case['submission_version'],
               'status': 'running', 'input_snapshot': snapshot, 'input_snapshot_hash': digest,
               'review_content_hash':content_digest,'content_identity_version':CONTENT_VERSION,'requested_model':settings.model,
               'upstream_record_references': snapshot['upstream_record_references'], 'prompt': prompt_metadata,
               'model_identifier': settings.model, 'generation_settings': dict(settings.generation),
               'configuration': configuration_metadata(), 'schema_version': 'decision-review-v1.4', 'schema_hash': content_hash(output_schema), 'review': None, 'raw_response': None, 'response_text': None,
               'input_size':input_size,'token_usage': None, 'validation_errors': [], 'error_message': '', 'created_at': now(), 'completed_at': None}
        store.save_decision_review(run)
        retain(run)
        try:
            report('GPT', 'One decision-review request using the saved snapshot; database credentials are never supplied.')
            raw, text = (request or structured_request)(settings,
                get_config()['prompts']['decision_review']['system'],
                prompt, output_schema, 'decision_review')
            run.update(raw_response=raw, response_text=text, token_usage=raw.get('usage'), model_identifier=raw.get('model', settings.model))
            retain(run)
            report('Validation', 'Checking the typed response, supplied reference IDs and screening routing consistency. Human factual review remains required.')
            if raw.get('status', 'completed') != 'completed' or not text:
                raise ValueError('GPT returned an incomplete response or refusal.')
            run['review'] = validate_review(json.loads(text), snapshot)
            if snapshot_hash(assemble_snapshot(case, store)) != digest:
                raise ValueError('Upstream records changed during generation. This review cannot be used as current.')
            run['status'] = 'succeeded'
        except Exception as error:
            message = str(error)[:2000] if isinstance(error, ValueError) and run['raw_response'] else request_failure(error, settings.timeout_seconds)['message']
            run.update(status='failed', validation_errors=[message], error_message=message)
        run['completed_at'] = now()
        retain(run)
        report('Save', 'Saving the versioned review and original response; a save retry never repeats GPT.')
        saved=retry_save(settings,store,run)
        if saved['status']=='succeeded':
            from underwriting.decision.reports import attempt_report
            attempt_report(settings,case,store,saved,report)
        return saved, 'GPT · saved to Turso'
    finally:
        lock.release()



def validate_saved(run, case):
    if run.get('status')!='succeeded' or not isinstance(run.get('review'),dict):
        raise ValueError('This saved review is unsuccessful or incomplete; it cannot be used as a successful cached result.')
    if (run['company_id'], run['submission_id']) != (case['case_id'], case['submission_version']):
        raise ValueError('Saved review belongs to a different company or submission.')
    if snapshot_hash(run['input_snapshot']) != run['input_snapshot_hash']:
        raise ValueError('Saved review snapshot integrity check failed.')
    return validate_review(run['review'], run['input_snapshot'])


def current_saved_review(case, store, identifier):
    run = store.review_by_id(identifier)
    if not run or run['status'] != 'succeeded' or (run['company_id'], run['submission_id']) != (case['case_id'], case['submission_version']):
        raise ValueError('Load a successful saved review for this company and submission first.')
    if not same_review_content(assemble_snapshot(case,store),run['input_snapshot']):
        raise ValueError('Decision review is stale. Load / Generate decision review for the current saved inputs.')
    if not review_matches_settings(run,store.settings):raise ValueError('Decision review prompt/model settings changed. Load / Generate the current review first.')
    validate_saved(run, case)
    return run


def save_decision(settings, case, store, review_id, *, action, notes, reviewer_label, secondary_actions, expected_decision=None, report_progress=None):
    require_owner(settings)
    run = current_saved_review(case, store, review_id)
    if action not in ACTIONS or not isinstance(notes, str) or len(notes) > 10000:
        raise ValueError('Choose a permitted human action and provide valid notes (maximum 10,000 characters).')
    if not isinstance(reviewer_label, str) or not reviewer_label.strip() or len(reviewer_label) > 200:
        raise ValueError('Enter a reviewer label (maximum 200 characters); this is not authenticated identity.')
    if action != run['review']['suggested_action']['action'] and not notes.strip():
        raise ValueError('Explain the reason in notes when your action differs from the AI suggestion.')
    if not isinstance(secondary_actions, list) or len(secondary_actions) != len(set(secondary_actions)) or not set(secondary_actions) <= set(ACTIONS) or action in secondary_actions:
        raise ValueError('Choose distinct permitted secondary actions, excluding the primary action.')
    if run['input_snapshot']['submission']['outcome'] in {'Outside demo scope', 'Pause for clarification'} and action == 'continue_underwriting_review':
        raise ValueError('Resolve the current outside-scope or paused screening before continuing within the demo.')
    history = store.decision_history(review_id)
    previous = history[0] if history else None
    if (previous['decision_id'] if previous else None) != expected_decision:
        raise ValueError('Decision history changed. Reload before saving another version.')
    record = {'decision_id': uuid.uuid4().hex, 'review_run_id': review_id, 'selected_action': action,
              'underwriter_notes': notes.strip(), 'reviewer_label': reviewer_label.strip(), 'secondary_actions': secondary_actions,
              'created_at': now(), 'supersedes_decision_id': expected_decision}
    if previous and all(previous[key] == record[key] for key in ('selected_action','underwriter_notes','reviewer_label','secondary_actions')):
        return previous
    store.save_human_decision(record, expected_decision)
    from underwriting.decision.reports import attempt_report
    attempt_report(settings,case,store,run,report_progress)
    return record


def export_report(settings, case, store, review_id):
    from underwriting.decision.reports import ensure_report
    run=current_saved_review(case,store,review_id)
    return ensure_report(settings,case,store,run)['pdf']


def export_historical_report(settings, case, store, review_id):
    """Read-only access to an explicitly labelled historical saved review."""
    if settings.missing(paid=False):
        raise ValueError('Missing configuration: ' + ', '.join(settings.missing(paid=False)))
    from underwriting.decision.reports import ensure_report
    run=store.review_by_id(review_id)
    if not run or run['status']!='succeeded':
        raise ValueError('A successful saved review is required for historical export.')
    return ensure_report(settings,case,store,run,historical=True)['pdf']


@reuse_read_connection
def export_current_report(settings, case, store):
    """Retrieve a matching durable report, rebuilding only on an explicit cache miss."""
    if settings.missing(paid=False):
        raise ValueError('Missing configuration: ' + ', '.join(settings.missing(paid=False)))
    from underwriting.decision.reports import ensure_report
    snapshot=assemble_snapshot(case,store)
    run=resolve_current_review(case,store,snapshot)
    if not run:
        raise ValueError('Generate the current Decision review before downloading its report. No GPT request was made by this download.')
    return ensure_report(settings,case,store,run)['pdf']
