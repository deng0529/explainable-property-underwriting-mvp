"""Read-only assembly of current saved upstream records. Never runs earlier stages."""
import copy
from underwriting.assessment.belief_service import current_input, content_hash
from underwriting.assessment.er_service import current_confirmed, input_belief_hash
from underwriting.assessment.brb_service import current_er_inputs, er_result_hash, rule_configuration_hash
from underwriting.configuration.decision_model import get_model, effective_er_model_hash
from underwriting.models.er import ENGINE_VERSION as ER_VERSION
from underwriting.models.brb import ENGINE_VERSION as BRB_VERSION
from underwriting.intake.screening import resolve_screening, current_screening_record


def assemble_snapshot(case, store):
    model = get_model()
    digest, reviewed_hash, proposal_id, confirmed, confirmation_ids, evidence_id = current_confirmed(case, store, model)
    intake = current_screening_record(case,store,digest)
    if not intake or intake.get('status') not in {'completed', 'needs_review'} or not intake.get('result'):
        raise ValueError('Complete and save Submission extraction and screening for this source first.')
    checks = intake['result'].get('screening_checks', [])
    if {item['check_id'] for item in checks} != {f'SC{i:02}' for i in range(1, 7)} or len(checks) != 6:
        raise ValueError('Complete and save all six screening checks first.')
    from underwriting.decision.fields import submission_field
    for identifier in ('company_name','business_description','site_address'):
        submission_field({'submission':{'extraction_and_screening':intake['result']}},identifier)
    screening = resolve_screening(intake,case)
    _, evidence, reviewed, current_hash, evidence_versions = current_input(case, store)
    proposal = store.latest_beliefs(case, digest, reviewed_hash)
    if current_hash != reviewed_hash or evidence['run_id'] != evidence_id or not proposal or proposal['run_id'] != proposal_id:
        raise ValueError('Assessment records changed while loading. Load the decision review again.')
    er = store.latest_er_compatible(case, digest, input_belief_hash(digest, reviewed_hash, proposal_id, confirmed), effective_er_model_hash(model), ER_VERSION)
    if not er:
        raise ValueError('Calculate and save current Fire/Security ER results first.')
    current_er_inputs(case, er, store, model)
    brb = store.latest_brb(case, digest, er['run_id'], er_result_hash(er), rule_configuration_hash(model), BRB_VERSION)
    if not brb or brb.get('status') != 'completed':
        raise ValueError('Calculate and save the current overall BRB assessment first.')
    evidence_reviews = store.reviews(evidence_id)
    belief_reviews = store.belief_reviews(proposal_id)
    intake_reviews = store.review_history(case, intake)
    def metadata(run):
        return {key: copy.deepcopy(run[key]) for key in ('run_id', 'case_id', 'source_version', 'source_hash', 'status', 'finished_at', 'prompt', 'prompts', 'model', 'configuration', 'decision_model', 'policy_version', 'schema_version', 'engine_version', 'rule_configuration', 'attribute_configuration_hash', 'assessment_settings_hash') if key in run}
    references = {'intake_run_id': intake['run_id'], 'evidence_run_id': evidence_id, 'belief_run_id': proposal_id,
                  'er_run_id': er['run_id'], 'brb_run_id': brb['run_id'], 'evidence_review_ids': evidence_versions,
                  'belief_confirmation_ids': confirmation_ids, 'submission_review_ids': [row['event_id'] for row in intake_reviews]}
    snapshot = {'schema_version': 'decision-snapshot-v1',
                'company': {key: case[key] for key in ('case_id', 'company_name', 'submission_version', 'status') if key in case},
                'source': {'sha256': digest, 'submission_version': case['submission_version'], 'document_name': case['document_path'].replace('\\', '/').split('/')[-1]},
                'submission': {'metadata': metadata(intake), 'extraction_and_screening': copy.deepcopy(intake['result']),
                               'outcome': screening['outcome'], 'screening_resolution': screening, 'validation_issues': intake.get('validation_issues', []), 'human_reviews': intake_reviews},
                'evidence': {'metadata': metadata(evidence), 'ai_original': copy.deepcopy(evidence['result']), 'human_reviewed': reviewed, 'review_events': evidence_reviews},
                'beliefs': {'metadata': metadata(proposal), 'ai_proposals': copy.deepcopy(proposal['result']), 'human_confirmed': confirmed, 'review_events': belief_reviews},
                'er': copy.deepcopy(er), 'brb': copy.deepcopy(brb), 'upstream_record_references': references,
                'completion': {'submission': intake['status'], 'screening': screening['outcome'], 'submission_review': intake_reviews[0]['status'] if intake_reviews else 'Not reviewed yet',
                               'evidence': 'Human reviewed', 'beliefs': 'Human confirmed', 'er': 'Current saved result', 'brb': 'Current saved result'}}
    catalogue = {}
    def collect(value, path='snapshot'):
        if isinstance(value, dict):
            for name, identifier in value.items():
                if name in {'run_id', 'event_id', 'evidence_id', 'gap_id', 'conflict_id', 'contradiction_id', 'field_id', 'check_id', 'rule_id', 'attribute_id'} and isinstance(identifier, str):
                    catalogue.setdefault(identifier, []).append(path)
                collect(identifier, path + '.' + name)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                collect(child, path + f'[{index}]')
    collect(snapshot)
    snapshot['reference_catalogue'] = catalogue
    if current_confirmed(case, store, model) != (digest, reviewed_hash, proposal_id, confirmed, confirmation_ids, evidence_id):
        raise ValueError('Confirmed inputs changed while loading; load the decision review again.')
    current_er_inputs(case, er, store, model)
    return snapshot


def snapshot_hash(snapshot):
    return content_hash(snapshot)


class DecisionPrerequisiteError(ValueError):
    """An upstream workflow requirement failed before generation was permitted."""


def check_submission_ready(case, store):
    # Resolve through the existing source helper (also shared by upstream stages).
    from underwriting.assessment.belief_service import document_hash
    digest = document_hash(case)
    intake = current_screening_record(case,store,digest)
    if not intake or intake.get('status') not in {'completed', 'needs_review'} or not intake.get('result'):
        raise DecisionPrerequisiteError('Complete and save Submission extraction and screening first. No GPT request was made.')
    checks = intake['result'].get('screening_checks', [])
    if len(checks) != 6 or {row.get('check_id') for row in checks} != {f'SC{i:02}' for i in range(1,7)}:
        raise DecisionPrerequisiteError('Complete all six Submission screening checks first. No GPT request was made.')
    issues = intake.get('validation_issues', [])
    resolve_screening(intake,case)
    return intake
