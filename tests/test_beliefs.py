"""Mock-only proposals and confirmations; production SQL on in-memory SQLite."""
import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from underwriting.configuration.settings import ROOT, get_config
from underwriting.configuration.access import Settings
from underwriting.persistence.intake import StoreError
from underwriting.assessment.evidence_service import review_attribute
from underwriting.persistence.beliefs import BeliefStore
from underwriting.assessment.belief_schema import validate_beliefs
from underwriting.assessment.belief_service import current_input, reviewed_set, belief_prompt, propose_beliefs, pending_beliefs, retry_belief_save, confirm_belief
import test_evidence as evidence_tests


class LocalBeliefStore(BeliefStore):
    def __init__(self, settings):
        self.settings = settings
        self.db = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
        self.fail_save = False
        self.fail_belief_save = False

    def execute(self, sql, args=(), missing_ok=False):
        if self.fail_belief_save and sql.startswith("INSERT OR IGNORE INTO attribute_belief_runs"):
            raise StoreError("Mock belief save failure")
        return evidence_tests.LocalEvidenceStore.execute(self, sql, args, missing_ok)


def proposals(evidence):
    result = []
    for attribute in evidence["attributes"]:
        identifier = attribute["attribute_id"]
        ids = [attribute["evidence_items"][0]["evidence_id"]]
        result.append({"attribute_id": identifier, "low": 0.4, "high": 0.4, "unknown": 0.2,
                       "assessment_summary": "Mock provisional judgment, not event probability.",
                       "low_support": {"explanation": "Mock favourable support", "evidence_ids": ids},
                       "high_support": {"explanation": "Mock adverse support", "evidence_ids": ids},
                       "unknown_basis": {"explanation": "Mock unresolved scope", "evidence_ids": ids, "gap_ids": []},
                       "important_concerns": ["Mock concern"], "clarification_questions": ["Mock question?"],
                       "protection_aspect_findings": [{"aspect": aspect, "finding": "Mock aspect finding", "evidence_ids": ids} for aspect in ("compartmentation", "detection", "suppression")] if identifier == "F3" else []})
    return {"assessments": result}


