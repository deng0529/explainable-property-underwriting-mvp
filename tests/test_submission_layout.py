"""One focused Submission presentation check; services are blocked or mocked."""
import copy
from dataclasses import replace
from html.parser import HTMLParser
from pathlib import Path
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from underwriting.configuration.access import Settings
from underwriting.configuration.cases import load_cases
from underwriting.configuration.settings import ROOT, get_config, configuration_metadata
from underwriting.documents.pdf import read_source
from underwriting.ui.submission_tables import table_html
import test_intake as fixtures


class TableReader(HTMLParser):
    def __init__(self, markup):
        super().__init__(convert_charrefs=True)
        self.headers, self.rows, self.cells = [], [], []
        self.cell, self.tag = None, None
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.cells = []
        elif tag in {"th", "td"}:
            self.cell, self.tag = [], tag

    def handle_data(self, text):
        if self.cell is not None:
            self.cell.append(text)

    def handle_endtag(self, tag):
        if tag in {"th", "td"} and self.cell is not None:
            value = "".join(self.cell)
            (self.headers if tag == "th" else self.cells).append(value)
            self.cell, self.tag = None, None
        elif tag == "tr" and self.cells:
            self.rows.append(self.cells)


def rendered_tables(app):
    return [TableReader(item.value) for item in app.html if "<table " in item.value]


