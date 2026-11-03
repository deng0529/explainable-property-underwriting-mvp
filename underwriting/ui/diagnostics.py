"""Presentation of allowlisted diagnostic metadata, never complete records."""
import streamlit as st
from underwriting.reliability.diagnostics import diagnostic


def remember(view, stage, run=None, error=None, source=None):
    view['last_diagnostics']=(run or {}).get('display_diagnostics') if run and not error and not source else None
    if view['last_diagnostics'] is None:
        view['last_diagnostics']=diagnostic(stage,run,source=source,error=error)


def render(view, stage):
    """Technical metadata stays available internally, outside the review interface."""
    return None


def source_label(value, settings):
    return value.replace('Turso database','Offline SQLite').replace('Turso','Offline SQLite') if settings.service_mode=='offline' else value