class BeliefTests(unittest.TestCase):
    def test_load_selects_latest_success_for_current_reviewed_evidence(self):
        first = self.run_action()
        self.store.save_beliefs(dict(first, run_id="newer-beliefs"))
        self.assertEqual(self.run_action()["run_id"], "newer-beliefs")
        self.store.save_beliefs(dict(first, run_id="failed-beliefs", status="failed", finished_at="2099-01-01"))
        self.assertEqual(self.run_action()["run_id"], "newer-beliefs")
        self.request.assert_called_once()
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings("test-key", "test-model", "https://test.invalid", "test-token", "local_owner", service_mode="test_turso", test_scope_confirmed=True, allowed_test_database_hosts=("test.invalid",))
        self.store = LocalBeliefStore(self.settings)
        self.case = {"case_id": "TEST-BELIEF", "submission_version": "1", "document_path": "unused.pdf"}
        self.patches = [patch("underwriting.configuration.access.local_server", return_value=True),
                        patch("underwriting.assessment.belief_service.ROOT", Path(self.temp.name)),
                        patch("underwriting.assessment.score_service.ROOT", Path(self.temp.name)),
                        patch("underwriting.assessment.belief_service.document_hash", return_value="mock-source")]
        for patched in self.patches:
            patched.start()
        evidence_tests.intake_tests.seed_submission(self.store,self.case,"mock-source")
        self.store.initialise_evidence()
        pages=[evidence_tests.TEXT]
        self.store.save_source({'source_hash':'mock-source','extraction_version':evidence_tests.TEXT_EXTRACTION_VERSION,
            'pages':pages,'text_hash':hashlib.sha256(json.dumps(pages,ensure_ascii=False).encode()).hexdigest()})
        self.evidence = {"run_id": "mock-evidence-run", "case_id": self.case["case_id"], "source_version": "1", "source_hash": "mock-source",
                         "status": "completed", "finished_at": "2026-01-01", "result": evidence_tests.fixture()}
        self.store.save_evidence(self.evidence)
        for attribute in self.evidence["result"]["attributes"]:
            review_attribute(self.settings, self.store, self.evidence, attribute["attribute_id"],
                             {item["evidence_id"]: "Human reviewed mock fact" for item in attribute["evidence_items"]}, "Mock human note", True)
        self.reviewed = current_input(self.case, self.store)[2]
        self.data = proposals(self.reviewed)
        self.request = Mock(side_effect=lambda *_: ({"status": "completed", "model": "mock-model", "usage": {"input_tokens": 1}}, json.dumps(self.data)))

    def tearDown(self):
        for patched in reversed(self.patches):
            patched.stop()
        self.store.db.close()
        self.temp.cleanup()

    def run_action(self, **kwargs):
        return propose_beliefs(self.settings, self.case, self.store, request=self.request, **kwargs)

    def test_prompt_and_fractional_proposals(self):
        prompt, metadata = belief_prompt(self.case, self.reviewed)
        self.assertIn("multiples of 0.05", prompt)
        self.assertIn("Human reviewed mock fact", prompt)
        self.assertIn("exact_quote", prompt)
        self.assertEqual(metadata["version"], "1.0")
        result = self.run_action()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["result"]["assessments"]), 5)
        loaded = self.run_action()
        self.assertEqual(loaded["run_id"], result["run_id"])
        self.request.assert_called_once()

    def test_invalid_values_ids_references_and_f3(self):
        for change in ({"low": 0.8}, {"low": float("nan")}, {"low": 0.43, "high": 0.37}, {"attribute_id": "OTHER"}):
            invalid = copy.deepcopy(self.data)
            invalid["assessments"][0].update(change)
            with self.assertRaises(ValueError):
                validate_beliefs(invalid, self.reviewed)
        invalid = copy.deepcopy(self.data)
        invalid["assessments"][0]["high_support"]["evidence_ids"] = ["S10"]
        with self.assertRaises(ValueError):
            validate_beliefs(invalid, self.reviewed)
        invalid = copy.deepcopy(self.data)
        next(item for item in invalid["assessments"] if item["attribute_id"] == "F3")["protection_aspect_findings"] = []
        with self.assertRaises(ValueError):
            validate_beliefs(invalid, self.reviewed)

    def test_all_reviewed_required_and_insufficient_information_allowed(self):
        first = self.evidence["result"]["attributes"][0]
        review_attribute(self.settings, self.store, self.evidence, "F1", {item["evidence_id"]: "Draft interpretation" for item in first["evidence_items"]}, "", False)
        with self.assertRaisesRegex(ValueError, "Pending: F1"):
            self.run_action()
        self.request.assert_not_called()
        no_evidence = copy.deepcopy(self.evidence)
        no_evidence["result"]["attributes"][0].update(evidence_items=[], information_gaps=[{"gap_id": "G1", "description": "Insufficient information", "basis": "not_found_in_supplied_material", "related_evidence_ids": []}])
        event = review_attribute(self.settings, self.store, no_evidence, "F1", {}, "Confirmed insufficient information", True)
        reviewed, _, _ = reviewed_set(no_evidence, self.store.reviews(no_evidence["run_id"]))
        data = copy.deepcopy(self.data)
        data["assessments"][0].update(low=0.0, high=0.0, unknown=1.0,
            low_support={"explanation": "No belief assigned", "evidence_ids": []},
            high_support={"explanation": "No belief assigned", "evidence_ids": []},
            unknown_basis={"explanation": "Insufficient supplied evidence", "evidence_ids": [], "gap_ids": ["G1"]})
        validate_beliefs(data, reviewed)

    def test_accept_edit_confirm_and_stale_evidence(self):
        run = self.run_action()
        accepted = confirm_belief(self.settings, self.case, self.store, run, "F1", "accept")
        self.assertEqual(accepted["values"], {"low": 0.4, "high": 0.4, "unknown": 0.2})
        with self.assertRaisesRegex(ValueError, "reason"):
            confirm_belief(self.settings, self.case, self.store, run, "F1", "edit", 0.5, 0.3)
        edited = confirm_belief(self.settings, self.case, self.store, run, "F1", "edit", 0.5, 0.3, "Human adjustment")
        self.assertEqual(edited["values"]["unknown"], 0.2)
        self.assertEqual(len(self.store.belief_reviews(run["run_id"])), 2)
        self.assertEqual(run["result"]["assessments"][0]["low"], 0.4)
        review_attribute(self.settings, self.store, self.evidence, "F1", {"F10": "Changed human interpretation"}, "new note", True)
        self.assertNotEqual(current_input(self.case, self.store)[3], run["reviewed_hash"])
        with self.assertRaisesRegex(ValueError, "stale"):
            confirm_belief(self.settings, self.case, self.store, run, "F1", "accept")
        self.assertIsNone(self.store.latest_beliefs(self.case, "mock-source", current_input(self.case, self.store)[3]))

    def test_accept_preserves_historical_values_after_increment_change(self):
        self.data["assessments"][0].update(low=0.35, high=0.45, unknown=0.2)
        run = self.run_action()
        changed = copy.deepcopy(get_config())
        changed["belief_assessment"]["increment"] = 0.1
        with patch("underwriting.assessment.belief_service.get_config", return_value=changed), patch("underwriting.assessment.belief_schema.get_config", return_value=changed):
            event = confirm_belief(self.settings, self.case, self.store, run, "F1", "accept")
        self.assertEqual(event["values"], {"low": 0.35, "high": 0.45, "unknown": 0.2})

    def test_outage_save_retry_and_failed_regeneration(self):
        with patch.object(self.store, "latest_beliefs", side_effect=StoreError("database outage")):
            with self.assertRaises(StoreError):
                self.run_action()
        self.request.assert_not_called()
        self.store.fail_belief_save = True
        with self.assertRaises(StoreError):
            self.run_action()
        retained = pending_beliefs(self.case, "mock-source")[0]
        self.store.fail_belief_save = False
        retry_belief_save(self.settings, self.store, retained)
        self.request.assert_called_once()
        self.data["assessments"][0]["low"] = 1.0
        failed = self.run_action(regenerate=True, confirmed=True, expected_run=retained["run_id"])
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(self.store.latest_beliefs(self.case, "mock-source", retained["reviewed_hash"])["run_id"], retained["run_id"])

    def test_ui_click_navigation_and_confirm(self):
        from streamlit.testing.v1 import AppTest
        # Case isolation: insert the same test-only fixture under the real selector's case ID/version.
        self.case.update(case_id="NB-2026-001", submission_version="1.0")
        self.evidence.update(case_id="NB-2026-001", source_version="1.0", run_id="mock-ui-evidence")
        self.store.save_evidence(self.evidence)
        for attribute in self.evidence["result"]["attributes"]:
            review_attribute(self.settings, self.store, self.evidence, attribute["attribute_id"], {item["evidence_id"]: item["extracted_fact"] for item in attribute["evidence_items"]}, "", True)
        # Extra metadata is used only for the existing evidence UI's provenance display.
        from underwriting.assessment.evidence_schema import attribute_definitions, attribute_hash, TEXT_EXTRACTION_VERSION
        from underwriting.configuration.settings import configuration_metadata
        self.evidence.update(prompt={"version": "1", "sha256": "mock"}, configuration=configuration_metadata(),
            attribute_configuration=attribute_definitions(), attribute_configuration_hash=attribute_hash(), schema_version="mock-schema",
            model="mock-model", text_extraction_version=TEXT_EXTRACTION_VERSION)
        # The fixture row is immutable; supply enriched metadata through the test adapter.
        evidence_tests.intake_tests.seed_submission(self.store,{"case_id":"NB-2026-001","submission_version":"1.0"},"mock-source")
        self.run_action()  # Existing canonical proposal; comparison must not replace it.
        original_latest = self.store.latest_evidence
        self.store.latest_evidence = lambda *args, **kwargs: self.evidence
        with patch("underwriting.ui.beliefs.settings_for_ui", return_value=self.settings), \
             patch("underwriting.ui.intake.settings_for_ui", return_value=Settings()), \
             patch("underwriting.ui.evidence.settings_for_ui", return_value=Settings()), \
             patch("underwriting.ui.beliefs.document_hash", return_value="mock-source"), \
             patch("underwriting.ui.evidence.document_hash", return_value="mock-source"), \
             patch("underwriting.ui.beliefs.BeliefStore", return_value=self.store), \
             patch("underwriting.assessment.belief_service.structured_request", self.request), \
             patch("underwriting.assessment.score_service.structured_request", side_effect=lambda settings,system,prompt,schema,name: ({"status":"completed"},json.dumps(__import__("comparison_fixtures").response(current_input(self.case,self.store)[2],"direct" if name=="attribute_direct_comparison" else "score")))):
            app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
            self.request.assert_called_once()
            self.assertFalse(app.exception)
            self.assertEqual([button.label for button in next(tab for tab in app.tabs if tab.label == "Assessment").button][:4], ["Load attribute evidence", "Load proposed beliefs", "Load saved Fire/Security results", "Load saved overall assessment"])
            self.assertFalse(app.button(key="case:NB-2026-001:calculate_brb").disabled)
            self.assertNotIn("Attribute belief summary", [item.value for item in app.subheader])
            prefix = "case:NB-2026-001"
            # The user must explicitly load evidence into this session before beliefs.
            app.session_state[f"{prefix}:1.0:mock-source:evidence"] = {
                "run": self.evidence, "attempt": self.evidence, "pending": [],
                "reviews": self.store.reviews(self.evidence["run_id"])}
            app.run()
            app.button(key=f"{prefix}:Propose beliefs").click().run()
            self.assertFalse(app.exception, app.exception)
            self.request.assert_called_once()
            self.assertTrue(any("Belief source: Turso database" in item.value for item in app.caption))
            self.assertEqual(app.session_state[prefix + ":assessment_stage"], "Beliefs")
            self.assertEqual(len(app.segmented_control), 0)
            self.assertTrue(any(item.value == "**Direct AI beliefs**" for item in next(tab for tab in app.tabs if tab.label == "Assessment").markdown))
            self.assertFalse(any(item.label == "Human evidence review" for item in next(tab for tab in app.tabs if tab.label == "Assessment").expander))
            self.assertFalse(any(item.value == "Fire and Security ER results" for item in next(tab for tab in app.tabs if tab.label == "Assessment").subheader))
            for name in ["F1 Combustible storage", "F2 Ignition controls", "F3 Fire protection", "S1 Access protection", "S2 Intrusion detection"]:
                app.button(key=f"{prefix}:select:{name}").click().run()
                self.assertFalse(app.exception)
            app.run()
            self.request.assert_called_once()
            app.button(key=f"{prefix}:Propose beliefs").click().run()
            self.assertTrue(any("Belief source:" in item.value for item in app.caption))
            self.request.assert_called_once()
            self.assertTrue(all(field.value == "" and field.disabled for field in app.text_input))
            app.button(key=f"{prefix}:select:F1 Combustible storage").click().run()
            next(field for field in app.text_area if field.label == "Reason for belief edits").set_value("Reviewed and confirmed by the underwriter").run()
            next(field for field in app.checkbox if field.label == "Confirm this attribute assessment").set_value(True).run()
            next(button for button in app.button if button.label == "Confirm beliefs").click().run()
            self.assertFalse(app.exception)
            run = app.session_state[f"{prefix}:1.0:mock-source:beliefs"]["comparison_direct"]
            self.assertEqual(self.store.belief_reviews(run["run_id"])[0]["values"], {"low": 0.2, "high": 0.6, "unknown": 0.2})
            self.request.assert_called_once()


if __name__ == "__main__":
    unittest.main()
