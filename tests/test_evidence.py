"""Attribute evidence tests: mocked GPT, real SQLite SQL, no hosted calls."""
import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from underwriting.configuration.settings import ROOT, get_config, validate, ConfigError
from underwriting.configuration.access import Settings
from underwriting.persistence.intake import StoreError
from underwriting.persistence.evidence import EvidenceStore
from underwriting.assessment.evidence_schema import validate_evidence, attribute_definitions, TEXT_EXTRACTION_VERSION
from underwriting.assessment.evidence_service import evidence_prompt, find_evidence, retained, retry_evidence_save, review_attribute
import test_intake as intake_tests

TEXT = "Mock client statement: clear aisles. Mock broker observation: blocked aisle."


def fixture():
    attributes = []
    for definition in attribute_definitions():
        identifier = definition["id"]
        aspects = definition.get("aspects", ["General"])
        items = [{"evidence_id": identifier + str(index), "page": 1, "section": "Mock section",
                  "exact_quote": TEXT, "source_attribution": "Mock source", "extracted_fact": "Test interpreted fact",
                  "aspect": aspect, "implication": "contextual", "relevance_explanation": "Test relevance; statements are not independently verified."}
                 for index, aspect in enumerate(aspects)]
        attributes.append({"attribute_id": identifier, "evidence_items": items, "information_gaps": [],
                           "contradictions": [], "ai_summary": "Mock summary without a grade."})
    return {"attributes": attributes}


