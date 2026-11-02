"""Small safe run diagnostics; no source text, credentials or exception bodies."""
from datetime import datetime, timezone
import copy


def diagnostic(stage, run=None, *, source=None, error=None, status=None, recovery=None):
    run = run or {}
    state = status or run.get('status','blocked' if error else 'not_loaded')
    start = run.get('started_at') or run.get('created_at')
    end = run.get('finished_at') or run.get('completed_at')
    duration = None
    try:
        if start and end:
            duration = round(max(0,(datetime.fromisoformat(end)-datetime.fromisoformat(start)).total_seconds()),3)
    except (ValueError,TypeError):
        pass
    if state in {'save_failed','running'}:
        recovery = recovery or ('retry_save' if run.get('raw_response') else 'review_interrupted_request')
    elif state in {'failed','stale','blocked'}:
        recovery = recovery or 'review_then_explicitly_retry'
    kind = type(error).__name__ if error else ('storage' if state=='save_failed' else run.get('error_diagnostics',{}).get('kind','validation_or_request') if state=='failed' else None)
    return {'schema_version':'run-diagnostics-v2','stage':stage,
            'run_id':run.get('run_id') or run.get('review_run_id'),'status':state,
            'result_source':source or ('received_response' if run.get('raw_response') else 'local_workflow'),
            'duration_seconds':duration,'error':{'kind':kind,'message':'The stage could not complete. Review prerequisites, validation status or storage; no automatic paid retry.'} if kind else None,
            'available_recovery_action':recovery}


def attach(run, stage, **kwargs):
    if run.get('persistence_error'):
        kwargs.update(status='save_failed',recovery='retry_save')
    run['diagnostics'] = diagnostic(stage,run,**kwargs)
    return run


def loaded(run, stage):
    result = copy.deepcopy(run)
    result['display_diagnostics'] = diagnostic(stage,run,source='saved_database_result')
    return result
