"""Shared explicit workflow checks. Reads saved records; never generates stages."""
from underwriting.intake.screening import current_screening_record, resolve_screening


class PrerequisiteError(ValueError):
    def __init__(self, stage, message, status='missing'):
        self.stage, self.status = stage, status
        super().__init__(message)


def assessment_entry(case, store, source_hash):
    run = current_screening_record(case,store,source_hash)
    if not run or run.get('status') not in {'completed','needs_review'} or not run.get('result'):
        old = store.latest(case['case_id'],None)
        state = 'stale' if old else 'missing'
        raise PrerequisiteError('Submission', 'First load and complete current Submission information and screening. '+('Saved Submission belongs to an earlier source/version.' if old else 'No current saved Submission is available.'),state)
    resolution = resolve_screening(run,case)
    if resolution['outcome'] != 'Proceed to assessment':
        raise PrerequisiteError('Submission','Assessment is blocked by screening: '+resolution['outcome']+'. Resolve the recorded checks or an explicitly explained saved override first. No earlier stage was run.','blocked')
    return resolution


def reviewed_evidence(run, events):
    from underwriting.assessment.belief_service import reviewed_set
    if not run or run.get('status') != 'completed':
        raise PrerequisiteError('Evidence','First click Load attribute evidence, then review and confirm each attribute evidence.')
    try:
        return reviewed_set(run,events)
    except ValueError as error:
        raise PrerequisiteError('Evidence','First finish the human evidence reviews. '+str(error)) from None


def reviewed_context(case, store):
    from underwriting.assessment.belief_service import current_input
    try:
        return current_input(case,store)
    except ValueError as error:
        raise PrerequisiteError('Evidence',str(error),'stale' if 'changed' in str(error).lower() else 'missing') from None


def confirmed_values(run, events, members, source_hash, reviewed_hash):
    from underwriting.assessment.belief_schema import validate_values
    latest = {event['attribute_id']:event for event in events}
    values, versions, missing = {}, {}, []
    for identifier in sorted(members):
        event = latest.get(identifier)
        if (not event or not event.get('confirmed') or event['run_id'] != run['run_id']
                or event['source_hash'] != source_hash or event['reviewed_hash'] != reviewed_hash):
            missing.append(identifier)
            continue
        validate_values(event['values'],run.get('assessment_settings'),check_increment=False)
        values[identifier], versions[identifier] = dict(event['values']), event['event_id']
    if missing:
        raise PrerequisiteError('Beliefs','Current human-confirmed beliefs are required for: '+', '.join(missing)+'. First load reviewed evidence, then load proposed beliefs and confirm them.')
    return values, versions
