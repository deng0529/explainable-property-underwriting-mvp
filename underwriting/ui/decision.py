"""Explicit decision-review loading, human decision history and saved-record reports."""
import hashlib
from contextlib import nullcontext
import streamlit as st
from underwriting.configuration.settings import get_config
from underwriting.configuration.decision_model import effective_model_hash, get_model
from underwriting.assessment.belief_service import content_hash
from underwriting.assessment.evidence_service import document_hash
from underwriting.ui.intake import settings_for_ui, authorised
from underwriting.configuration.access import local_comparison_settings
from underwriting.persistence.decision import DecisionStore
from underwriting.persistence.intake import StoreError
from underwriting.decision.schema import ACTIONS, validate_screening_narrative
from underwriting.decision.service import load_generate, retry_save, pending, save_decision, export_report, export_historical_report, export_current_report, latest_content_attempt, SavedReviewUnavailableError
from underwriting.decision.content import same_review_content
from underwriting.decision.snapshot import assemble_snapshot, snapshot_hash, DecisionPrerequisiteError


def session_signature(case, prefix):
    """Only source/model identity, never which tabs have populated browser caches."""
    try:
        source=document_hash(case)
    except OSError:
        source='unavailable'
    return content_hash({'source':source,'model':effective_model_hash(get_model()),'decision_prompt':get_config()['prompts']['decision_review'],'belief_source':get_config()['belief_comparison']['calculation_input_method']})


def known_newer_inputs(snapshot, prefix):
    """Notice explicit newer saved events locally without querying on navigation."""
    if not snapshot:
        return False
    stages={'intake_view':'submission','evidence':'evidence','beliefs':'beliefs','er':'er','brb':'brb'}
    for key in list(st.session_state):
        if not key.startswith(prefix+':') or ':decision' in key:
            continue
        stage=stages.get(key.rsplit(':',1)[-1])
        value=st.session_state[key]
        if not stage or not isinstance(value,dict):
            continue
        direct_mode=snapshot.get('beliefs',{}).get('input_method')=='direct_ai'
        cached=(value.get('comparison_direct') if stage=='beliefs' and direct_mode else value.get('run')) or value.get('saved')
        saved=snapshot[stage].get('metadata',snapshot[stage])
        if isinstance(cached,dict) and cached.get('run_id')!=saved.get('run_id') and cached.get('finished_at','')>saved.get('finished_at',''):
            return True
        if stage=='beliefs' and direct_mode:continue
        if stage not in {'evidence','beliefs'}:
            continue
        events=snapshot[stage].get('review_events',[])
        saved_events={event['attribute_id']:event for event in events}
        current_events={event['attribute_id']:event for event in value.get('reviews',[])}
        for identifier,event in current_events.items():
            prior=saved_events.get(identifier)
            if prior and event['event_id']!=prior['event_id'] and event.get('created_at','')>prior.get('created_at',''):
                return True
    return False


def references(snapshot, ids):
    rows = []
    wanted = set(ids)
    def visit(value):
        if isinstance(value, dict):
            if any(value.get(name) in wanted for name in ('field_id','check_id','attribute_id','evidence_id','gap_id','conflict_id','contradiction_id','event_id','rule_id')):
                rows.append(value)
            else:
                for child in value.values():
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(snapshot)
    return {'reference_locations': {identifier: snapshot['reference_catalogue'][identifier] for identifier in ids}, 'saved_support': rows}


