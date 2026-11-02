"""ER verification using an independent exact recursive mass combination."""
import copy
import itertools
import random
import unittest
from fractions import Fraction
from unittest.mock import patch

from underwriting.configuration.decision_model import get_model, effective_model_hash
from underwriting.models.er import combine_er, ERInputError, ENGINE_VERSION
from underwriting.models.er_adapter import aggregate_configured_group
from underwriting.persistence.er import ERStore
from underwriting.assessment.er_service import calculate_er
from underwriting.ui.er_presentation import group_presentation
import test_beliefs as belief_tests


def recursive_reference(matrix, weights):
    """Separate recursive revised-ER mass rule, with Fraction arithmetic.

    Keep assigned singleton masses, importance residual B, and incomplete residual T.
    Apply pairwise conflict normalisation, then remove B only at the end.
    No analytical products from the production implementation are used.
    """
    sources = []
    for row, weight in zip(matrix, weights):
        w = Fraction(str(weight))
        assigned = [w * Fraction(str(value)) for value in row]
        sources.append((assigned, 1 - w, w * (1 - sum(Fraction(str(value)) for value in row))))
    assigned, bar, tilde = sources[0]
    for other, other_bar, other_tilde in sources[1:]:
        h, other_h = bar + tilde, other_bar + other_tilde
        conflict = sum(assigned[i] * other[j] for i in range(len(assigned)) for j in range(len(other)) if i != j)
        k = 1 / (1 - conflict)
        new_assigned = [k * (assigned[i] * other[i] + assigned[i] * other_h + h * other[i]) for i in range(len(assigned))]
        new_bar = k * bar * other_bar
        new_tilde = k * (tilde * other_tilde + tilde * other_bar + bar * other_tilde)
        assigned, bar, tilde = new_assigned, new_bar, new_tilde
    return [float(value / (1 - bar)) for value in assigned], float(tilde / (1 - bar))


class ERCoreTests(unittest.TestCase):
    def assertReference(self, matrix, weights):
        result = combine_er(matrix, weights)
        expected, unknown = recursive_reference(matrix, weights)
        for actual, reference in zip(result["assigned_beliefs"], expected):
            self.assertAlmostEqual(actual, reference, places=12)
        self.assertAlmostEqual(result["unassigned_belief"], unknown, places=12)
        self.assertAlmostEqual(sum(result["assigned_beliefs"]) + result["unassigned_belief"], 1, places=12)
        return result

    def test_pure_low_high_unknown_and_single_active(self):
        for row in ([1, 0], [0, 1], [0, 0]):
            result = self.assertReference([row, row, row], [1 / 3] * 3)
            self.assertEqual(result["assigned_beliefs"], row)
            self.assertEqual(result["unassigned_belief"], 1 - sum(row))
        matrix = [[0.3, 0.2], [0.1, 0.7]]
        self.assertEqual(self.assertReference(matrix, [1, 0])["assigned_beliefs"], matrix[0])
        self.assertEqual(self.assertReference([matrix[0]], [1])["assigned_beliefs"], matrix[0])

    def test_mixed_complete_incomplete_equal_unequal_and_reorder(self):
        cases = [([[1, 0], [0, 1]], [.5, .5]),
                 ([[.4, .4], [.2, .3], [0, 0]], [.2, .3, .5]),
                 ([[.2, .8], [.7, .3]], [.25, .75]),
                 ([[1, 0], [0, 1], [0, 0]], [.5, .25, .25])]
        for matrix, weights in cases:
            expected = self.assertReference(matrix, weights)
            for permutation in itertools.permutations(range(len(matrix))):
                actual = self.assertReference([matrix[i] for i in permutation], [weights[i] for i in permutation])
                self.assertEqual(actual["assigned_beliefs"], expected["assigned_beliefs"])
        self.assertNotAlmostEqual(combine_er(cases[-1][0], cases[-1][1])["assigned_beliefs"][0], .5)

    def test_random_cases_and_more_than_two_grades(self):
        generator = random.Random(7821)
        for _ in range(50):
            count, grades = generator.randint(1, 8), generator.randint(1, 5)
            matrix = []
            for _ in range(count):
                parts = [generator.randint(0, 10) for _ in range(grades + 1)]
                total = sum(parts) or 1
                matrix.append([value / total for value in parts[:-1]])
            weights = [generator.randint(1, 9) for _ in range(count)]
            total = sum(weights)
            self.assertReference(matrix, [value / total for value in weights])

    def test_invalid_inputs_and_documented_cleanup(self):
        for matrix, weights in [([], []), ([[.7, .4]], [1]), ([[float("nan"), 0]], [1]),
                                ([[1, 0]], [float("inf")]), ([[1, 0], [0, 1]], [0, 0]),
                                ([[1, 0], [0, 1]], [1, 1]), ([[1, 0]], [-1]),
                                ([[1, 0], [0]], [.5, .5])]:
            with self.assertRaises(ERInputError):
                combine_er(matrix, weights)
        result = combine_er([[.7, .30000000000000004]], [1])
        self.assertTrue(result["diagnostics"]["cleanups"])
        with self.assertRaises(ERInputError):
            combine_er([[.7, .3000001]], [1])

    def test_config_adapter_dynamic_members_grades_unknown_and_weights(self):
        model = copy.deepcopy(get_model())
        beliefs = {identifier: {"low": .4, "high": .4, "unknown": .2} for identifier in [a["id"] for a in model["attributes"]]}
        result = aggregate_configured_group("fire", beliefs, model)
        self.assertEqual(len(result["effective_weights"]), 3)
        self.assertAlmostEqual(sum(result["effective_weights"].values()), 1)
        beliefs["F1"]["unknown"] = .3
        with self.assertRaisesRegex(ERInputError, "Unknown"):
            aggregate_configured_group("fire", beliefs, model)
        beliefs["F1"]["unknown"] = .2
        model["attributes"].append({"id": "X1", "label": "Test", "definition": "Test", "enabled": True})
        group = model["er"]["groups"]["fire"]
        group["members"].append("X1")
        group["importance_weights"]["X1"] = 2
        beliefs["X1"] = {"low": .1, "high": .2, "unknown": .7}
        self.assertEqual(len(aggregate_configured_group("fire", beliefs, model)["effective_weights"]), 4)
        group["members"].remove("X1")
        del group["importance_weights"]["X1"]
        model["attributes"].pop()
        self.assertEqual(len(aggregate_configured_group("fire", beliefs, model)["effective_weights"]), 3)
        model["grades"]["order"].append("medium")
        model["brb"]["enabled"] = False  # This test changes the ER frame, not the nine-rule BRB base.
        model["grades"]["definitions"]["medium"] = {"label": "Medium", "definition": "Test grade"}
        for definition in model["brb"]["inputs"].values():
            definition["states"].append("medium")
            definition["state_definitions"]["medium"] = "Test state"
        model["brb"]["output"]["states"].append("medium")
        for rule in model["brb"]["rules"]:
            rule["conclusion_beliefs"]["medium"] = 0
        for values in beliefs.values():
            values["medium"] = 0
        self.assertEqual(list(aggregate_configured_group("fire", beliefs, model)["assigned_beliefs"]), ["low", "high", "medium"])


