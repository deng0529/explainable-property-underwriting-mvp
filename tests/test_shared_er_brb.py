"""Shared ER core, complete BRB interfaces, and descriptive output labels."""
import copy
import unittest
from unittest.mock import patch

import underwriting.models.er as er_core
from underwriting.models.er_adapter import aggregate_configured_group
from underwriting.ui.er_presentation import describe_er_result
from underwriting.models.brb_adapter import prepare_brb_inputs, combine_activated_rule_conclusions
from underwriting.configuration.decision_model import get_model, validate_model
from underwriting.configuration.settings import ConfigError
from test_er import recursive_reference


class SharedERTests(unittest.TestCase):
    def result(self, low, high, unknown):
        return {"assigned_beliefs": {"low": low, "high": high}, "unassigned_belief": unknown}

    def test_largest_component_labels_and_ties_use_unrounded_values(self):
        model = copy.deepcopy(get_model())
        for values, label in [((.6, .3, .1), "Low concern"), ((.2, .7, .1), "High concern"),
                              ((.1, .2, .7), "Insufficient information"), ((.45, .45, .1), "Mixed assessment"),
                              ((.1, .45, .45), "Mixed assessment"), ((1/3, 1/3, 1/3), "Mixed assessment")]:
            result = self.result(*values)
            original = copy.deepcopy(result)
            summary = describe_er_result(result, model)
            self.assertEqual(summary["label"], label)
            self.assertEqual(summary["winning_belief_degree"], max(values))
            self.assertEqual(result, original)
        model["tolerances"]["assessment_tie"] = .000001
        self.assertEqual(describe_er_result(self.result(.4500005, .45, .0999995), model)["label"], "Mixed assessment")
        # Both display as 0.45 but are NOT tied within the configured tolerance.
        self.assertEqual(describe_er_result(self.result(.450002, .45, .099998), model)["label"], "Low concern")
        model["tolerances"]["assessment_tie"] = 0
        self.assertEqual(describe_er_result(self.result(.4500005, .45, .0999995), model)["label"], "Low concern")

    def test_tie_tolerance_validation(self):
        for invalid in (-1, 1, float("nan"), float("inf"), True):
            model = copy.deepcopy(get_model())
            model["tolerances"]["assessment_tie"] = invalid
            with self.assertRaises(ConfigError):
                validate_model(model)

    def test_groups_and_activated_rules_call_the_same_core_with_distinct_weights(self):
        model = get_model()
        beliefs = {item["id"]: {"low": .2, "high": .5, "unknown": .3} for item in model["attributes"]}
        conclusions = [{"low": .1, "high": .6, "unknown": .1}, {"low": .6, "high": .2, "unknown": .1}]
        weights = [.2, .8]
        with patch("underwriting.models.er.combine_er", wraps=er_core.combine_er) as shared:
            aggregate_configured_group("fire", beliefs, model)
            combined = combine_activated_rule_conclusions(conclusions, weights, model["brb"]["output"]["states"])
            self.assertEqual(shared.call_count, 2)
            self.assertEqual(shared.call_args_list[0].args[1], [1/3, 1/3, 1/3])
            self.assertEqual(shared.call_args_list[1].args[1], weights)
        expected, remainder = recursive_reference([[.1, .6, .1], [.6, .2, .1]], weights)
        for actual, reference in zip(combined["state_beliefs"].values(), expected):
            self.assertAlmostEqual(actual, reference, places=12)
        self.assertAlmostEqual(combined["residual_unassigned_belief"], remainder, places=12)
        self.assertTrue(model["brb"]["enabled"])
        self.assertTrue(all(rule["conclusion_beliefs"] is not None for rule in model["brb"]["rules"]))

    def test_brb_inputs_keep_every_fraction_and_ignore_display_labels(self):
        model = get_model()
        groups = {"fire": self.result(.36, .44, .20), "security": self.result(.123456789, .276543211, .6)}
        groups["fire"]["label"] = "Low concern"  # Deliberately inconsistent display metadata must be ignored.
        original = copy.deepcopy(groups)
        self.assertEqual(prepare_brb_inputs(groups, model), {
            "fire": {"low": .36, "high": .44, "unknown": .20},
            "security": {"low": .123456789, "high": .276543211, "unknown": .6}})
        self.assertEqual(groups, original)
        with self.assertRaises(er_core.ERInputError):
            prepare_brb_inputs({"fire": "High concern", "security": groups["security"]}, model)

    def test_explicit_unknown_and_residual_remain_separate(self):
        result = combine_activated_rule_conclusions([{"low": .2, "high": .3, "unknown": .1}], [1], ["low", "high", "unknown"])
        self.assertEqual(result["state_beliefs"]["unknown"], .1)
        self.assertEqual(result["residual_unassigned_belief"], .4)
        zero = combine_activated_rule_conclusions([{"low": 0, "high": 0, "unknown": 0}], [1], ["low", "high", "unknown"])
        self.assertEqual(zero["state_beliefs"]["unknown"], 0)
        self.assertEqual(zero["residual_unassigned_belief"], 1)
        for conclusions, weights in [([None], [1]), ([{"low": 1}], [1]),
                                     ([{"low": .2, "high": .3, "unknown": .1}], [0]),
                                     ([{"low": .2, "high": .3, "unknown": .1}], [2])]:
            with self.assertRaises(er_core.ERInputError):
                combine_activated_rule_conclusions(conclusions, weights, ["low", "high", "unknown"])
