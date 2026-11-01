"""Configuration-only checks: no inference, database access or GPT requests."""
import copy
import itertools
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from underwriting.configuration.settings import ConfigError, get_config
from underwriting.configuration.decision_model import get_model, validate_model, load_model, effective_model_hash, attribute_groups
from underwriting.assessment.evidence_schema import attribute_definitions, evidence_output_schema, validate_evidence
from underwriting.assessment.belief_schema import belief_output_schema
import test_evidence as evidence_tests


class DecisionModelTests(unittest.TestCase):
    def model(self):
        return copy.deepcopy(get_model())

    def test_initial_scaffold_and_separation(self):
        model = self.model()
        validate_model(model)
        self.assertNotIn("assessment", get_config())
        self.assertNotIn("overall_rules", get_config())
        self.assertEqual(model["grades"]["order"], ["low", "high"])
        self.assertTrue(model["er"]["enabled"])
        self.assertTrue(model["brb"]["enabled"])
        self.assertTrue(model["brb"]["configured"])
        self.assertEqual(model["er"]["groups"]["fire"]["importance_weights"], {"F1": 1, "F2": 1, "F3": 1})
        self.assertEqual(model["er"]["normalization_policy"], "divide_by_group_total")
        self.assertEqual(len(model["brb"]["rules"]), 9)
        self.assertTrue(all(sum(rule["conclusion_beliefs"].values()) == 1 and rule["rule_weight"] == 1 for rule in model["brb"]["rules"]))
        self.assertEqual({tuple(rule["antecedents"].values()) for rule in model["brb"]["rules"]}, set(itertools.product(["low", "high", "unknown"], repeat=2)))
        self.assertNotEqual(model["brb"]["output"]["explicit_unknown"]["field"], model["brb"]["output"]["residual_unassigned"]["field"])

    def test_invalid_weights_and_memberships(self):
        for value in (-1, float("inf"), float("nan"), True):
            model = self.model()
            model["er"]["groups"]["fire"]["importance_weights"]["F1"] = value
            with self.assertRaises(ConfigError):
                validate_model(model)
        model = self.model()
        model["er"]["groups"]["fire"]["importance_weights"] = dict.fromkeys(["F1", "F2", "F3"], 0)
        with self.assertRaisesRegex(ConfigError, "positive"):
            validate_model(model)
        for member in ("F1", "DOES_NOT_EXIST"):
            model = self.model()
            model["er"]["groups"]["fire"]["members"].append(member)
            with self.assertRaises(ConfigError):
                validate_model(model)

    def test_duplicate_ids_grade_order_and_cycles(self):
        model = self.model()
        model["attributes"].append(model["attributes"][0])
        with self.assertRaisesRegex(ConfigError, "unique"):
            validate_model(model)
        model = self.model()
        model["grades"]["order"] = ["low", "low"]
        with self.assertRaises(ConfigError):
            validate_model(model)
        model = self.model()
        model["er"]["groups"]["fire"].update(members=["er:security"], importance_weights={"er:security": 1})
        model["er"]["groups"]["security"].update(members=["er:fire"], importance_weights={"er:fire": 1})
        with self.assertRaisesRegex(ConfigError, "circular"):
            validate_model(model)

    def test_rules_references_and_distributions(self):
        model = self.model()
        model["brb"]["rules"][0]["antecedents"]["fire"] = "invalid"
        with self.assertRaises(ConfigError):
            validate_model(model)
        model = self.model()
        model["brb"]["rules"][1]["antecedents"] = copy.deepcopy(model["brb"]["rules"][0]["antecedents"])
        with self.assertRaisesRegex(ConfigError, "duplicate"):
            validate_model(model)
        model = self.model()
        model["brb"]["inputs"]["fire"]["reference"] = "er:missing"
        with self.assertRaises(ConfigError):
            validate_model(model)
        for conclusion in ({"low": .8, "high": .8, "unknown": 0}, {"low": -0.1, "high": .4, "unknown": .1}):
            model = self.model()
            model["brb"]["rules"][0]["conclusion_beliefs"] = conclusion
            with self.assertRaises(ConfigError):
                validate_model(model)

    def test_enabled_rules_must_be_configured_complete_and_nonnull(self):
        # Hypothetical validation fixture only. No method implementation or inference is called.
        model = self.model()
        model["er"].update(enabled=True, method="test-method-id")
        model["brb"].update(enabled=True, configured=True, method="rimer_activation_analytical_er")
        model["brb"]["rules"][0]["conclusion_beliefs"] = None
        with self.assertRaisesRegex(ConfigError, "null"):
            validate_model(model)
        for rule in model["brb"]["rules"]:
            rule["conclusion_beliefs"] = {"low": .2, "high": .3, "unknown": .1}
        validate_model(model)  # Incomplete conclusions are permitted; no residual value is computed.
        model["brb"]["rules"].pop()
        with self.assertRaisesRegex(ConfigError, "Cartesian"):
            validate_model(model)

    def test_hash_is_deterministic_and_changes_with_weights(self):
        model = self.model()
        reversed_keys = dict(reversed(list(model.items())))
        self.assertEqual(effective_model_hash(model), effective_model_hash(reversed_keys))
        model["er"]["groups"]["fire"]["importance_weights"]["F1"] = 2
        self.assertNotEqual(effective_model_hash(model), effective_model_hash(get_model()))

    def test_adding_disabling_removing_attribute_updates_consumers(self):
        historical_definitions = copy.deepcopy(attribute_definitions())
        historical_response = evidence_tests.fixture()
        model = self.model()
        model["attributes"].append({"id": "X1", "label": "Test attribute", "definition": "Test-only definition", "enabled": True})
        model["er"]["groups"]["fire"]["members"].append("X1")
        model["er"]["groups"]["fire"]["importance_weights"]["X1"] = 1
        validate_model(model)
        with patch("underwriting.configuration.decision_model.get_model", return_value=model):
            self.assertIn("X1", [attribute["id"] for attribute in attribute_definitions()])
            self.assertIn("X1", evidence_output_schema()["$defs"]["Attribute"]["properties"]["attribute_id"]["enum"])
            self.assertIn("X1", belief_output_schema()["$defs"]["Assessment"]["properties"]["attribute_id"]["enum"])
            validate_evidence(evidence_tests.fixture(), [evidence_tests.TEXT])
            validate_evidence(historical_response, [evidence_tests.TEXT], definitions=historical_definitions)
            self.assertTrue(any(attribute["id"] == "X1" for attribute in attribute_groups()["Fire"]))
        model["attributes"][-1]["enabled"] = False
        with patch("underwriting.configuration.decision_model.get_model", return_value=model):
            self.assertNotIn("X1", [attribute["id"] for attribute in attribute_definitions()])
            validate_evidence(evidence_tests.fixture(), [evidence_tests.TEXT])
        model["attributes"].pop()
        model["er"]["groups"]["fire"]["members"].remove("X1")
        del model["er"]["groups"]["fire"]["importance_weights"]["X1"]
        validate_model(model)

    def test_invalid_yaml_and_unknown_residual_separation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "decision.yaml"
            path.write_text("model_version: [", encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "malformed YAML"):
                load_model(path)
        model = self.model()
        model["brb"]["output"]["residual_unassigned"]["field"] = model["brb"]["output"]["explicit_unknown"]["field"]
        with self.assertRaisesRegex(ConfigError, "different fields"):
            validate_model(model)


if __name__ == "__main__":
    unittest.main()
