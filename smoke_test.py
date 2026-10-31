"""Focused three-tab UI/document smoke check; no external services."""
import hashlib
from unittest.mock import patch

import streamlit as st
from streamlit.testing.v1 import AppTest

import case_catalogue
import source_document

cases = case_catalogue.load_cases()
case_id = "NB-2026-001"
prefix = f"case:{case_id}"
original = source_document.read_source(cases[case_id]["document_path"])
original_hash = hashlib.sha256(original).hexdigest()
assert len(cases) == 1
assert len(source_document.render_pages(original)) == 4
downloads = []
real_download = st.download_button


def capture_download(*args, **kwargs):
    downloads.append(kwargs["data"])
    return real_download(*args, **kwargs)


with patch.object(st, "download_button", side_effect=capture_download):
    app = AppTest.from_file("app.py", default_timeout=30).run()
    assert not app.exception, app.exception
    assert [tab.label for tab in app.tabs] == ["Submission", "Assessment", "Decision"]
    assert all(len(tab.children) for tab in app.tabs)
    assert app.selectbox(key="selected_case_id").options == ["Northbridge Furniture Storage Ltd"]
    app.selectbox(key="selected_case_id").set_value(case_id).run()
    assert not app.exception
    assert downloads[-1] == original
    assert len(app.get("image")) == 1
    assert app.button(key=f"{prefix}:previous_page").disabled
    for page in range(2, 5):
        app.button(key=f"{prefix}:next_page").click().run()
        assert not app.exception
        assert app.session_state[f"{prefix}:pdf_page"] == page
        assert len(app.get("image")) == 1
    assert app.button(key=f"{prefix}:next_page").disabled
    for page in range(3, 0, -1):
        app.button(key=f"{prefix}:previous_page").click().run()
        assert app.session_state[f"{prefix}:pdf_page"] == page
    names = ["F1 Combustible storage", "F2 Ignition controls", "F3 Fire protection",
             "S1 Access protection", "S2 Intrusion detection"]
    for name in names:
        app.button(key=f"{prefix}:select:{name}").click().run()
        assert not app.exception
        assert app.session_state[f"{prefix}:attribute"] == name
        assert name in [element.value for element in app.subheader]
    assert all(field.value == "" and field.disabled for field in app.text_input)
    assert all(field.value == "" and field.disabled for field in app.text_area)
    assert app.selectbox(key=f"{prefix}:next_action").value is None
    assert app.selectbox(key=f"{prefix}:next_action").disabled
    assert all(button.disabled for button in app.button
               if ":select:" not in button.key
               and not button.key.endswith((":previous_page", ":next_page")))
    # A second entry exists only in memory during this check, never in the catalogue.
    test_case = dict(cases[case_id], case_id="TEST-ONLY", company_name="Test-only company")
    with patch.object(case_catalogue, "load_cases", return_value={**cases, "TEST-ONLY": test_case}):
        app.run()
        app.selectbox(key="selected_case_id").set_value("TEST-ONLY").run()
        assert not app.exception
        assert app.session_state["case:TEST-ONLY:attribute"] == names[0]
        assert app.session_state["case:TEST-ONLY:pdf_page"] == 1
        app.button(key="case:TEST-ONLY:next_page").click().run()
        app.selectbox(key="selected_case_id").set_value(case_id).run()
        assert app.session_state[f"{prefix}:attribute"] == names[-1]
        assert app.session_state[f"{prefix}:pdf_page"] == 1
    with patch.object(source_document, "read_source", side_effect=FileNotFoundError("Missing source")):
        app.run()
        assert not app.exception
        assert "Source PDF unavailable" in app.error[0].value
    with patch.object(source_document, "render_pages", side_effect=RuntimeError("Preview unavailable")):
        app.run()
        assert not app.exception
        assert "PDF previews unavailable" in app.error[0].value
        assert downloads[-1] == original

assert hashlib.sha256(source_document.read_source()).hexdigest() == original_hash
print("PASS: three tab contents; five attributes; company selection/case isolation; four-page navigation; exact PDF download; disabled blank future features; visible source/preview errors; unchanged PDF.")
