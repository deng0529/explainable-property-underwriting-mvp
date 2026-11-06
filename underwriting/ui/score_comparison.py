"""Experimental full-context comparison; canonical confirmation remains separate."""
import streamlit as st
from underwriting.assessment.score_service import method_identity
from underwriting.assessment.evidence_schema import attribute_definitions

def distribution(values):
    for column,name in zip(st.columns(3),('low','high','unknown')):
        column.metric(name.title(),f"{values[name]*100:.1f}%" if values else '—')

def method_stale(view,run,method):
    if not run:return False
    if run.get('comparison_stale') or not run.get('input_snapshot'):return True
    if view.get('comparison_snapshot_hash')!=run.get('input_hash'):return True
    if view.get('comparison_reviewed_snapshot_hash')!=run.get('reviewed_snapshot_hash'):return True
    if attribute_definitions()!=run['attribute_configuration']:return True
    return method_identity(view['settings'],run['input_snapshot'],method)!=run['cache_key']

def score_stale(view):return method_stale(view,view.get('score_run'),'score')

def result_for(view,method,identifier):
    run=view.get('comparison_direct' if method=='direct' else 'score_run')
    if not run or method_stale(view,run,method) or run.get('status')!='completed' or not run.get('result'):return None
    return next((a for a in run['result']['assessments'] if a['attribute_id']==identifier),None)

def details(item):
    with st.expander('Evidence support and unresolved information'):
        for name in ('low','high'):
            support=item[name+'_support'];st.markdown('**'+name.title()+' support**')
            st.text(support['explanation']);st.caption('Evidence IDs: '+(', '.join(support['evidence_ids']) or '—'))
        st.markdown('**Unknown support**')
        if item['unresolved_information']:st.caption('Unassigned belief reflects the unresolved information below; these are gaps, not evidence of an absent protection.')
        elif not item['evidence_review_flags']:st.caption('No unresolved support was returned for Unknown.')
        for issue in item['unresolved_information']:
            st.text(issue['issue']+' · '+issue['materiality']+' · '+issue['potential_assessment_impact'])
        for flag in item['evidence_review_flags']:
            st.text('Evidence review needed: '+flag['issue'])
            st.caption('Evidence IDs: '+(', '.join(flag['evidence_ids']) or '— (unreviewed source finding)'))
            if flag['page'] is not None:st.caption(f"Page {flag['page']} · {flag['section'] or 'Section not specified'}");st.text(flag['exact_quote'])
        if not item['unresolved_information'] and not item['evidence_review_flags']:st.caption('No unresolved items or evidence-review flags returned.')

def status(view,run,method):
    stale=method_stale(view,run,method)
    if stale:st.caption('Stale result · input or method version differs')
    elif run:
        source={'generated':('Generated and saved' if run['status']=='completed' else 'Generated response · saved attempt'),'database':'Loaded from database','retained_response':'Retained response · no new GPT call'}.get(run.get('result_source'),'Saved result')
        st.caption(('Save pending · ' if run.get('save_pending') else '')+run['status'].title()+' · '+source+' · '+str(run['finished_at'] or 'Interrupted'))
    else:st.caption('Missing result · click Load proposed beliefs')
    error=view.get('direct_comparison_error' if method=='direct' else 'score_error')
    if error:st.text(error)
    if run and run['status'] not in {'completed','stale'}:
        for issue in run.get('validation_errors',[]):st.text(issue)

def support_payload(item):
    return {key:item[key] for key in ('low_support','high_support','unresolved_information','evidence_review_flags')}

def render_comparison(view,identifier,proposal=None):
    a=result_for(view,'direct',identifier);b=result_for(view,'score',identifier)
    shared=bool(a and b and support_payload(a)==support_payload(b))
    direct_tab,score_tab=st.tabs(['Direct AI beliefs','Score + Unknown'])
    with direct_tab:
        st.markdown('**Direct AI beliefs**');distribution(a)
        status(view,view.get('comparison_direct'),'direct')
        if a:
            st.text(a['allocation_explanation'])
            if not shared:details(a)
    with score_tab:
        st.markdown('**Score + Unknown**')
        if b:
            minimum,maximum=view['score_run']['method_settings']['score_scale']['minimum'],view['score_run']['method_settings']['score_scale']['maximum']
            st.text(f"Concern score: {b['concern_score']:g} / {maximum:g}" if b['assessable'] else 'Not assessable')
            st.text(f"Unknown judgement: {b['unknown']*100:.1f}%")
        distribution(b['beliefs'] if b else None);status(view,view.get('score_run'),'score')
        if b:
            st.text(b['score_explanation']);st.text(b['unknown_explanation'])
            if not shared:details(b)
    if shared:details(a)
    if a and b:
        st.caption('Method B − Method A (percentage points): '+', '.join(f"{key.title()} {(b['beliefs'][key]-a[key])*100:+.1f}" for key in ('low','high','unknown')))
    st.caption('Both methods use the same full-document/reviewed-evidence snapshot. Explicit ER/BRB calculations use Direct AI beliefs; Score + Unknown remains comparison-only. Human confirmations remain separate.')