def render_decision(case, prefix):
    settings = local_comparison_settings(settings_for_ui())
    owner = authorised(settings)
    key = f"{prefix}:{case['submission_version']}:decision"
    view = st.session_state.setdefault(key, {'run': None, 'attempt': None, 'history': [], 'signature': None, 'stale': False, 'origin': None})
    signature = session_signature(case, prefix)
    if view['run'] and (view['signature'] != signature or known_newer_inputs(view.get('current_snapshot'),prefix)):
        view['stale'] = True
    store = None
    if not settings.missing(paid=False):
        try:
            store = DecisionStore(settings)
        except StoreError as error:
            st.error(str(error))
    st.subheader('Decision review')
    st.caption('Before any paid request, we check saved Submission screening and completed Assessment reviews, current configured belief inputs, ER and BRB results. Current saved results are reused; you do not need to reopen earlier tabs. Paused screening can be reviewed for clarification or referral.')
    def action(regenerate=False):
        if store is None:
            st.error('Missing configuration: ' + ', '.join(settings.missing(paid=False)) if settings.missing(paid=False) else 'Database connection unavailable.')
            return
        try:
            with st.status('Decision review · checking saved inputs', expanded=True) as status, (store.read_connection() if hasattr(store,'read_connection') else nullcontext()):
                def progress(stage, message):
                    status.update(label='Decision review · ' + stage)
                    status.write(message)
                    if stage=='Report retry needed':
                        view['report_error']=message
                    elif stage=='Report':
                        view.pop('report_error',None)
                loaded_context={}
                run, origin = load_generate(settings, case, store, regenerate=regenerate, confirmed=regenerate,
                    expected_run=view['attempt']['review_run_id'] if view['attempt'] else None, progress=progress,
                    on_snapshot=lambda snapshot: loaded_context.update(snapshot=snapshot))
                view.pop('blocked', None)
                view['attempt_origin'] = origin
                current_snapshot = loaded_context.get('snapshot')
                if current_snapshot is None:
                    current_snapshot = assemble_snapshot(case,store)
                current_hash = snapshot_hash(current_snapshot)
                view['current_screening'] = current_snapshot['submission']['screening_resolution']
                view['current_snapshot'] = current_snapshot
                # Explicit DB load: refresh an already-loaded Submission cache to
                # the identical selected record. Never write or re-extract data.
                from underwriting.ui.intake import view_key
                intake_key = view_key(case,current_snapshot['source']['sha256'])
                if intake_key in st.session_state:
                    current_intake = store.latest(case['case_id'],case['submission_version'],current_snapshot['source']['sha256'])
                    cached = dict(st.session_state[intake_key])
                    cached.update(saved=current_intake,stale=False,error=None)
                    st.session_state[intake_key] = cached
                view['attempt'] = latest_content_attempt(case,store,current_snapshot)
                if not view['attempt'] and run['input_snapshot_hash'] == current_hash:
                    view['attempt'] = run
                if run['status'] == 'succeeded':
                    st.session_state[key+':sections']='AI review summary'
                    view.update(run=run, origin=origin, history=store.decision_history(run['review_run_id']),
                                signature=session_signature(case,prefix), stale=not same_review_content(run['input_snapshot'],current_snapshot) or origin.startswith('Historical review'))
                    status.update(label='Historical review loaded · not current' if view['stale'] else 'Saved review loaded · no GPT request' if origin.startswith('Turso database') else 'New AI review generated and saved', state='complete', expanded=False)
                else:
                    loaded_failure = origin.startswith('Turso database')
                    status.update(label='Previous failed attempt loaded · no new GPT request' if loaded_failure else 'Decision review failed' + (' · earlier successful review preserved' if view['run'] else ' · no successful review loaded'), state='error', expanded=True)
                    if loaded_failure:
                        status.write('Current saved Submission and Assessment records were found. This is the previous failed response, not a new paid attempt. To request a new review with the corrected reference and routing constraints, use the explicit regeneration confirmation below.')
                    for issue in run['validation_errors']:
                        status.write(issue)
                    if view['run']:
                        view['stale'] = not same_review_content(assemble_snapshot(case,store),view['run']['input_snapshot'])
        except SavedReviewUnavailableError as error:
            view['blocked']=str(error)
            st.warning(str(error))
        except DecisionPrerequisiteError as error:
            status.update(label='Prerequisites incomplete · no GPT request made', state='error', expanded=False)
            from underwriting.ui.diagnostics import remember
            remember(view,'Decision',error=error)
            view['blocked'] = str(error)
            if view['run']:
                view['stale'] = True
        except (ValueError, OSError, PermissionError, StoreError) as error:
            from underwriting.ui.diagnostics import remember
            remember(view,'Decision',error=error)
            st.error(str(error))
            if view['run']:
                view['stale'] = True
        st.session_state[key] = view
    with st.container(horizontal=True):
        load_clicked=st.button('Load / Generate decision review' if owner else 'Load saved decision review',key=key+':load',type='primary',help='Read matching saved Submission and Assessment records directly from Turso before any GPT request.')
    if load_clicked:
        action()
    from underwriting.ui.diagnostics import render
    render(view,'Decision')
    if view.get('blocked'):
        st.warning(view['blocked'])
        st.caption('Complete the indicated work in Submission / Assessment, then click Load / Generate decision review to check again. No GPT request or database write was made by this blocked action.')
    if not view['run']:
        st.caption('No successful review loaded. See the failed-attempt details below.' if view.get('attempt') else 'Not loaded yet. Complete Submission, evidence reviews, confirmed beliefs, Fire/Security ER and overall BRB, then load the review.')
    if view['run']:
        render_review(case,key,settings,owner,store,view)
    else:
        ai_tab,human_tab=st.tabs(['AI review summary','Human decision'],key=key+':sections',on_change='rerun')
        with ai_tab:
            if ai_tab.open:
                st.caption('Not loaded yet. Use Load / Generate decision review to query saved records.')
        with human_tab:
            if human_tab.open:
                st.caption('Load a current AI review before recording a human decision.')
    st.download_button('Download review report',data=lambda: export_current_report(settings,case,store),
                       file_name=f"{case['case_id']}-review.pdf",mime='application/pdf',
                       disabled=store is None or not view['run'] or view['stale'],
                       key=key+':current_report',on_click='ignore',help='Retrieve the matching saved PDF. No GPT request.')
    if view.get('report_error'):
        st.warning(view['report_error'])
        if st.button('Retry report · no GPT call',disabled=not owner or not view['run'] or view['stale'],key=key+':retry_report'):
            try:
                from underwriting.decision.reports import ensure_report
                ensure_report(settings,case,store,view['run'])
                view.pop('report_error',None)
                st.success('PDF saved. The AI review was not regenerated.')
            except (ValueError,OSError,PermissionError,StoreError) as error:
                st.error(str(error))
    with st.expander('Regenerate decision review with GPT'):
        st.caption('One explicit paid request. Previous reviews and human decisions remain in history; failed attempts do not replace them.')
        confirm = st.checkbox('Confirm a new paid decision review; preserve earlier versions', disabled=not owner, key=key+':regenerate_confirm')
        if st.button('Regenerate decision review', disabled=not owner or not confirm or store is None or bool(view.get('blocked')), key=key+':regenerate'):
            action(True)
    attempt = view.get('attempt')
    if attempt and attempt['status'] != 'succeeded':
        if view.get('blocked'):
            st.caption('An earlier failed attempt remains in history. This prerequisite check did not make a GPT request.')
        else:
            st.error('Previous saved attempt: validation failed or request did not finish. This is historical failure information; loading it does not call GPT. Use explicit regeneration only after the saved-data readiness check passes.')
        with st.expander('Decision review error details', expanded=not bool(view.get('blocked'))):
            st.caption('Saved attempt status: ' + attempt['status'])
            issues = attempt.get('validation_errors') or [attempt.get('error_message') or 'This request did not finish. No automatic retry was made.']
            for issue in issues:
                st.text(issue)
            if any('reference' in issue.lower() for issue in issues):
                st.caption('The response used unsupported reference formatting or IDs. New requests now require exact IDs from the saved snapshot. The original failed response is preserved.')
    if owner:
        try:
            retained = pending(case)
        except ValueError as error:
            st.error(str(error))
            retained = []
        for run in retained:
            st.warning('Decision response retained locally. Retry saving without another GPT call.')
            if st.button('Retry save · decision review · no GPT call', disabled=store is None, key=key+':retry:'+run['review_run_id']):
                try:
                    saved = retry_save(settings,store,run)
                    view['attempt'] = saved
                    if saved['status'] == 'succeeded':
                        stale = not same_review_content(assemble_snapshot(case,store),saved['input_snapshot'])
                        view.update(run=saved, history=store.decision_history(saved['review_run_id']), stale=stale,
                                    signature=session_signature(case,prefix), origin='Retained GPT response · saved without another GPT call')
                    st.rerun()
                except (ValueError, OSError, PermissionError, StoreError) as error:
                    st.error(str(error))


