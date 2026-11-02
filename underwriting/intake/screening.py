"""Authoritative workflow screening resolution; validation concerns remain separate."""
import hashlib
import json
from underwriting.configuration.settings import get_config

RESOLVER_VERSION = 'screening-resolution-v2'
OUTCOMES = ('Proceed to assessment', 'Pause for clarification', 'Outside demo scope')


def resolve_checks(checks, override=None):
    policy = get_config()['screening']
    expected = {row['id'] for row in policy['checks']}
    values = {}
    for row in checks:
        identifier = row.get('check_id')
        if identifier not in expected or identifier in values or row.get('result') not in {'pass','fail','unknown'}:
            raise ValueError('Screening checks contain invalid/duplicate IDs or results.')
        values[identifier] = row
    reasons = []
    for identifier in sorted(expected):
        row = values.get(identifier)
        if row is None or row['result'] != 'pass':
            reason = row.get('reason') if row else None
            clarification = (row.get('information_required') if row else None) or reason or 'Clarify the essential information for this check.'
            reasons.append({'check_id':identifier,'result':row['result'] if row else 'unknown',
                            'reason':reason or 'No resolved saved result exists for this essential check.',
                            'clarification_needed':clarification})
    scope = [row for row in reasons if row['check_id'] in policy['scope_failure_checks'] and row['result']=='fail']
    derived = 'Outside demo scope' if scope else 'Pause for clarification' if reasons else 'Proceed to assessment'
    effective = derived
    if override:
        if override.get('outcome') not in OUTCOMES or not isinstance(override.get('reason'),str) or not override['reason'].strip() or not override.get('event_id'):
            raise ValueError('An explicit screening override requires a saved event ID, valid outcome and reason.')
        effective = override['outcome']
        reasons.append({'check_id':'Human override','result':effective,'reason':override['reason'],
                        'clarification_needed':override.get('clarification_needed') or override['reason']})
    return {'derived_outcome':derived,'outcome':effective,'reasons':reasons,'override':override}


def resolve_screening(run, case=None, override=None):
    if not run or not run.get('result'):
        return {'outcome':'Not screened yet','reasons':[],'override':None,'record_id':None}
    if case and (run.get('case_id'),run.get('source_version')) != (case['case_id'],case['submission_version']):
        raise ValueError('Screening record belongs to another company/submission version.')
    override = override if override is not None else run.get('screening_override')
    if override and any(override.get(key) != run.get(key) for key in ('case_id','source_version','source_hash')):
        raise ValueError('Screening override belongs to a different company, source or submission.')
    resolution = resolve_checks(run['result'].get('screening_checks',[]),override)
    resolution.update(resolver_version=RESOLVER_VERSION,record_id=run['run_id'],record_version=run.get('policy_version','historical'),
                      case_id=run.get('case_id'),source_version=run.get('source_version'),source_hash=run.get('source_hash'),
                      checks=run['result'].get('screening_checks',[]),validation_concerns=run.get('validation_issues',[]),
                      recorded_historical_outcome=run.get('outcome'))
    hashed = {key:value for key,value in resolution.items() if key != 'recorded_historical_outcome'}
    if run.get('outcome') and run['outcome']!=resolution['outcome']:
        resolution['correction']={'original_record_id':run['run_id'],'original_outcome':run['outcome'],'resolved_outcome':resolution['outcome'],
                                  'reason':'Resolved from the six stored check results and any explicit human override; general validation concerns are separate.',
                                  'resolver_version':RESOLVER_VERSION,'original_record_preserved':True}
    hashed = {key:value for key,value in resolution.items() if key != 'recorded_historical_outcome'}
    resolution['resolution_hash'] = hashlib.sha256(json.dumps(hashed,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()
    return resolution


def current_screening_record(case, store, source_hash):
    """Select one exact case/submission/source record; never fall back across versions."""
    run = store.latest(case['case_id'],case['submission_version'],source_hash)
    if run and (run.get('case_id'),run.get('source_version'),run.get('source_hash')) != (case['case_id'],case['submission_version'],source_hash):
        raise ValueError('Selected screening record does not match the current company/submission/source.')
    return run