class LocalEvidenceStore(EvidenceStore):
    def __init__(self, settings):
        self.settings = settings
        self.db = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
        self.fail_save = False

    def execute(self, sql, args=(), missing_ok=False):
        if self.fail_save and sql.startswith("INSERT OR IGNORE INTO attribute_evidence_runs"):
            raise StoreError("Mock evidence save failure")
        return intake_tests.SQLiteStore.execute(self, sql, args, missing_ok)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings("test-key", "test-model", "https://test.invalid", "test-token", "local_owner", service_mode="test_turso", test_scope_confirmed=True, allowed_test_database_hosts=("test.invalid",))
        self.store = LocalEvidenceStore(self.settings)
        self.case = {"case_id": "TEST-EVIDENCE", "submission_version": "1", "document_path": "test.pdf"}
        self.data = fixture()
        self.request = Mock(side_effect=lambda *_: ({"status": "completed", "model": "mock-model", "usage": {"input_tokens": 1}}, json.dumps(self.data)))
        self.patches = [patch("underwriting.configuration.access.local_server", return_value=True),
                        patch("underwriting.assessment.evidence_service.ROOT", Path(self.temp.name)),
                        patch("underwriting.assessment.evidence_service.document_hash", return_value="test-hash"),
                        patch("underwriting.assessment.evidence_service.source_context", return_value=("test-hash", [TEXT]))]
        for patched in self.patches:
            patched.start()
        intake_tests.seed_submission(self.store,self.case,"test-hash")

    def tearDown(self):
        for patched in reversed(self.patches):
            patched.stop()
        self.store.db.close()
        self.temp.cleanup()

    def run_action(self, **kwargs):
        return find_evidence(self.settings, self.case, self.store, request=self.request, **kwargs)

    def test_yaml_prompt_exact_and_single_substitution(self):
        approved = get_config()["prompts"]["attribute_evidence"]
        self.assertTrue(approved["template"].startswith("You are assisting a commercial property underwriter"))
        prompt, metadata = evidence_prompt(self.case, [TEXT + " {{case_id_and_source_version}}"])
        self.assertIn("[PAGE 1]", prompt)
        self.assertIn("TEST-EVIDENCE", prompt)
        self.assertIn("{{case_id_and_source_version}}", prompt)  # Source content is not templated twice.
        self.assertIn("Assess sprinkler arrangements under F3", prompt)
        self.assertEqual(metadata["sha256"], hashlib.sha256(approved["template"].encode()).hexdigest())
        invalid = copy.deepcopy(get_config())
        invalid["prompts"]["attribute_evidence"]["template"] = "missing placeholders"
        with self.assertRaises(ConfigError):
            validate(invalid)

    def test_all_five_ids_quotes_gaps_and_contradictions(self):
        validate_evidence(self.data, [TEXT])
        no_evidence = copy.deepcopy(self.data)
        no_evidence["attributes"][0].update(evidence_items=[], information_gaps=[{"gap_id": "G1", "description": "No relevant material found in the test source", "basis": "not_found_in_supplied_material", "related_evidence_ids": []}])
        validate_evidence(no_evidence, [TEXT])
        conflicting = copy.deepcopy(self.data)
        conflicting["attributes"][0]["contradictions"] = [{"description": "Mock client and broker accounts differ", "related_evidence_ids": ["F10"]}]
        validate_evidence(conflicting, [TEXT])
        invalid = copy.deepcopy(self.data)
        invalid["attributes"][0]["evidence_items"][0]["exact_quote"] = "fabricated quotation"
        with self.assertRaisesRegex(ValueError, "does not match"):
            validate_evidence(invalid, [TEXT])
        invalid = copy.deepcopy(self.data)
        invalid["attributes"][0]["evidence_items"][0]["page"] = 8
        with self.assertRaises(ValueError):
            validate_evidence(invalid, [TEXT])
        invalid = copy.deepcopy(self.data)
        invalid["attributes"][0]["information_gaps"] = [{"gap_id": "G1", "description": "gap", "basis": "explicit_in_source", "related_evidence_ids": ["missing-id"]}]
        with self.assertRaises(ValueError):
            validate_evidence(invalid, [TEXT])
        invalid = copy.deepcopy(self.data)
        invalid["attributes"].append(invalid["attributes"][0])
        with self.assertRaises(ValueError):
            validate_evidence(invalid, [TEXT])

    def test_saved_load_zero_calls_and_source_reuse(self):
        first = self.run_action()
        self.assertEqual(first["status"], "completed")
        self.assertIsNotNone(self.store.source("test-hash", TEXT_EXTRACTION_VERSION))
        with patch("underwriting.assessment.evidence_service.source_context", side_effect=AssertionError("Must reuse matching stored text")):
            loaded = self.run_action()
        self.assertEqual(first["run_id"], loaded["run_id"])
        self.request.assert_called_once()
        self.assertIsNone(self.store.latest_evidence(dict(self.case, case_id="OTHER"), "test-hash"))
        self.assertIsNone(self.store.latest_evidence(self.case, "different-source"))

    def test_load_selects_latest_success_and_not_latest_failure(self):
        first = self.run_action()
        newer = dict(first, run_id="newer-success")  # Same timestamp exercises rowid tie-break.
        self.store.save_evidence(newer)
        self.assertEqual(self.run_action()["run_id"], "newer-success")
        failed = dict(newer, run_id="newer-failure", status="failed", finished_at="2099-01-01")
        self.store.save_evidence(failed)
        self.assertEqual(self.run_action()["run_id"], "newer-success")
        self.request.assert_called_once()

    def test_named_review_summary_updates_and_preserves_prior_reviewers(self):
        from underwriting.ui.evidence import evidence_review_summary
        run = self.run_action()
        facts = {item["evidence_id"]: item["extracted_fact"] for item in run["result"]["attributes"][0]["evidence_items"]}
        alice = review_attribute(self.settings, self.store, run, "F1", facts, "", True, reviewer_name="Alice")
        bob = review_attribute(self.settings, self.store, run, "F1", facts, "Second review", True, reviewer_name="Bob")
        summary, reviewers = evidence_review_summary(run, self.store.reviews(run["run_id"]))
        self.assertIn("1/5", summary)
        self.assertEqual(set(reviewers), {"Alice", "Bob"})
        self.assertEqual(reviewers["Bob"]["time"], bob["created_at"])
        self.assertEqual(alice["reviewer_identity_type"], "self_reported")
        for attribute in run["result"]["attributes"][1:]:
            values = {item["evidence_id"]: item["extracted_fact"] for item in attribute["evidence_items"]}
            review_attribute(self.settings, self.store, run, attribute["attribute_id"], values, "", True, reviewer_name="Bob")
        self.assertIn("complete", evidence_review_summary(run, self.store.reviews(run["run_id"]))[0])
        review_attribute(self.settings, self.store, run, "F1", facts, "Reopen", False, reviewer_name="Bob")
        self.assertIn("4/5", evidence_review_summary(run, self.store.reviews(run["run_id"]))[0])
        with self.assertRaisesRegex(ValueError, "reviewer name"):
            review_attribute(self.settings, self.store, run, "F1", facts, "", True, reviewer_name="")

    def test_review_events_keep_ai_and_quotes_immutable(self):
        run = self.run_action()
        original = copy.deepcopy(run)
        event = review_attribute(self.settings, self.store, run, "F1", {"F10": "Human-corrected interpretation"}, "Reviewer note", True)
        self.assertEqual(self.store.reviews(run["run_id"])[0], event)
        self.assertEqual(run, original)
        with self.assertRaises(PermissionError):
            review_attribute(replace(self.settings, access_mode="public_readonly"), self.store, run, "F1", {"F10": "change"}, "", True)

    def test_outage_and_save_retry_never_repeat_gpt(self):
        with patch.object(self.store, "latest_evidence", side_effect=StoreError("outage")):
            with self.assertRaises(StoreError):
                self.run_action()
        self.request.assert_not_called()
        self.store.fail_save = True
        with self.assertRaises(StoreError):
            self.run_action()
        saved = retained(self.case, "test-hash")[0]
        self.assertEqual(saved["status"], "completed")
        self.store.fail_save = False
        retry_evidence_save(self.settings, self.store, saved)
        self.request.assert_called_once()

    def test_failed_regeneration_preserves_success_and_requires_confirmation(self):
        first = self.run_action()
        with self.assertRaises(PermissionError):
            self.run_action(regenerate=True, confirmed=False)
        self.data["attributes"][0]["evidence_items"][0]["exact_quote"] = "unsupported"
        failed = self.run_action(regenerate=True, confirmed=True, expected_run=first["run_id"])
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(self.store.latest_evidence(self.case, "test-hash")["run_id"], first["run_id"])

    def test_ui_load_navigation_and_review_persistence(self):
        from streamlit.testing.v1 import AppTest
        # The actual case selector is retained; all evidence below is explicitly mocked.
        self.case["case_id"], self.case["submission_version"] = "NB-2026-001", "1.0"
        intake_tests.seed_submission(self.store,self.case,"test-hash")
        with patch("underwriting.ui.evidence.settings_for_ui", return_value=self.settings), \
             patch("underwriting.ui.intake.settings_for_ui", return_value=Settings()), \
             patch("underwriting.ui.evidence.document_hash", return_value="test-hash"), \
             patch("underwriting.ui.evidence.EvidenceStore", return_value=self.store), \
             patch("underwriting.assessment.evidence_service.structured_request", self.request):
            app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
            self.assertFalse(app.exception)
            self.request.assert_not_called()
            prefix = "case:NB-2026-001"
            app.button(key=f"{prefix}:Find evidence").click().run()
            self.assertFalse(app.exception, app.exception)
            self.request.assert_called_once()
            self.assertTrue(any("Evidence source: GPT" in item.value for item in app.caption))
            self.assertFalse(any(item.label == "Evidence provenance and versions" for item in app.expander))
            self.assertEqual(app.session_state[prefix + ":assessment_stage"], "Evidence")
            self.assertFalse(any(item.value == "Low" for item in next(tab for tab in app.tabs if tab.label == "Assessment").caption))
            self.assertFalse(any(item.value == "AI-proposed beliefs" for item in next(tab for tab in app.tabs if tab.label == "Assessment").markdown))
            self.assertFalse(any(item.value == "Fire and Security ER results" for item in next(tab for tab in app.tabs if tab.label == "Assessment").subheader))
            for name in ["F1 Combustible storage", "F2 Ignition controls", "F3 Fire protection", "S1 Access protection", "S2 Intrusion detection"]:
                app.button(key=f"{prefix}:select:{name}").click().run()
                self.assertFalse(app.exception)
            app.run()
            self.request.assert_called_once()
            app.button(key=f"{prefix}:Find evidence").click().run()
            self.assertTrue(any("Evidence source:" in item.value for item in app.caption))
            self.request.assert_called_once()
            self.assertTrue(all(field.value == "" and field.disabled for field in app.text_input if field.label != "Reviewer name"))
            app.button(key=f"{prefix}:select:F1 Combustible storage").click().run()
            facts = next(field for field in app.text_area if field.label == "Interpreted fact · F10")
            facts.set_value("Reviewed mock interpretation")
            next(field for field in app.text_area if field.label == "Review notes").set_value("Mock human note")
            next(field for field in app.text_input if field.label == "Reviewer name").set_value("Alice")
            next(field for field in app.checkbox if field.label == "Confirm this attribute's evidence as reviewed").set_value(True)
            next(button for button in app.button if button.label == "Save evidence review").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any("Review recorded by Alice" in item.value for item in app.caption))
            self.assertTrue(any(event["corrected_facts"]["F10"] == "Reviewed mock interpretation" for event in self.store.reviews(app.session_state[f"{prefix}:1.0:test-hash:evidence"]["run"]["run_id"])))
            self.request.assert_called_once()


if __name__ == "__main__":
    unittest.main()
