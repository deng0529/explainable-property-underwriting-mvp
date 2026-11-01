"""Stage-specific rendering must not load, calculate, or mix panels."""
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from underwriting.configuration.decision_model import get_model
from underwriting.ui.navigation import activate_stage


class AssessmentViewTests(unittest.TestCase):
    def test_stage_panels_and_navigation_preserve_loaded_data(self):
        import streamlit as st
        model = get_model()
        assessments = [{"attribute_id": item["id"], "low": .4, "high": .4, "unknown": .2} for item in model["attributes"]]
        beliefs = {"run": {"result": {"assessments": assessments}}, "reviews": [], "stale": False}
        script = '''import streamlit as st
from underwriting.ui.assessment import render_workspace
from underwriting.configuration.decision_model import attribute_groups
attributes = {group: {a['id'] + ' ' + a['label']: a['definition'] for a in members} for group, members in attribute_groups().items()}
definitions = {name: definition for members in attributes.values() for name, definition in members.items()}
st.session_state.setdefault("selected_attribute", next(iter(definitions)))
render_workspace("test", attributes, definitions, "selected_attribute", {"run": {}}, st.session_state["beliefs"], st.session_state["er"], st.session_state.get("brb"))
'''
        def evidence_details(*args):
            st.text("Evidence facts and summary")
            st.text_area("Evidence review notes", key="test:evidence_notes", persist_state="session")

        with patch("underwriting.ui.assessment.attribute_details", side_effect=evidence_details), \
             patch("underwriting.ui.assessment.belief_details", side_effect=lambda *args: st.text("Belief confirmation")), \
             patch("underwriting.ui.assessment.er_results", side_effect=lambda *args: st.text("Fire and Security distributions")), \
             patch("underwriting.ui.assessment.brb_results", side_effect=lambda *args: st.text("Overall BRB distribution")), \
             patch("underwriting.intake.service.OpenAI", side_effect=AssertionError("Navigation must not call GPT")) as client, \
             patch("underwriting.persistence.intake.TursoStore.execute", side_effect=AssertionError("Navigation must not query the database")):
            app = AppTest.from_string(script)
            app.session_state["beliefs"] = beliefs
            app.session_state["er"] = {"run": {"groups": {}}, "stale": False}
            app.run()
            self.assertFalse(app.exception)
            self.assertIn("Evidence facts and summary", [item.value for item in app.text])
            self.assertNotIn("Low", [item.value for item in app.caption])
            self.assertNotIn("Belief confirmation", [item.value for item in app.text])
            self.assertNotIn("Fire and Security ER results", [item.value for item in app.subheader])
            self.assertEqual(len(app.button), 5)
            app.text_area(key="test:evidence_notes").input("Keep this review note").run()
            app.segmented_control[0].set_value("Beliefs").run()
            self.assertFalse(app.exception)
            self.assertIn("Belief confirmation", [item.value for item in app.text])
            self.assertIn("Low", [item.value for item in app.caption])
            self.assertNotIn("Evidence facts and summary", [item.value for item in app.text])
            self.assertNotIn("Fire and Security distributions", [item.value for item in app.text])
            self.assertEqual(len(app.button), 5)
            app.segmented_control[0].set_value("ER results").run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.button), 0)
            self.assertIn("Fire and Security distributions", [item.value for item in app.text])
            app.session_state["brb"] = {"run": {"status": "completed"}, "stale": False}
            app.session_state["test:assessment_stage"] = "BRB results"
            app.session_state["test:assessment_navigation_revision"] = 3
            app.run()
            self.assertEqual(app.segmented_control[0].value, "BRB results")
            self.assertIn("Overall BRB distribution", [item.value for item in app.text])
            self.assertNotIn("Fire and Security distributions", [item.value for item in app.text])
            self.assertNotIn("Belief confirmation", [item.value for item in app.text])
            app.segmented_control[0].set_value("Evidence").run()
            self.assertEqual(app.text_area(key="test:evidence_notes").value, "Keep this review note")
            # Force navigation while the selector still has the old browser value.
            app.session_state["test:assessment_stage"] = "Beliefs"
            app.session_state["test:assessment_navigation_revision"] = 1
            app.run()
            self.assertEqual(app.segmented_control[0].value, "Beliefs")
            self.assertIn("Belief confirmation", [item.value for item in app.text])
            app.session_state["test:assessment_stage"] = "ER results"
            app.session_state["test:assessment_navigation_revision"] = 2
            app.run()
            self.assertEqual(app.segmented_control[0].value, "ER results")
            self.assertIn("Fire and Security distributions", [item.value for item in app.text])
            client.assert_not_called()

    def test_programmatic_navigation_advances_revision(self):
        state = {}
        activate_stage(state, "case:A", "Evidence")
        activate_stage(state, "case:A", "Beliefs")
        self.assertEqual(state["case:A:assessment_stage"], "Beliefs")
        self.assertEqual(state["case:A:assessment_navigation_revision"], 2)
        self.assertNotIn("case:B:assessment_stage", state)
        activate_stage(state, "case:A", "BRB results")
        self.assertEqual(state["case:A:assessment_stage"], "BRB results")
