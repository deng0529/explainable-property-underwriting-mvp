"""Pure numerical and mock-persistence BRB checks; no paid or hosted calls."""
import copy
import itertools
import json
import math
import unittest
from dataclasses import replace
from unittest.mock import patch

from underwriting.models.brb import infer_brb
from underwriting.assessment.brb_service import calculate_brb, er_result_hash, rule_configuration_hash
from underwriting.persistence.brb import BRBStore
from underwriting.configuration.decision_model import get_model, effective_er_model_hash
from underwriting.assessment.er_service import calculate_er
from underwriting.ui.brb_presentation import input_support_summary, strongest_rule_summary, activation_summary
import test_er as er_tests
import test_beliefs as belief_tests


class BRBCoreTests(unittest.TestCase):
    def test_activation_matches_inspected_upstream_equations_with_documented_epsilon(self):
        # Independent scalar reference to compute_activation_weights in the verified
        # upstream inference.py blob e5b2cce81336709f5914d772da9bbcf470fabd8d.
        for weights in ({"fire": 1, "security": 1}, {"fire": 2, "security": .5}, {"fire": 1, "security": 0}):
            config = self.config()
            config["input_attribute_weights"] = weights
            inputs = self.inputs()
            relative = {key: value / max(weights.values()) for key, value in weights.items()}
            strengths = [rule["rule_weight"] * math.prod(inputs[key][state] ** relative[key] for key, state in rule["antecedents"].items()) for rule in config["rules"]]
            upstream = [value / (sum(strengths) + 1e-12) for value in strengths]
            ours = infer_brb(inputs, config)
            for row, reference in zip(ours["rules"], upstream):
                self.assertAlmostEqual(row["activation_weight"], reference, delta=2e-12)
            self.assertAlmostEqual(sum(row["activation_weight"] for row in ours["rules"]), 1, places=14)

    def test_input_and_rule_explanations_use_actual_numbers(self):
        config = self.config()
        result = infer_brb(self.inputs(), config)
        original = copy.deepcopy(result)
        lines = input_support_summary(result, config)
        self.assertIn("Security", lines[0])
        self.assertIn("Fire", lines[1])
        self.assertIn("tied", lines[2])
        self.assertIn("R04 (42.00%)", strongest_rule_summary(result))
        self.assertIn("High 0.90", strongest_rule_summary(result))
        self.assertIn("All 9 rules are active", activation_summary(result))
        pure = {name: {"low": 1, "high": 0, "unknown": 0} for name in config["inputs"]}
        self.assertIn("1 of 9", activation_summary(infer_brb(pure, config)))
        self.assertEqual(result, original)
    def config(self):
        return copy.deepcopy(get_model()["brb"])

    def inputs(self):
        return {"fire": {"low": .3, "high": .6, "unknown": .1}, "security": {"low": .7, "high": .2, "unknown": .1}}

    def test_all_nine_pure_inputs_reproduce_configured_conclusions(self):
        config = self.config()
        for rule in config["rules"]:
            inputs = {identifier: {state: float(state == rule["antecedents"][identifier]) for state in definition["states"]}
                      for identifier, definition in config["inputs"].items()}
            result = infer_brb(inputs, config)
            for state, expected in rule["conclusion_beliefs"].items():
                self.assertAlmostEqual(result["state_beliefs"][state], expected, places=12)
            self.assertAlmostEqual(result["residual_unassigned"], 0, places=12)
        self.assertEqual(infer_brb({name: {"low": 0, "high": 0, "unknown": 1} for name in config["inputs"]}, config)["state_beliefs"]["unknown"], 1)

    def test_supplied_strengths_and_independent_recursive_er_reference(self):
        config, inputs = self.config(), self.inputs()
        original = copy.deepcopy(inputs)
        result = infer_brb(inputs, config)
        expected = [.21, .06, .03, .42, .12, .06, .07, .02, .01]
        for row, strength in zip(result["rules"], expected):
            self.assertAlmostEqual(row["strength"], strength, places=14)
            self.assertAlmostEqual(row["activation_weight"], strength, places=14)
        matrix = [list(rule["conclusion_beliefs"].values()) for rule in config["rules"]]
        assigned, residual = er_tests.recursive_reference(matrix, expected)
        for actual, reference in zip(result["state_beliefs"].values(), assigned):
            self.assertAlmostEqual(actual, reference, places=12)
        self.assertAlmostEqual(result["residual_unassigned"], residual, places=12)
        self.assertEqual(inputs, original)
        print("BRB independent-reference example:", json.dumps({"distribution": result["state_beliefs"], "residual_unassigned": result["residual_unassigned"]}))

    def test_unequal_and_zero_attribute_weights_use_max_normalisation(self):
        config = self.config()
        config["input_attribute_weights"] = {"fire": 2, "security": 1}
        result = infer_brb(self.inputs(), config)
        self.assertEqual(result["relative_attribute_weights"], {"fire": 1, "security": .5})
        self.assertAlmostEqual(result["rules"][0]["strength"], .3 * .7 ** .5)
        config["input_attribute_weights"] = {"fire": 1, "security": 0}
        inputs = {"fire": {"low": 1, "high": 0, "unknown": 0}, "security": {"low": 0, "high": 0, "unknown": 1}}
        result = infer_brb(inputs, config)
        self.assertEqual([round(rule["activation_weight"], 12) for rule in result["rules"][:3]], [round(1/3, 12)] * 3)
        self.assertEqual(result["rules"][0]["matching_factors"]["security"], 1)

    def test_invalid_inputs_rules_weights_and_no_match(self):
        for value in (-.1, float("nan"), float("inf"), True):
            inputs = self.inputs()
            inputs["fire"]["low"] = value
            with self.assertRaises(ValueError):
                infer_brb(inputs, self.config())
        for change in ("all_zero", "duplicate", "missing", "bad_conclusion"):
            config = self.config()
            if change == "all_zero":
                config["input_attribute_weights"] = {"fire": 0, "security": 0}
            elif change == "duplicate":
                config["rules"][1]["id"] = config["rules"][0]["id"]
            elif change == "missing":
                config["rules"].pop()
            else:
                config["rules"][0]["conclusion_beliefs"]["low"] = 2
            with self.assertRaises(ValueError):
                infer_brb(self.inputs(), config)
        config = self.config()
        for rule in config["rules"][1:]:
            rule["rule_weight"] = 0
        inputs = {name: {"low": 0, "high": 1, "unknown": 0} for name in config["inputs"]}
        result = infer_brb(inputs, config)
        self.assertEqual(result["status"], "no_matching_rules")
        self.assertIsNone(result["state_beliefs"])
        self.assertIsNone(result["residual_unassigned"])

    def test_display_threshold_does_not_discard_activations(self):
        config = self.config()
        original = infer_brb(self.inputs(), config)
        config["active_rule_display_tolerance"] = .5
        result = infer_brb(self.inputs(), config)
        self.assertEqual(result["state_beliefs"], original["state_beliefs"])
        self.assertEqual(len([rule for rule in result["rules"] if rule["activation_weight"] > 0]), 9)

    def test_small_matching_strength_underflow_is_not_no_matching_rules(self):
        config = self.config()
        for rule in config["rules"][1:]:
            rule["rule_weight"] = 0
        inputs = {name: {"low": 1e-200, "high": 1.0, "unknown": 0} for name in config["inputs"]}
        result = infer_brb(inputs, config)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["rules"][0]["activation_weight"], 1)
        self.assertEqual(result["numerical_underflow_rules"], ["R01"])
        self.assertEqual(result["state_beliefs"], config["rules"][0]["conclusion_beliefs"])

    def test_directional_grid_reports_counterexamples_without_altering_rules(self):
        config = self.config()
        counterexamples, comparisons = [], 0
        for uf, us in itertools.product([0, .1, .3, .6], repeat=2):
            for changed in ("fire", "security"):
                for fixed_fraction in (0, .25, .5, .75, 1):
                    fixed = "security" if changed == "fire" else "fire"
                    unknown = {"fire": uf, "security": us}
                    previous = None
                    for fraction in (0, .25, .5, .75, 1):
                        fractions = {changed: fraction, fixed: fixed_fraction}
                        inputs = {name: {"low": (1 - unknown[name]) * (1 - fractions[name]), "high": (1 - unknown[name]) * fractions[name], "unknown": unknown[name]} for name in config["inputs"]}
                        high = infer_brb(inputs, config)["state_beliefs"]["high"]
                        if previous:
                            comparisons += 1
                            if high < previous[0] - 1e-12:
                                counterexamples.append({"changed": changed, "previous_inputs": previous[1], "inputs": inputs, "previous_high": previous[0], "high": high})
                        previous = high, inputs
        print("BRB directional grid:", json.dumps({"comparisons": comparisons, "counterexamples": len(counterexamples), "first_counterexample": counterexamples[0] if counterexamples else None}))


