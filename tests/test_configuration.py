"""Configuration-only checks; no live services or credential values are used."""
import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from underwriting.configuration.settings import ROOT, ConfigError, get_config, load_config, validate
from underwriting.configuration.access import load_settings
from underwriting.intake.schema import fields_definition, UI_TO_PROMPT


class ConfigurationTests(unittest.TestCase):
    def test_valid_configuration_and_existing_identifiers(self):
        config = load_config()
        self.assertEqual(list(config["intake"]["display_groups"]), ["Company", "Submission", "Premises", "Building", "Cover and values", "Business interruption", "Loss and insurance history", "Completeness"])
        self.assertEqual([check["id"] for check in config["screening"]["checks"]], [f"SC{i:02d}" for i in range(1, 7)])
        self.assertEqual(len(fields_definition()), 40)
        self.assertIn("number_of_employees", {field["id"] for field in fields_definition()})
        self.assertEqual(UI_TO_PROMPT["number_of_employees"], "employee_count")

    def test_model_is_only_from_yaml_and_missing_message_is_clear(self):
        config = copy.deepcopy(get_config())
        config["services"]["mode"] = "offline"
        config["llm"]["model"] = None
        with patch("underwriting.configuration.access.get_config", return_value=config), \
             patch("underwriting.configuration.access.load_dotenv"), \
             patch.dict(os.environ, {"OPENAI_MODEL": "must-not-be-used"}, clear=True):
            settings = load_settings({"V2_OPENAI_API_KEY": "test-only-key", "V2_TURSO_DATABASE_URL": "test-only-url",
                                      "V2_TURSO_AUTH_TOKEN": "test-only-token", "INTAKE_ADMIN_PASSWORD": "test-only-password"})
            self.assertEqual(settings.model, "")
            self.assertEqual(settings.missing(), ["llm.model"])
            self.assertNotIn("test-only-key", repr(settings))
            self.assertNotIn("test-only-password", repr(settings))
            config["llm"]["model"] = "explicit-test-model"
            self.assertEqual(load_settings().model, "explicit-test-model")

    def test_invalid_yaml_duplicate_keys_and_unsafe_tags(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.yaml"
            for content in ("llm: [", "llm: {}\nllm: {}", "!!python/object/apply:os.system ['must-not-run']"):
                path.write_text(content, encoding="utf-8")
                with self.assertRaises(ConfigError):
                    load_config(path)

    def test_settings_errors_are_specific_and_do_not_echo_values(self):
        config = copy.deepcopy(get_config())
        config["llm"]["timeout_seconds"] = "test-only-invalid-value"
        with self.assertRaisesRegex(ConfigError, "llm.timeout_seconds") as caught:
            validate(config)
        self.assertNotIn("test-only-invalid-value", str(caught.exception))
        config = copy.deepcopy(get_config())
        config["intake"]["field_groups"][0]["fields"][1]["id"] = config["intake"]["field_groups"][0]["fields"][0]["id"]
        with self.assertRaisesRegex(ConfigError, "unique"):
            validate(config)
        config = copy.deepcopy(get_config())
        config["V2_OPENAI_API_KEY"] = "test-only-forbidden"
        with self.assertRaises(ConfigError) as caught:
            validate(config)
        self.assertNotIn("test-only-forbidden", str(caught.exception))

    def test_optional_parameters_require_explicit_compatibility(self):
        config = copy.deepcopy(get_config())
        self.assertEqual(config["llm"]["generation"], {})
        config["llm"]["generation"] = {"temperature": 0.2}
        with self.assertRaisesRegex(ConfigError, "supported_generation_parameters"):
            validate(config)
        config["llm"].update(model="explicit-test-model", supported_generation_parameters=["temperature"])
        validate(config)

    def test_invalid_configuration_is_visible_in_ui(self):
        from streamlit.testing.v1 import AppTest
        get_config.cache_clear()
        try:
            with patch("underwriting.configuration.settings.load_config", side_effect=ConfigError("llm.timeout_seconds: invalid type")), \
                 patch("underwriting.intake.service.OpenAI", side_effect=AssertionError("No API calls")) as client:
                app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
                self.assertFalse(app.exception)
                self.assertIn("llm.timeout_seconds", app.error[0].value)
                client.assert_not_called()
        finally:
            get_config.cache_clear()


if __name__ == "__main__":
    unittest.main()
