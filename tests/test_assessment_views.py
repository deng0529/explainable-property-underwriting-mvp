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
            app = AppTest.from_string(script, default_timeout=30)
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
            app.session_state["test:assessment_stage"] = "Beliefs"
            app.run()
            self.assertFalse(app.exception)
            self.assertIn("Belief confirmation", [item.value for item in app.text])
            self.assertNotIn("Low", [item.value for item in app.caption])
            self.assertNotIn("Evidence facts and summary", [item.value for item in app.text])
            self.assertNotIn("Fire and Security distributions", [item.value for item in app.text])
            self.assertEqual(len(app.button), 5)
            app.session_state["test:assessment_stage"] = "ER results"
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.button), 0)
            self.assertIn("Fire and Security distributions", [item.value for item in app.text])
            app.session_state["brb"] = {"run": {"status": "completed"}, "stale": False}
            app.session_state["test:assessment_stage"] = "BRB results"
            app.session_state["test:assessment_navigation_revision"] = 3
            app.run()
            self.assertEqual(len(app.segmented_control), 0)
            self.assertIn("Overall BRB distribution", [item.value for item in app.text])
            self.assertNotIn("Fire and Security distributions", [item.value for item in app.text])
            self.assertNotIn("Belief confirmation", [item.value for item in app.text])
            app.session_state["test:assessment_stage"] = "Evidence"
            app.run()
            self.assertEqual(app.text_area(key="test:evidence_notes").value, "Keep this review note")
            # Force navigation while the selector still has the old browser value.
            app.session_state["test:assessment_stage"] = "Beliefs"
            app.session_state["test:assessment_navigation_revision"] = 1
            app.run()
            self.assertEqual(len(app.segmented_control), 0)
            self.assertIn("Belief confirmation", [item.value for item in app.text])
            app.session_state["test:assessment_stage"] = "ER results"
            app.session_state["test:assessment_navigation_revision"] = 2
            app.run()
            self.assertEqual(len(app.segmented_control), 0)
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

    def test_four_top_buttons_select_one_stage_without_side_effects(self):
        script = '''import streamlit as st
from underwriting.ui.navigation import stage_button
prefix = st.selectbox("Company", ["case:A", "case:B"])
with st.container(key="assessment_actions"):
    columns = st.columns(4)
    for column, label, stage in zip(columns,
        ["Load attribute evidence", "Load proposed beliefs", "Calculate Fire/Security ER", "Calculate overall BRB assessment"],
        ["Evidence", "Beliefs", "ER results", "BRB results"]):
        stage_button(column, label, prefix, stage, key=prefix + stage)
st.text(st.session_state.get(prefix + ":assessment_stage", "Evidence"))
st.text_input("Review draft", key=prefix + ":draft", persist_state="session")
'''
        with patch("underwriting.intake.service.OpenAI") as paid, \
             patch("underwriting.persistence.intake.TursoStore.execute") as database, \
             patch("underwriting.ui.er.calculate_er") as er, \
             patch("underwriting.ui.brb.calculate_brb") as brb:
            app = AppTest.from_string(script, default_timeout=30).run()
            for index, stage in enumerate(["Evidence", "Beliefs", "ER results", "BRB results"]):
                app.button[index].click().run()
                self.assertFalse(app.exception)
                self.assertEqual([button.proto.type for button in app.button],
                                 ["primary" if i == index else "secondary" for i in range(4)])
                self.assertEqual(app.text[0].value, stage)
                app.text_input[0].input("Preserve review draft").run()
                self.assertEqual(app.text[0].value, stage)
                self.assertEqual(len(app.segmented_control), 0)
            app.selectbox[0].set_value("case:B").run()
            self.assertEqual(app.text[0].value, "Evidence")
            app.selectbox[0].set_value("case:A").run()
            self.assertEqual(app.text[0].value, "BRB results")
            self.assertEqual(app.text_input[0].value, "Preserve review draft")
            for operation in (paid, database, er, brb):
                operation.assert_not_called()

    def test_result_summaries_precede_inputs_and_keep_residual_separate(self):
        import copy
        from underwriting.models.er_adapter import aggregate_configured_group
        from underwriting.models.brb import infer_brb
        model = get_model()
        values = {item["id"]: {"low": .4, "high": .4, "unknown": .2} for item in model["attributes"]}
        groups = {name: aggregate_configured_group(name, values, model) for name in model["er"]["groups"]}
        er = {"decision_model_snapshot": model, "confirmed_beliefs": values, "groups": groups, "finished_at": "mock timestamp"}
        result = infer_brb({name: dict(group["assigned_beliefs"], unknown=group["unassigned_belief"]) for name, group in groups.items()}, model["brb"])
        brb = {"result": result, "decision_model_snapshot": model}
        original = copy.deepcopy((er, brb))
        script = '''import streamlit as st
from underwriting.ui.er import er_results
from underwriting.ui.brb import brb_results
if st.session_state["stage"] == "ER":
    er_results({"run": st.session_state["er"], "stale": False})
else:
    brb_results({"run": st.session_state["brb"], "stale": False})
'''
        with patch("underwriting.persistence.intake.TursoStore.execute") as database, \
             patch("underwriting.intake.service.OpenAI") as paid:
            app = AppTest.from_string(script, default_timeout=30)
            app.session_state["stage"] = "ER"
            app.session_state["er"] = er
            app.session_state["brb"] = brb
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual([m.label for m in app.metric], ["Low concern", "High concern", "Unknown"] * 2)
            nodes = [node.type for node in app.main]
            self.assertLess(nodes.index("metric"), nodes.index("table"))
            self.assertEqual(list(app.table[0].value.columns), ["Attribute", "Low", "High", "Unknown", "Effective weight"])
            app.session_state["stage"] = "BRB"
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual([m.label for m in app.metric], ["Low concern", "High concern", "Unknown"])
            nodes = [node.type for node in app.main]
            self.assertLess(nodes.index("metric"), nodes.index("table"))
            self.assertEqual(list(app.table[0].value.columns), ["Group", "Low", "High", "Unknown"])
            partial = copy.deepcopy(brb)
            partial["result"]["state_beliefs"] = {"low": .25, "high": .25, "unknown": .25}
            partial["result"]["residual_unassigned"] = .25
            app.session_state["brb"] = partial
            app.run()
            self.assertFalse(app.exception)
            self.assertTrue(any("Residual unassigned belief: 0.25" == item.value for item in app.text))
            self.assertEqual(app.metric[2].value, "0.25")
            self.assertEqual((er, brb), original)
            database.assert_not_called()
            paid.assert_not_called()
