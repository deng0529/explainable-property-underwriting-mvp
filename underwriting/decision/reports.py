"""Durable versioned PDF artifacts; no GPT and no browser-state dependencies."""
import hashlib
import threading
import uuid
from underwriting.configuration.access import require_owner
from underwriting.assessment.belief_service import content_hash
from underwriting.intake.service import now
from underwriting.persistence.intake import StoreError
from underwriting.decision import report

_guard=threading.Lock()
_locks={}


def ensure_report(settings, case, store, run, *, template_version=None, historical=False):
    from underwriting.decision.service import current_saved_review
    if historical:
        from underwriting.decision.service import validate_saved
        validate_saved(run,case)
    else:
        run=current_saved_review(case,store,run['review_run_id'])
    history=store.decision_history(run['review_run_id'])
    decision=history[0] if history else None
    version=(template_version or report.TEMPLATE_VERSION)+(':historical' if historical else '')
    key=content_hash([run['review_run_id'],run['input_snapshot_hash'],version,decision['decision_id'] if decision else None])
    with _guard:
        lock=_locks.setdefault(key,threading.Lock())
    with lock:
        saved=store.saved_report(key)
        if saved:
            if hashlib.sha256(saved['pdf']).hexdigest()!=saved['report_hash']:
                raise StoreError('Saved PDF failed its integrity check. Ask the owner to create a new report version.')
            return saved
        require_owner(settings)
        pdf=report.render_report(run,decision,historical=historical)
        # Guard against a concurrent upstream change while formatting the artifact.
        if not historical:
            current_saved_review(case,store,run['review_run_id'])
        newest=store.decision_history(run['review_run_id'])
        if (newest[0]['decision_id'] if newest else None)!=(decision['decision_id'] if decision else None):
            raise ValueError('Human decision changed while building the report. Retry report; no GPT request is needed.')
        record={'report_id':uuid.uuid4().hex,'artifact_key':key,'review_run_id':run['review_run_id'],
                'snapshot_hash':run['input_snapshot_hash'],'template_version':version,
                'human_decision_id':decision['decision_id'] if decision else None,
                'report_hash':hashlib.sha256(pdf).hexdigest(),'created_at':now(),'pdf':pdf}
        store.initialise_reports()
        store.save_report(record)
        return store.saved_report(key)


def attempt_report(settings, case, store, run, progress=None):
    emit=progress or (lambda stage,message:None)
    try:
        emit('Report','Checking the matching durable PDF artifact; formatting only if missing.')
        result=ensure_report(settings,case,store,run)
        emit('Report','The versioned PDF is saved in Turso and ready for reuse.')
        return result
    except Exception as error:
        detail=str(error) if isinstance(error,(ValueError,OSError,PermissionError,StoreError)) else 'PDF creation failed.'
        emit('Report retry needed','Review/decision remains saved. '+detail+' Retry report; do not regenerate the AI review.')
        return None
