"""Temporary display-only comparison. Human confirmations stay in the direct view."""
import streamlit as st
from underwriting.assessment.score_service import score_identity
from underwriting.assessment.belief_service import reviewed_snapshot_hash

def distribution(values):
    for col,key in zip(st.columns(3),('low','high','unknown')):
        col.metric(key.title(),f"{values[key]*100:.1f}%" if values else '—')

def score_stale(view):
    run=view.get('score_run')
    if not run:return False
    if view.get('stale') or run.get('comparison_stale'):return True
    if view.get('comparison_snapshot_hash')!=run['reviewed_snapshot_hash']:return True
    return score_identity(view['settings'],run['reviewed_evidence'])!=run['cache_key']

def render_comparison(view,identifier,proposal):
    run=view.get('score_run')
    stale=score_stale(view) if run else False
    score=next((a for a in run['result']['assessments'] if a['attribute_id']==identifier),None) if run and run.get('result') and run['status']=='completed' and not stale else None
    left,right=st.columns(2)
    with left:
        st.markdown('**Direct AI beliefs**')
        distribution(proposal)
        st.caption('Stale' if view.get('stale') else view.get('origin','Missing direct result' if view.get('direct_comparison_error') else 'Not loaded yet'))
        if proposal:st.text(proposal['assessment_summary'])
        elif view.get('direct_comparison_error'):st.caption(view['direct_comparison_error'])
    with right:
        st.markdown('**AI score → beliefs**')
        if score:
            st.text(f"Concern score: {score['concern_score']:g} / 5" if score['assessable'] else 'Not assessable')
        else:st.text('Stale result' if stale else ('Missing result' if view.get('score_error') else 'Not loaded yet') if not run else run['status'].replace('_',' ').title())
        distribution(score['beliefs'] if score else None)
        if run:
            st.caption(('Stale · ' if stale else '')+({'generated':'Generated and saved','retained_response':'Retained response saved · no new GPT call'}.get(run.get('result_source'),'Loaded from database'))+' · '+run['finished_at'])
        if score:
            st.text(score['explanation'])
            with st.expander('Score evidence and information gaps'):
                st.caption('Evidence IDs: '+(', '.join(score['supporting_evidence_ids']) or '—'))
                st.write('Missing information: '+('; '.join(score['missing_information']) or 'No items returned in this response'))
                st.write('Contradictions: '+('; '.join(score['contradictions']) or 'No items returned in this response'))
        if run and run['status']!='completed':
            for issue in run.get('validation_errors',[]):st.text(issue)
        if view.get('score_error'):st.text(view['score_error'])
    st.caption('Experimental Low/High membership mapping. An assessable score has Unknown = 0, so this initial method does not represent partial ignorance. Unassessable evidence has Unknown = 100%. Display only; ER/BRB use existing human-confirmed beliefs.')
    if proposal and score:
        differences=', '.join(f"{key.title()} {(score['beliefs'][key]-proposal[key])*100:+.1f}" for key in ('low','high','unknown'))
        st.caption('Score − direct (percentage points): '+differences)