class LocalBRBStore(BRBStore):
    def __init__(self, other):
        self.settings, self.db = other.settings, other.db
        self.fail_save, self.fail_belief_save = False, False

    execute = belief_tests.LocalBeliefStore.execute


class BRBIntegrationTests(unittest.TestCase):
    setUp = er_tests.ERIntegrationTests.setUp
    tearDown = er_tests.ERIntegrationTests.tearDown
    run_action = er_tests.ERIntegrationTests.run_action

    def prepare_er(self):
        from underwriting.assessment.belief_service import confirm_belief
        proposal = self.run_action()
        for item in proposal["result"]["assessments"]:
            confirm_belief(self.settings, self.case, self.store, proposal, item["attribute_id"], "accept")
        store = LocalBRBStore(self.store)
        return store, calculate_er(self.settings, self.case, store)

    def test_saved_reuse_rules_change_and_history_without_gpt_or_er_recalculation(self):
        store, er = self.prepare_er()
        with patch("underwriting.intake.service.OpenAI", side_effect=AssertionError("No GPT calls")), \
             patch("underwriting.assessment.er_service.aggregate_configured_group", side_effect=AssertionError("No upstream ER recalculation")):
            first, reused = calculate_brb(self.settings, self.case, er, store)
            self.assertFalse(reused)
            with patch("underwriting.assessment.brb_service.infer_brb", side_effect=AssertionError("Saved result must be reused")):
                second, reused = calculate_brb(self.settings, self.case, er, store)
            self.assertTrue(reused)
            self.assertEqual(first["run_id"], second["run_id"])
            model = copy.deepcopy(get_model())
            model["brb"]["rules"][0]["conclusion_beliefs"].update(low=.9, high=.1)
            changed, reused = calculate_brb(self.settings, self.case, er, store, model)
            self.assertFalse(reused)
            self.assertNotEqual(first["rule_configuration"]["sha256"], changed["rule_configuration"]["sha256"])
            self.assertEqual(first["er_run_id"], changed["er_run_id"])
            self.assertEqual(effective_er_model_hash(model), effective_er_model_hash(get_model()))
            old = store.latest_brb(self.case, er["source_hash"], er["run_id"], er_result_hash(er), rule_configuration_hash(get_model()), first["engine_version"])
            self.assertEqual(old["run_id"], first["run_id"])

    def test_changed_confirmed_inputs_stop_brb_and_readonly_requires_saved_result(self):
        store, er = self.prepare_er()
        readonly = replace(self.settings, access_mode="public_readonly")
        with patch("underwriting.assessment.brb_service.infer_brb", side_effect=AssertionError("Read-only must not infer")):
            with self.assertRaises(PermissionError):
                calculate_brb(readonly, self.case, er, store)
        first, _ = calculate_brb(self.settings, self.case, er, store)
        with patch("underwriting.assessment.brb_service.infer_brb", side_effect=AssertionError("Read-only must reuse saved result")):
            loaded, reused = calculate_brb(readonly, self.case, er, store)
        self.assertTrue(reused)
        self.assertEqual(first["run_id"], loaded["run_id"])
        from underwriting.assessment.belief_service import confirm_belief
        proposal = self.store.latest_beliefs(self.case, "mock-source", er["reviewed_hash"])
        confirm_belief(self.settings, self.case, self.store, proposal, "F1", "edit", .5, .3, "Mock adjustment")
        with self.assertRaisesRegex(ValueError, "beliefs changed"):
            calculate_brb(self.settings, self.case, er, store)

    def test_new_er_version_requires_new_brb_and_save_failure_keeps_history(self):
        from underwriting.persistence.intake import StoreError
        store, er = self.prepare_er()
        first, _ = calculate_brb(self.settings, self.case, er, store)
        newer_er = calculate_er(self.settings, self.case, store)
        with self.assertRaisesRegex(ValueError, "different ER result"):
            calculate_brb(self.settings, self.case, er, store)
        with patch.object(store, "save_brb", side_effect=StoreError("Mock save failure")):
            with self.assertRaises(StoreError):
                calculate_brb(self.settings, self.case, newer_er, store)
        self.assertEqual(store.latest_brb(self.case, er["source_hash"], er["run_id"], er_result_hash(er), rule_configuration_hash(get_model()), first["engine_version"])["run_id"], first["run_id"])
        second, reused = calculate_brb(self.settings, self.case, newer_er, store)
        self.assertFalse(reused)
        self.assertNotEqual(first["er_result_hash"], second["er_result_hash"])
        with patch.object(store, "latest_brb", side_effect=StoreError("Mock outage")), patch("underwriting.assessment.brb_service.infer_brb", side_effect=AssertionError("An outage must not trigger inference")):
            with self.assertRaises(StoreError):
                calculate_brb(self.settings, self.case, newer_er, store)

    def test_explicit_ui_action_switches_view_and_navigation_does_not_infer_or_write(self):
        from streamlit.testing.v1 import AppTest
        store, er = self.prepare_er()
        script = '''import streamlit as st
from underwriting.ui.brb import brb_controls, brb_results
view = brb_controls({"case_id":"TEST-BELIEF","submission_version":"1"}, "brb-ui", st.session_state["er_view"], st.columns(1)[0])
if st.session_state.get("brb-ui:assessment_stage") == "BRB results":
    brb_results(view)
'''
        with patch("underwriting.ui.brb.settings_for_ui", return_value=self.settings), \
             patch("underwriting.ui.brb.BRBStore", return_value=store), \
             patch("underwriting.intake.service.OpenAI", side_effect=AssertionError("No GPT")), \
             patch("underwriting.assessment.brb_service.infer_brb", wraps=infer_brb) as infer:
            app = AppTest.from_string(script)
            app.session_state["er_view"] = {"run": er, "stale": False}
            app.run()
            self.assertFalse(app.exception)
            infer.assert_not_called()
            self.assertEqual(len(app.metric), 0)
            app.button(key="brb-ui:calculate_brb").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(infer.call_count, 1)
            self.assertEqual(len(app.metric), 3)
            self.assertEqual(list(app.table[0].value.columns), ["Low", "High", "Unknown"])
            self.assertEqual(len(app.table[0].value), 1)
            self.assertEqual(len(app.table[1].value), 1)
            self.assertEqual(len(app.table[2].value), 9)
            self.assertFalse(any(item.label == "Calculation details" for item in app.expander))
            self.assertEqual(len(app.json), 0)
            self.assertTrue(any("All 9 rules are active" in item.value for item in app.caption))
            self.assertEqual(app.session_state["brb-ui:assessment_stage"], "BRB results")
            with patch.object(store, "execute", side_effect=AssertionError("Navigation must not query/write")):
                app.run()
            self.assertFalse(app.exception)
            self.assertEqual(infer.call_count, 1)
            app.button(key="brb-ui:calculate_brb").click().run()
            self.assertEqual(infer.call_count, 1)
            self.assertTrue(any("no recalculation" in item.value for item in app.caption))
            model = copy.deepcopy(get_model())
            model["brb"]["rules"][0]["rule_weight"] = 2
            with patch("underwriting.ui.brb.get_model", return_value=model):
                app.run()
                self.assertTrue(any("stale" in item.value for item in app.warning))
                self.assertEqual(len(app.metric), 0)
            threshold_model = copy.deepcopy(get_model())
            threshold_model["brb"]["active_rule_display_tolerance"] = .5
            with patch("underwriting.ui.brb.get_model", return_value=threshold_model):
                app.button(key="brb-ui:calculate_brb").click().run()
                self.assertFalse(app.exception)
                self.assertEqual(len(app.metric), 3)
                self.assertTrue(any("hidden only by the display threshold" in item.value for item in app.caption))
                self.assertEqual(len(app.table), 3)  # Two ER comparison tables and complete rules.
                self.assertEqual(len(app.table[2].value), 9)