def concise_text(text, limit, label):
    words = text.split()
    st.write(text if len(words)<=limit else ' '.join(words[:limit])+'…')
    if len(words)>limit:
        with st.expander(label):
            st.text(text)


def compact_rows(rows, label):
    st.table(rows[:5],hide_index=True)
    if len(rows)>5:
        with st.expander(f'{label} ({len(rows)-5} more)'):
            st.table(rows[5:],hide_index=True)


def render_review(case, key, settings, owner, store, view):
    from underwriting.decision.fields import submission_field
    run=view['run']
    historical=view['stale']
    snapshot=view.get('current_snapshot')
    if not snapshot:
        st.error('Current saved report inputs were not loaded. Click Load / Generate decision review to retrieve them from Turso.')
        return
    try:
        business=submission_field(snapshot,'business_description')
        premises=submission_field(snapshot,'site_address')
    except ValueError as error:
        st.error(str(error))
        return
    screening=snapshot['submission']['screening_resolution']
    if historical:
        st.caption('Historical review — not current')
        st.caption('Original review date: '+run['completed_at'][:10])
        prior=run['input_snapshot']['submission']
        changed=prior['outcome']!=screening['outcome'] or prior.get('screening_resolution',{}).get('resolution_hash')!=screening['resolution_hash']
        st.caption('Screening changed since this review' if changed else 'Saved upstream inputs changed since this review')
    else:
        st.caption('AI review saved '+run['completed_at'][:10])
        st.caption('Loaded from database — no GPT call on this load' if view.get('origin','').startswith('Turso database') else 'Generated by one explicit GPT request and saved to the database')
    ai_tab,human_tab=st.tabs(['AI review summary','Human decision'],key=key+':sections',on_change='rerun')
    with ai_tab:
        if ai_tab.open:
            if historical:
                st.write('Current AI review pending. The previous review remains available in History below. Regeneration is an explicit paid action.')
            else:
                st.write(run['review']['review_summary'])
            st.caption('Business: '+business+' | Premises: '+premises)
            with st.expander('Screening details'):
                st.write('Current resolved outcome: '+screening['outcome'])
                st.table([{'Check':row['check_id'],'Result':row['result'].title()} for row in screening['checks']],hide_index=True)
                if screening.get('override'):
                    override=screening['override']
                    st.write('Human override: '+override['outcome']+' · '+override['reason']+' · '+str(override.get('created_at','Date not recorded')))
                for reason in screening['reasons']:
                    st.write(reason['check_id']+': '+reason['reason']+' Clarification needed: '+reason['clarification_needed'])
                if screening.get('correction'):
                    st.caption('A historical aggregate was corrected through the shared resolver. Its original record is preserved; non-blocking validation concerns do not independently pause six Pass checks.')
            st.markdown('**Current saved assessment**')
            if snapshot['beliefs'].get('input_method')=='direct_ai':st.caption('Direct AI beliefs → Fire/Security ER → overall BRB. These inputs are not human-confirmed beliefs.')
            for column,(state,value) in zip(st.columns(3),snapshot['brb']['result']['state_beliefs'].items()):
                column.metric({'low':'Low concern','high':'High concern','unknown':'Unknown'}[state],f'{value:.2f}')
            residual=snapshot['brb']['result']['residual_unassigned']
            if residual:
                st.caption('Separate residual unassigned belief: '+str(residual))
            form_key=key+':'+run['review_run_id']
            review=None if historical else run['review']
            st.subheader('Key concerns')
            if review and review['key_concerns']:
                compact_rows([{'Concern':row['finding'],'Why it matters':row['significance'],'Follow-up':row['suggested_follow_up']} for row in review['key_concerns']],'Additional concerns')
            else:
                st.caption('Awaiting the current AI review.' if historical else 'No supported concern items identified in this saved review.')
            st.subheader('Missing information')
            if review and review['missing_information']:
                compact_rows([{'Information/document':row['information_or_document'],'Reason':row['reason']} for row in review['missing_information']],'Additional information requests')
            else:
                st.caption('Awaiting the current AI review.' if historical else 'No essential information requests recorded in this review.')
            st.subheader('AI-suggested next action')
            if review:
                st.write(ACTIONS[review['suggested_action']['action']])
                concise_text(review['suggested_action']['rationale'],60,'Full action reason')
            else:
                st.write('Current AI review pending. No historical recommendation is presented as current.')
            if historical:
                with st.expander('History — original AI review (not current)'):
                    previous=run['review']
                    st.caption('Original date: '+run['completed_at'][:10]+' · Screening at review: '+run['input_snapshot']['submission']['outcome'])
                    st.markdown('**Original summary**')
                    st.write(previous['review_summary'])
                    st.write(previous['assessment_interpretation'])
                    if previous['key_concerns']:
                        st.table([{'Concern':row['finding'],'Why it matters':row['significance'],'Follow-up':row['suggested_follow_up']} for row in previous['key_concerns']],hide_index=True)
                    if previous['missing_information']:
                        st.table([{'Information/document':row['information_or_document'],'Reason':row['reason']} for row in previous['missing_information']],hide_index=True)
                    st.write('Original AI suggestion: '+ACTIONS[previous['suggested_action']['action']])
                    st.write(previous['suggested_action']['rationale'])
                    st.download_button('Download historical review report',data=lambda: export_historical_report(settings,case,store,run['review_run_id']),
                                       file_name=f"{case['case_id']}-historical-review.pdf",mime='application/pdf',disabled=store is None,
                                       key=form_key+':historical_report',on_click='ignore',help='Original matching saved snapshot, marked not current. No GPT request.')
    with human_tab:
        if human_tab.open:
            form_key = key+':'+run['review_run_id']
            disabled = not owner or view['stale'] or store is None
            with st.container(border=True,key=form_key+':human_draft'):
                selected = st.selectbox('Human next action', list(ACTIONS), format_func=ACTIONS.get, index=None, disabled=disabled, key=form_key+':action',persist_state='session')
                notes = st.text_area('Underwriter notes / reason for a different action', max_chars=10000, disabled=disabled, key=form_key+':notes',persist_state='session')
                label = st.text_input('Reviewer label (not authenticated identity)', max_chars=200, disabled=disabled, key=form_key+':reviewer',persist_state='session')
                secondary = st.multiselect('Optional human secondary actions', list(ACTIONS), format_func=ACTIONS.get, disabled=disabled, key=form_key+':secondary',persist_state='session')
                if st.button('Save decision',disabled=disabled,key=form_key+':save'):
                    try:
                        previous = view['history'][0]['decision_id'] if view['history'] else None
                        save_decision(settings,case,store,run['review_run_id'],action=selected,notes=notes,reviewer_label=label,
                                      secondary_actions=secondary,expected_decision=previous,report_progress=lambda stage,message: view.update(report_error=message) if stage=='Report retry needed' else view.pop('report_error',None))
                        view['history'] = store.decision_history(run['review_run_id'])
                        st.success('Human decision saved. The AI review was not regenerated.')
                    except (ValueError,OSError,PermissionError,StoreError) as error:
                        st.error(str(error))
            if not owner:
                st.caption('Read-only access: saved review/report viewing only. An authorised local owner records decisions.')
            with st.expander('Saved decision history'):
                if not view['history']:
                    st.caption('No human decision recorded. The report is a draft.')
                for event in view['history']:
                    st.write(event['created_at']+' · '+event['reviewer_label']+' · '+ACTIONS[event['selected_action']])
                    st.text(event['underwriter_notes'])
                    st.caption('Saved human decision' + (' · replaces an earlier saved decision' if event['supersedes_decision_id'] else ''))