class SubmissionLayoutTests(unittest.TestCase):
    def test_submission_empty_populated_navigation_sources_pdf_and_scoped_layout(self):
        configuration = get_config()
        cases = load_cases()
        case_id = "NB-2026-001"
        prefix = "case:" + case_id
        other = dict(cases[case_id], case_id="TEST-LAYOUT", company_name="Mock second company")
        categories = configuration["intake"]["display_groups"]
        groups = {group["label"]: group for group in configuration["intake"]["field_groups"]}
        settings = Settings("mock-key", "mock-model", "https://test.invalid", "mock-token", "local_owner")
        state = {"saved": None, "readonly": False}

        def view(case, query=False):
            return {"settings": replace(settings, access_mode="public_readonly") if state["readonly"] else settings,
                    "saved": state["saved"] if case["case_id"] == case_id else None,
                    "latest_attempt": None, "stale": False, "error": None, "pending": [],
                    "source_hash": "mock-source", "store": None}

        downloaded = []
        import streamlit as st
        real_download = st.download_button
        def download(*args, **kwargs):
            if args and args[0]=="Download original PDF":
                downloaded.append(kwargs["data"])
            return real_download(*args, **kwargs)

        with patch("underwriting.ui.intake.load_view", side_effect=view), \
             patch("underwriting.configuration.cases.load_cases", return_value={**cases, other["case_id"]: other}), \
             patch("underwriting.configuration.access.local_server", return_value=True), \
             patch("underwriting.ui.evidence.settings_for_ui", return_value=Settings()), \
             patch("underwriting.ui.beliefs.settings_for_ui", return_value=Settings()), \
             patch("underwriting.ui.er.settings_for_ui", return_value=Settings()), \
             patch("underwriting.ui.brb.settings_for_ui", return_value=Settings()), \
             patch("underwriting.persistence.intake.TursoStore.execute", side_effect=AssertionError("Navigation must not access/write the database")) as database, \
             patch("underwriting.intake.service.OpenAI", side_effect=AssertionError("No GPT requests")) as gpt, \
             patch("underwriting.ui.intake.run_intake", side_effect=AssertionError("No extraction on navigation")) as extract, \
             patch.object(st, "download_button", side_effect=download):
            app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
            self.assertFalse(app.exception)
            self.assertEqual([tab.label for tab in app.tabs if tab.label in {"Submission", "Assessment", "Decision"}], ["Submission", "Assessment", "Decision"])
            self.assertEqual([tab.label for tab in app.tabs if tab.label in {"Basic information", "Screening"}], ["Basic information", "Screening"])
            self.assertEqual(app.session_state[prefix + ":submission_section"], "Basic information")
            self.assertEqual(len(app.get("image")), 0)

            for category, group_names in categories.items():
                app.radio(key=prefix + ":information_category").set_value(category).run()
                self.assertFalse(app.exception)
                table = rendered_tables(app)[0]
                fields = [(field, groups[group_name]["empty_status"]) for group_name in group_names for field in groups[group_name]["fields"]]
                self.assertEqual(table.headers, ["Field", "Value", "Status"])
                self.assertEqual(table.rows, [[field["label"], "—", status] for field, status in fields])
                self.assertFalse(any(widget.label == "Screening check" for widget in app.radio))
                self.assertTrue(all(not item.proto.expanded for item in app.expander if item.label == "Sources and details"))
                if category == "Loss and insurance history":
                    self.assertEqual(rendered_tables(app)[1].headers, ["Loss date", "Description", "Amount paid", "Outstanding amount", "Remedial action"])
                    self.assertEqual(rendered_tables(app)[1].rows, [])

            app.session_state[prefix + ":submission_section"] = "Screening"
            app.run()
            for check in configuration["screening"]["checks"]:
                app.radio(key=prefix + ":screening_check").set_value(check["id"]).run()
                self.assertFalse(app.exception)
                self.assertEqual(rendered_tables(app)[0].rows, [
                    ["Check ID and name", check["id"] + " — " + check["name"]],
                    ["Screening question", check["question"]], ["Result", "Not screened yet"], ["Reason", "—"]])
                self.assertIn("**Outcome:** Not screened yet", [item.value for item in app.markdown])
                self.assertFalse(any(widget.label == "Information category" for widget in app.radio))

            data = fixtures.response_data()
            long_value = "Long value <script>unsafe()</script> " + "storage detail " * 120 + "END OF LONG VALUE"
            field = next(item for item in data["basic_information"] if item["field_id"] == "business_description")
            field.update(value=long_value, note="Full note retained", evidence=[dict(fixtures.EVIDENCE, quote="Exact <b>quotation</b> retained")])
            losses = next(item for item in data["basic_information"] if item["field_id"] == "previous_losses")
            losses.update(status="found", unit="GBP", value=[{"loss_date": "2024-06", "description": "Mock loss " + long_value,
                          "amount_paid": 0, "outstanding_amount": None, "remedial_action": "Verify source", "evidence": [fixtures.EVIDENCE]}])
            data["screening_checks"][-1]["information_required"] = "Mock clarification required"
            data["non_blocking_review_flags"] = [{"issue": "Mock review flag", "information_required": "Mock broker question", "evidence": [fixtures.EVIDENCE]}]
            saved = {"case_id":case_id,"run_id": "mock-layout-run", "status": "completed", "outcome": "Proceed to assessment", "finished_at": "2026-11-02T09:00:00Z",
                     "configuration": configuration_metadata(), "configuration_snapshot": configuration, "model": "mock-model", "schema_version": "mock-schema", "policy_version": "mock-policy",
                     "source_version": "1.0", "source_hash": "mock-source", "prompts": {}, "result": data, "validation_issues": []}
            original = copy.deepcopy(saved)
            state["saved"] = saved
            app.session_state[prefix + ":submission_section"] = "Basic information"
            app.run()
            for category in categories:
                app.radio(key=prefix + ":information_category").set_value(category).run()
                self.assertFalse(app.exception)
                table = rendered_tables(app)[0]
                if category == "Company":
                    self.assertTrue(any(row[1] == long_value for row in table.rows))
                    markup = "\n".join(item.value for item in app.html)
                    self.assertIn("&lt;script&gt;", markup)
                    self.assertNotIn("<script>", markup)
                    self.assertIn("END OF LONG VALUE", markup)
                    self.assertIn("Exact <b>quotation</b> retained", [item.value for item in app.text])
                if category == "Loss and insurance history":
                    self.assertEqual(rendered_tables(app)[1].rows[0][2:4], ["0", "—"])
                    self.assertTrue(any(item.value == "Currency: GBP" for item in app.caption))
            note_key = "case:" + case_id + ":submission_review:mock-layout-run:basic_notes"
            app.text_area(key=note_key).input("Keep unsaved basic review note").run()
            app.session_state[prefix + ":submission_section"] = "Screening"
            app.run()
            for check in configuration["screening"]["checks"]:
                app.radio(key=prefix + ":screening_check").set_value(check["id"]).run()
                self.assertEqual(rendered_tables(app)[0].rows[2], ["Result", "Pass"])
            self.assertIn(["Information required", "Mock clarification required"], rendered_tables(app)[0].rows)
            self.assertIn("Mock review flag", [item.value for item in app.text])
            self.assertEqual(app.session_state[note_key], "Keep unsaved basic review note")
            app.session_state[prefix + ":submission_section"] = "Basic information"
            app.run()
            self.assertEqual(app.text_area(key=note_key).value, "Keep unsaved basic review note")
            app.selectbox(key="selected_case_id").set_value(other["case_id"]).run()
            self.assertFalse(app.exception)
            self.assertTrue(all(row[1] == "—" for row in rendered_tables(app)[0].rows))
            app.selectbox(key="selected_case_id").set_value(case_id).run()
            self.assertEqual(state["saved"], original)
            state["readonly"] = True
            app.run()
            self.assertTrue(app.button(key="case:" + case_id + ":submission_review:mock-layout-run:save").disabled)
            app.button(key=prefix + ":toggle_document").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(downloaded[-1], read_source(cases[case_id]["document_path"]))
            self.assertEqual(len(app.get("image")), 1)
            app.button(key=prefix + ":toggle_document").click().run()
            self.assertEqual(len(app.get("image")), 0)
            database.assert_not_called()
            gpt.assert_not_called()
            extract.assert_not_called()

        css = (ROOT / "underwriting/ui/styles/submission.css").read_text(encoding="utf-8")
        self.assertIn(".st-key-active_workspace [role=\"tab\"]", css)
        self.assertIn("font-size: 18px", css)
        self.assertIn("font-size: 16px", css)
        self.assertIn("max-width: 900px", css)
        self.assertIn("flex-direction: column", css)
        self.assertIn("overflow-wrap: anywhere", css)
        self.assertNotIn("height:", css.replace("line-height:", ""))
        self.assertIn("&lt;img", table_html(["Field", "Value", "Status"], [["<img src=x>", '"<script>x</script>"', None]], label='"unsafe"'))
