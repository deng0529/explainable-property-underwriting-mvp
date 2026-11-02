"""Streamlit Community Cloud entry point: saved-results viewing only."""
import os

# Fixed by the server entry point, never by a visitor widget or URL parameter.
os.environ['UNDERWRITING_PROFILE'] = 'public_readonly'

from underwriting.ui.app import main

main()
