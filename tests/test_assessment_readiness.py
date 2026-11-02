"""Dependency reminders use loaded state; never fetch or generate automatically."""
import unittest
from unittest.mock import patch

from underwriting.ui.readiness import evidence_blocker, belief_blocker
from underwriting.configuration.decision_model import get_model
import test_er as er_tests


class ReadinessTests(unittest.TestCase):
    setUp = er_tests.ERIntegrationTests.setUp
    tearDown = er_tests.ERIntegrationTests.tearDown
    run_action = er_tests.ERIntegrationTests.run_action

    def test_evidence_required_and_reviewed(self):
        self.assertIn("Load attribute evidence", evidence_blocker({"run": None}))
        view = {"run": self.evidence, "reviews": []}
        self.assertIn("human evidence reviews", evidence_blocker(view))
        view["reviews"] = self.store.reviews(self.evidence["run_id"])
        self.assertEqual(evidence_blocker(view), "")
        view["stale"] = True
        self.assertIn("Reload", evidence_blocker(view))

    def test_beliefs_required_confirmed_current_and_not_revoked(self):
        from underwriting.assessment.belief_service import confirm_belief
        model = get_model()
        self.assertIn("Load proposed beliefs", belief_blocker({"run": None}, model))
        proposal = self.run_action()
        view = {"run": proposal, "reviews": []}
        self.assertIn("F1", belief_blocker(view, model))
        for item in proposal["result"]["assessments"]:
            confirm_belief(self.settings, self.case, self.store, proposal, item["attribute_id"], "accept")
        view["reviews"] = self.store.belief_reviews(proposal["run_id"])
        self.assertEqual(belief_blocker(view, model), "")
        view["reviews"].append(dict(view["reviews"][0], confirmed=False))
        self.assertIn("confirm the beliefs", belief_blocker(view, model))
        view["stale"] = True
        self.assertIn("Evidence has changed", belief_blocker(view, model))
        view["prerequisite"] = "First load evidence."
        self.assertEqual(belief_blocker(view, model), "First load evidence.")

    def test_ui_blocks_downstream_actions_without_database_or_gpt_requests(self):
        from streamlit.testing.v1 import AppTest
        script = '''import streamlit as st
from underwriting.ui.beliefs import belief_controls
from underwriting.ui.er import er_controls
case = {"case_id": "TEST-BELIEF", "submission_version": "1", "document_path": "unused.pdf"}
slots = st.columns(2)
beliefs = belief_controls(case, "gate", {"run": None, "reviews": []}, slots[0])
er_controls(case, "gate", beliefs, slots[1])
'''
        with patch("underwriting.ui.beliefs.settings_for_ui", return_value=self.settings), \
             patch("underwriting.ui.er.settings_for_ui", return_value=self.settings), \
             patch("underwriting.ui.beliefs.document_hash", return_value="mock-source"), \
             patch("underwriting.ui.er.document_hash", return_value="mock-source"), \
             patch("underwriting.ui.beliefs.BeliefStore", return_value=self.store), \
             patch("underwriting.ui.beliefs.propose_beliefs") as propose, \
             patch("underwriting.ui.er.calculate_er") as calculate, \
             patch.object(self.store, "execute", side_effect=AssertionError("No automatic DB request")):
            app = AppTest.from_string(script).run()
            self.assertFalse(app.exception)
            self.assertFalse(app.button(key="gate:Propose beliefs").disabled)
            self.assertFalse(app.button(key="gate:Calculate assessment").disabled)
            self.assertFalse(any("First click Load attribute evidence" in item.value for item in app.caption))
            app.button(key="gate:Propose beliefs").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any("First click Load attribute evidence" in item.value for item in app.warning))
            app.button(key="gate:Calculate assessment").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any("Load attribute evidence" in item.value for item in app.warning))
            app.run()
            self.assertFalse(app.exception)
            propose.assert_not_called()
            calculate.assert_not_called()

    def test_reset_clears_only_active_tab_and_preserves_company(self):
        from underwriting.ui.workspace import company_changed, RESET_OPTION, workspace_scope
        state = {"selected_case_id": "TEST-BELIEF", "current_case_id": "TEST-BELIEF", "active_workspace": "Submission"}
        with workspace_scope(state, "Submission"):
            state["intake"] = {"saved": "old"}
        with workspace_scope(state, "Assessment"):
            state["evidence"] = {"run": "old"}
            state["random-run:review"] = True
        state["selected_case_id"] = RESET_OPTION
        company_changed(state)
        self.assertNotIn("intake", state)
        self.assertIn("evidence", state)
        self.assertIn("random-run:review", state)
        self.assertEqual(state["selected_case_id"], "TEST-BELIEF")
        state["active_workspace"] = "Assessment"
        state["intake"] = {"saved": "reloaded"}
        state["selected_case_id"] = RESET_OPTION
        company_changed(state)
        self.assertNotIn("evidence", state)
        self.assertNotIn("random-run:review", state)
        self.assertEqual(state["intake"], {"saved": "reloaded"})

    def test_full_app_reset_returns_to_empty_without_loading_saved_records(self):
        from streamlit.testing.v1 import AppTest
        from underwriting.configuration.access import Settings
        from underwriting.configuration.settings import ROOT
        with patch("underwriting.ui.intake.settings_for_ui", return_value=Settings()), \
             patch("underwriting.intake.service.OpenAI", side_effect=AssertionError("No GPT")) as client, \
             patch("underwriting.persistence.intake.TursoStore.execute", side_effect=AssertionError("No database access")):
            app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
            self.assertFalse(app.exception)
            company = app.selectbox(key="selected_case_id").value
            self.assertFalse(any("Model settings" in item.label for item in app.expander))
            prefix = "case:" + company
            self.assertEqual([button.label for button in next(tab for tab in app.tabs if tab.label == "Assessment").button][:4], [
                "Load attribute evidence", "Load proposed beliefs", "Load saved Fire/Security results", "Load saved overall assessment"])
            self.assertFalse(app.button(key=prefix + ":calculate_brb").disabled)
            self.assertFalse(any("Calculate current Fire / Security ER results first" in item.value for item in next(tab for tab in app.tabs if tab.label == "Assessment").caption))
            app.radio(key=prefix + ":information_category").set_value("Completeness").run()
            app.button(key=prefix + ":toggle_document").click().run()
            self.assertFalse(app.exception)
            app.session_state[prefix + ":attribute"] = "F2 Ignition controls"
            from underwriting.ui.workspace import RESET_OPTION
            self.assertEqual(app.selectbox(key="selected_case_id").options[0], "Reset workspace")
            app.selectbox(key="selected_case_id").set_value(RESET_OPTION).run()
            self.assertFalse(app.exception)
            self.assertEqual(app.selectbox(key="selected_case_id").value, company)
            self.assertEqual(app.radio(key=prefix + ":information_category").value, "Company")
            self.assertFalse(app.session_state[prefix + ":document_visible"])
            self.assertEqual(len(app.get("image")), 0)
            self.assertEqual(app.session_state[prefix + ":attribute"], "F2 Ignition controls")
            self.assertTrue(any("Submission reset" in item.value for item in app.success))
            self.assertTrue(any("Not loaded yet" in item.value for item in app.caption))
            app.session_state[prefix + ":submission_section"] = "Screening"
            app.run()
            self.assertTrue(any("**Outcome:** Not screened yet" == item.value for item in app.markdown))
            app.session_state[prefix + ":submission_section"] = "Basic information"
            app.run()
            app.radio(key=prefix + ":information_category").set_value("Premises").run()
            app.session_state["active_workspace"] = "Assessment"
            app.run()
            app.selectbox(key="selected_case_id").set_value(RESET_OPTION).run()
            self.assertFalse(app.exception)
            self.assertEqual(app.radio(key=prefix + ":information_category").value, "Premises")
            self.assertEqual(app.session_state[prefix + ":attribute"], "F1 Combustible storage")
            self.assertTrue(any("Assessment reset" in item.value for item in app.success))
            self.assertFalse(app.button(key=prefix + ":Propose beliefs").disabled)
            self.assertFalse(app.button(key=prefix + ":Calculate assessment").disabled)
            app.button(key=prefix + ":Propose beliefs").click().run()
            self.assertTrue(any("First click Load attribute evidence" in item.value for item in app.warning))
            app.button(key=prefix + ":Calculate assessment").click().run()
            self.assertTrue(any("Load attribute evidence" in item.value for item in app.warning))
            client.assert_not_called()
