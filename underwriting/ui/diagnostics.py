"""Presentation of allowlisted diagnostic metadata, never complete records."""
import streamlit as st
from underwriting.reliability.diagnostics import diagnostic


def remember(view, stage, run=None, error=None, source=None):
    view['last_diagnostics']=(run or {}).get('display_diagnostics') if run and not error and not source else None
    if view['last_diagnostics'] is None:
        view['last_diagnostics']=diagnostic(stage,run,source=source,error=error)


def render(view, stage):
    run=view.get('run') or view.get('saved') or view.get('latest_attempt') or view.get('attempt')
    value=view.get('last_diagnostics')
    if value is None and run:
        origin=view.get('origin') or ''
        source='saved_database_result' if 'database' in origin or 'SQLite' in origin else None
        value=diagnostic(stage,run,source=source,status='stale' if view.get('stale') else None)
    if value is None and view.get('error'):
        value=diagnostic(stage,error=ValueError('Stage error'))
    if value is not None:
        with st.expander('Run diagnostics · '+stage):
            st.json(value)


def source_label(value, settings):
    return value.replace('Turso database','Offline SQLite').replace('Turso','Offline SQLite') if settings.service_mode=='offline' else value
