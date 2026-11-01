"""Focused three-tab UI/document smoke check; no external services."""
import hashlib
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import streamlit as st
from streamlit.testing.v1 import AppTest

import underwriting.configuration.cases as case_catalogue
import underwriting.documents.pdf as source_document
from underwriting.configuration.access import Settings
from underwriting.configuration.settings import get_config

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


with patch.object(st, "download_button", side_effect=capture_download), \
     patch("underwriting.ui.intake.settings_for_ui", return_value=Settings()), \
     patch("underwriting.ui.evidence.settings_for_ui", return_value=Settings()), \
     patch("underwriting.ui.beliefs.settings_for_ui", return_value=Settings()), \
     patch("underwriting.ui.er.settings_for_ui", return_value=Settings()), \
     patch("underwriting.ui.brb.settings_for_ui", return_value=Settings()), \
     patch("underwriting.intake.service.OpenAI", side_effect=AssertionError("Navigation must not call GPT")) as gpt_client:
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not app.exception, app.exception
    assert len(app.get("image")) == 0
    assert not downloads
    assert app.button(key=f"{prefix}:toggle_document").label == "View company document"
    assert app.button(key=f"{prefix}:extract_and_screen").disabled
    assert any("Missing configuration:" in item.value for item in app.caption)
    assert "**Outcome:** Not screened yet" in [item.value for item in app.markdown]
    schema = {"groups": get_config()["intake"]["field_groups"]}
    groups = {group["label"]: group for group in schema["groups"]}
    categories = {
        "Company": ["Company details", "Business operations"],
        "Submission": ["Submission details"], "Premises": ["Premises"],
        "Building": ["Building details"],
        "Cover and values": ["Requested cover", "Declared property values"],
        "Business interruption": ["Business interruption"],
        "Loss and insurance history": ["Loss and insurance history"],
        "Completeness": ["Submission completeness"],
    }
    assert app.radio(key=f"{prefix}:information_category").options == list(categories)
    for category, group_names in categories.items():
        app.radio(key=f"{prefix}:information_category").set_value(category).run()
        assert not app.exception, app.exception
        markdown = [item.value for item in app.tabs[0].markdown]
        assert f"**{category}**" in markdown
        visible_fields = [field for name in group_names for field in groups[name]["fields"]]
        expected_labels = {field["label"] for field in visible_fields}
        for group in groups.values():
            for field in group["fields"]:
                if field["type"] == "text":
                    assert (field["label"] in markdown) == (field["label"] in expected_labels)
        assert [item.value for item in app.tabs[0].text].count("—") == sum(field["type"] == "text" for field in visible_fields) + 1
        assert groups[group_names[0]]["empty_status"] in [item.value for item in app.tabs[0].caption]
        if category == "Loss and insurance history":
            loss_table = app.tabs[0].dataframe[0].value
            assert loss_table.empty
            assert list(loss_table.columns) == ["Loss date", "Description", "Amount paid", "Outstanding amount", "Remedial action"]
        else:
            assert len(app.tabs[0].dataframe) == 0
    screening = get_config()["screening"]
    for check in screening["checks"]:
        app.radio(key=f"{prefix}:screening_check").set_value(check["id"]).run()
        assert not app.exception
        markdown = [item.value for item in app.tabs[0].markdown]
        captions = [item.value for item in app.tabs[0].caption]
        assert f"**{check['id']} — {check['name']}**" in markdown
        assert check["question"] in captions
        assert markdown.count("**Result:** Not screened yet") == 1
        assert markdown.count("**Reason:**") == 1
        assert "**Source references:** —" in markdown
        assert not downloads
        assert app.button(key=f"{prefix}:extract_and_screen").disabled
    for expander in app.tabs[0].expander:
        assert not expander.proto.expanded
    submission_text = "\n".join(item.value for item in app.tabs[0].markdown)
    assert "£" not in submission_text and "Not provided" not in submission_text
    app.button(key=f"{prefix}:toggle_document").click().run()
    assert not app.exception
    assert [tab.label for tab in app.tabs] == ["Submission", "Assessment", "Decision"]
    assert all(len(tab.children) for tab in app.tabs)
    assert app.selectbox(key="selected_case_id").options == ["Reset workspace", "Northbridge Furniture Storage Ltd"]
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
               and not button.key.endswith((":previous_page", ":next_page", ":toggle_document", ":Propose beliefs", ":Calculate assessment")))
    # A second entry exists only in memory during this check, never in the catalogue.
    test_case = dict(cases[case_id], case_id="TEST-ONLY", company_name="Test-only company")
    with patch.object(case_catalogue, "load_cases", return_value={**cases, "TEST-ONLY": test_case}):
        app.run()
        app.selectbox(key="selected_case_id").set_value("TEST-ONLY").run()
        assert not app.exception
        assert app.session_state["case:TEST-ONLY:attribute"] == names[0]
        assert app.session_state["case:TEST-ONLY:pdf_page"] == 1
        assert app.radio(key="case:TEST-ONLY:information_category").value == "Company"
        assert app.radio(key="case:TEST-ONLY:screening_check").value == "SC01"
        assert not app.session_state["case:TEST-ONLY:document_visible"]
        assert len(app.get("image")) == 0
        app.button(key="case:TEST-ONLY:toggle_document").click().run()
        app.button(key="case:TEST-ONLY:next_page").click().run()
        app.selectbox(key="selected_case_id").set_value(case_id).run()
        assert app.session_state[f"{prefix}:attribute"] == names[-1]
        assert app.session_state[f"{prefix}:pdf_page"] == 1
        assert app.session_state[f"{prefix}:document_visible"]
        assert app.radio(key=f"{prefix}:information_category").value == "Completeness"
        assert app.radio(key=f"{prefix}:screening_check").value == "SC06"
    with patch.object(source_document, "read_source", side_effect=FileNotFoundError("Missing source")):
        app.run()
        assert not app.exception
        assert "Source PDF unavailable" in app.error[0].value
    with patch.object(source_document, "render_pages", side_effect=RuntimeError("Preview unavailable")):
        app.run()
        assert not app.exception
        assert "PDF previews unavailable" in app.error[0].value
        assert downloads[-1] == original
    app.button(key=f"{prefix}:toggle_document").click().run()
    assert len(app.get("image")) == 0
    assert not app.get("download_button")
    gpt_client.assert_not_called()

assert hashlib.sha256(source_document.read_source()).hexdigest() == original_hash
print("PASS: three-tab UI; eight information categories; six screening checks; case isolation; five attributes; four-page PDF navigation and exact download; visible document errors; zero GPT calls; unchanged source PDF.")
