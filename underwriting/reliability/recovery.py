"""Explicit recovery only for durably recorded responses; never repeats a call."""
import copy
from datetime import datetime, timezone


def recover_running(run, settings, validator, stage, *, lock=None):
    if run.get('status') != 'running':
        return run
    if lock and lock.locked():
        raise ValueError('This '+stage+' request is still running in this process. Wait; do not repeat it.')
    created = run.get('started_at') or run.get('created_at')
    try:
        elapsed = (datetime.now(timezone.utc)-datetime.fromisoformat(created)).total_seconds()
    except (TypeError,ValueError):
        raise ValueError('Interrupted run has no valid start time. Owner review is required; no GPT retry was made.') from None
    if elapsed <= run.get('timeout_seconds',settings.timeout_seconds)+60:
        raise ValueError('This request may still be running. Wait for its configured timeout and recovery grace period.')
    result = copy.deepcopy(run)
    raw = result.get('raw_response')
    if not raw or raw.get('status','completed')!='completed' or not result.get('response_text'):
        result.update(status='failed',error='No complete response was durably recorded. The remote outcome may be ambiguous; owner review and explicit confirmation are required before any new paid attempt.',validation_errors=['Interrupted request: no complete durably recorded response.'])
        result['recovery_limit']='A response never received or persisted cannot be recovered.'
    else:
        try:
            validator(result)
            result['recovered_from_received_response']=True
        except (ValueError,TypeError,KeyError):
            result.update(status='failed',validation_errors=['Retained response failed validation; original output preserved for review.'],error='Retained response failed validation.')
    result['finished_at']=datetime.now(timezone.utc).isoformat()
    result['recovery_action']='validated_retained_response' if result.get('recovered_from_received_response') else 'owner_review_required'
    return result
