"""Saved-first and local/read-only tests; all services are mocked."""
import copy
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from underwriting.configuration.access import load_settings, require_owner
from underwriting.ui.intake import load_view
from underwriting.intake.recovery import recover_screening
from underwriting.persistence.intake import StoreError
from underwriting.intake.service import now
import test_intake as fixtures

TEXT = fixtures.TEXT


class SavedFirstTests(unittest.TestCase):
    setUp = fixtures.IntakeTests.setUp
    tearDown = fixtures.IntakeTests.tearDown
    run_action = fixtures.IntakeTests.run_action
    def test_startup_does_not_query_analysis_or_call_gpt(self):
        with patch("underwriting.ui.intake.settings_for_ui", return_value=self.settings), \
             patch("underwriting.ui.intake.source_context", return_value=("startup-only-hash", [TEXT])), \
             patch("underwriting.ui.intake.TursoStore", return_value=self.store), \
             patch.object(self.store, "candidates", side_effect=AssertionError("Startup queried Turso")), \
             patch.object(self.store, "latest", side_effect=AssertionError("Startup queried Turso")):
            view = load_view(self.case)
            self.assertIsNone(view["saved"])
        self.request.assert_not_called()
    # Reuse the mock-only fixtures and production-SQL SQLite adapter.
    def test_dotenv_explicit_loading_aliases_and_preserved_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text("V2_OPENAI_API_KEY=test-only-file-key\nV2_TURSO_DATABASE_URL=https://test.invalid\nV2_TURSO_AUTH_TOKEN=test-only-token\n", encoding="utf-8")
            (root / "Corda.env").write_text("OPENAI_API_KEY=must-not-load\n", encoding="utf-8")
            with patch("underwriting.configuration.access.ROOT", root), patch.dict(os.environ, {"V2_OPENAI_API_KEY": "test-only-existing"}, clear=True):
                settings = load_settings()
                self.assertEqual(settings.api_key, "test-only-existing")
                self.assertEqual(settings.database_token, "test-only-token")
                self.assertEqual(settings.model, "gpt-5-mini")
                self.assertEqual(settings.missing(), [])
                self.assertNotIn("test-only-existing", repr(settings))
                self.assertNotIn("test-only-token", repr(settings))
                self.assertNotIn("OPENAI_API_KEY", os.environ)

    def test_missing_screening_reuses_saved_extraction(self):
        saved = self.run_action()
        partial = copy.deepcopy(saved)
        partial.update(run_id="partial-run", finished_at=now(), status="extracted", outcome=None)
        partial["result"]["screening_checks"] = []
        self.store.save(partial, None)
        with patch("underwriting.ui.intake.settings_for_ui", return_value=self.settings), \
             patch("underwriting.ui.intake.source_context", return_value=("test-hash", [TEXT])), \
             patch("underwriting.ui.intake.TursoStore", return_value=self.store):
            view = load_view(self.case, query=True)
            self.assertFalse(view["error"])
            self.assertEqual(view["saved"]["derived_from_run_id"], "partial-run")
            self.assertEqual(len(view["saved"]["result"]["screening_checks"]), 6)
            self.assertEqual(self.request.call_count, 1)
            loaded_again = load_view(self.case, query=True)
            self.assertEqual(loaded_again["saved"]["run_id"], view["saved"]["run_id"])
            self.assertEqual(self.request.call_count, 1)

    def test_database_outage_never_calls_gpt(self):
        with patch.object(self.store, "latest", side_effect=StoreError("Mock database outage")):
            with self.assertRaises(StoreError):
                self.run_action()
        self.request.assert_not_called()
        with patch("underwriting.ui.intake.settings_for_ui", return_value=self.settings), \
             patch("underwriting.ui.intake.source_context", return_value=("test-hash", [TEXT])), \
             patch("underwriting.ui.intake.TursoStore", return_value=self.store), \
             patch.object(self.store, "latest", side_effect=StoreError("Mock database outage")):
            view = load_view(self.case, query=True)
            self.assertIn("outage", view["error"])
            self.assertIsNone(view["saved"])
        self.request.assert_not_called()

    def test_readonly_blocks_actions_even_with_local_binding(self):
        readonly = replace(self.settings, access_mode="public_readonly")
        with self.assertRaises(PermissionError):
            require_owner(readonly)
        with patch("underwriting.configuration.access.local_server", return_value=False):
            with self.assertRaises(PermissionError):
                require_owner(self.settings)

    def test_partial_essential_gaps_are_reviewed_not_filled(self):
        saved = self.run_action()
        saved["result"]["screening_checks"] = []
        company = next(field for field in saved["result"]["basic_information"] if field["field_id"] == "company_name")
        company.update(status="not_provided", value=None, evidence=[])
        recovered = recover_screening(saved, [TEXT], self.case)
        self.assertEqual(recovered["status"], "needs_review")
        self.assertEqual(recovered["outcome"], "Pause for clarification")
        self.assertEqual(recovered["result"]["screening_checks"][0]["result"], "unknown")
        self.assertIsNone(recovered["result"]["basic_information"][5]["value"])
        self.assertEqual(self.request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