class LocalERStore(ERStore):
    def __init__(self, other):
        self.settings, self.db = other.settings, other.db
        self.fail_save, self.fail_belief_save = False, False

    execute = belief_tests.LocalBeliefStore.execute


class ERIntegrationTests(unittest.TestCase):
    setUp = belief_tests.BeliefTests.setUp
    tearDown = belief_tests.BeliefTests.tearDown
    run_action = belief_tests.BeliefTests.run_action

    def test_current_confirmed_inputs_saved_provenance_and_no_gpt(self):
        from underwriting.assessment.belief_service import confirm_belief
        proposal = self.run_action()
        for item in proposal["result"]["assessments"]:
            confirm_belief(self.settings, self.case, self.store, proposal, item["attribute_id"], "accept")
        store = LocalERStore(self.store)
        with patch("underwriting.intake.service.OpenAI", side_effect=AssertionError("ER cannot call GPT")):
            result = calculate_er(self.settings, self.case, store)
            again = calculate_er(self.settings, self.case, store)
        self.assertEqual(set(result["groups"]), set(get_model()["er"]["groups"]))
        self.assertEqual(result["engine_version"], ENGINE_VERSION)
        for identifier, values in result["brb_inputs"].items():
            group = result["groups"][identifier]
            self.assertEqual(values, dict(group["assigned_beliefs"], unknown=group["unassigned_belief"]))
        self.assertEqual(result["upstream"]["commit"], "6744efbabeb27fe15dc52c516d86c929030b230e")
        self.assertNotEqual(result["run_id"], again["run_id"])
        self.assertEqual(result["input_belief_hash"], again["input_belief_hash"])
        confirm_belief(self.settings, self.case, self.store, proposal, "F1", "edit", .5, .3, "Mock adjustment")
        changed = calculate_er(self.settings, self.case, store)
        self.assertNotEqual(changed["input_belief_hash"], result["input_belief_hash"])
        self.assertEqual(store.latest_er(self.case, result["source_hash"], result["input_belief_hash"], result["decision_model"]["sha256"], ENGINE_VERSION)["run_id"], again["run_id"])

    def test_input_explanations_rounding_ties_and_no_mutation(self):
        model = copy.deepcopy(get_model())
        values = {item["id"]: {"low": .4, "high": .4, "unknown": .2} for item in model["attributes"]}
        values["F1"] = {"low": .8, "high": .1, "unknown": .1}
        values["F2"] = {"low": .1, "high": .8, "unknown": .1}
        values["F3"] = {"low": .1, "high": .1, "unknown": .8}
        run = {"decision_model_snapshot": model, "confirmed_beliefs": values,
               "groups": {name: aggregate_configured_group(name, values, model) for name in model["er"]["groups"]}}
        original = copy.deepcopy(run)
        rows, explanation = group_presentation(run, "fire")
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["Low concern"], "0.80")
        self.assertEqual(rows[0]["Weight"], "0.33")
        self.assertIn("F1 Combustible storage", explanation[0])
        self.assertIn("F2 Ignition controls", explanation[1])
        self.assertIn("F3 Fire protection", explanation[2])
        self.assertIn("(tied)", group_presentation(run, "security")[1][0])
        self.assertEqual(run, original)
        model["er"]["groups"]["fire"]["importance_weights"] = {"F1": 1, "F2": 0, "F3": 0}
        values["F1"] = {"low": 1, "high": 0, "unknown": 0}
        run["groups"]["fire"] = aggregate_configured_group("fire", values, model)
        explanation = group_presentation(run, "fire")[1]
        self.assertIn("no weighted input support", explanation[1])
        self.assertIn("no unassigned belief", explanation[2])

    def test_unconfirmed_inputs_and_outage_stop_without_gpt(self):
        self.run_action()
        store = LocalERStore(self.store)
        with self.assertRaisesRegex(ValueError, "human-confirmed"):
            calculate_er(self.settings, self.case, store)

    def test_explicit_ui_calculation_and_model_staleness(self):
        from underwriting.assessment.belief_service import confirm_belief
        from streamlit.testing.v1 import AppTest
        proposal = self.run_action()
        for item in proposal["result"]["assessments"]:
            confirm_belief(self.settings, self.case, self.store, proposal, item["attribute_id"], "accept")
        store = LocalERStore(self.store)
        script = """import streamlit as st
from underwriting.ui.er import er_controls, er_results
case = {"case_id": "TEST-BELIEF", "submission_version": "1", "document_path": "unused.pdf"}
view = er_controls(case, "test-er", st.session_state["belief_view"], st.columns(1)[0])
er_results(view)
"""
        with patch("underwriting.ui.er.settings_for_ui", return_value=self.settings), \
             patch("underwriting.ui.er.ERStore", return_value=store), \
             patch("underwriting.ui.er.document_hash", return_value="mock-source"), \
             patch("underwriting.intake.service.OpenAI", side_effect=AssertionError("ER must not call GPT")) as client:
            app = AppTest.from_string(script, default_timeout=30)
            app.session_state["belief_view"] = {"run": proposal, "reviews": store.belief_reviews(proposal["run_id"]), "stale": False}
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual(app.button(key="test-er:Calculate assessment").label, "Calculate Fire/Security ER")
            app.button(key="test-er:Calculate assessment").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state["test-er:assessment_stage"], "ER results")
            self.assertTrue(any(item.value == "**Fire ER**" for item in app.markdown))
            self.assertTrue(any(item.value == "**Security ER**" for item in app.markdown))
            self.assertEqual([item.label for item in app.metric], ["Low concern", "High concern", "Unknown"] * 2)
            self.assertTrue(all(item.value != "—" for item in app.metric))
            self.assertFalse(any("Overall BRB assessment" in item.value for item in app.markdown))
            self.assertEqual(len(app.table), 2)
            self.assertEqual(len(app.table[0].value), 3)
            self.assertEqual(len(app.table[1].value), 2)
            for item in app.text:
                if ": " in item.value:
                    self.assertRegex(item.value.split(": ")[-1], r"^\d\.\d{2}$")
            self.assertFalse(any("provenance" in item.label.lower() for item in app.expander))
            self.assertEqual(len(app.json), 2)
            self.assertTrue(any("nonlinearly" in item.value for item in app.caption))
            self.assertTrue(any("Most supported: Mixed assessment" in item.value for item in app.markdown))
            changed = copy.deepcopy(get_model())
            changed["er"]["groups"]["fire"]["importance_weights"]["F1"] = 2
            with patch("underwriting.ui.er.get_model", return_value=changed):
                app.run()
                self.assertTrue(any("stale" in item.value.lower() for item in app.warning))
            client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
