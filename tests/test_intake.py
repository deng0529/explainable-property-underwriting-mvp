"""Mock-only intake tests. Never read real credentials or call hosted services."""
import copy
import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from underwriting.configuration.access import Settings, ROOT
from underwriting.intake.schema import IntakeOutput, UI_TO_PROMPT, fields_definition, validate_output, derive_outcome
from underwriting.intake.service import run_intake, retry_save, pending, prompt_context, request_once
from underwriting.persistence.intake import TursoStore, StoreError

TEXT = "Test-only company at Test site, UK. Furniture storage/distribution without manufacturing. One occupied site. Property damage requested. Alternative test statement."
EVIDENCE = {"page": 1, "section": "Test fixture", "quote": TEXT, "attribution": "Mock fixture"}


def response_data():
    fields = [{"field_id": UI_TO_PROMPT.get(f["id"], f["id"]), "value": None, "unit": None,
               "status": "not_provided", "evidence": [], "note": None} for f in fields_definition()]
    populated = {"company_name": "Test-only company", "site_address": "Test site", "country_or_territory": "UK",
                 "business_description": "Furniture storage/distribution without manufacturing", "site_count": 1,
                 "occupancy": "Occupied", "requested_insurance_sections": ["Property damage"]}
    for field in fields:
        if field["field_id"] in populated:
            field.update(value=populated[field["field_id"]], status="found", evidence=[copy.deepcopy(EVIDENCE)])
    return {"basic_information": fields, "screening_checks": [
        {"check_id": f"SC{i:02}", "result": "pass", "reason": "Mock source supports this check.",
         "supporting_field_ids": [], "evidence": [copy.deepcopy(EVIDENCE)]} for i in range(1, 7)],
        "non_blocking_review_flags": []}


class SQLiteStore(TursoStore):
    """Exercise the same production SQL against in-memory SQLite, not a fake state dictionary."""
    def __init__(self, settings):
        self.settings = settings
        self.db = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
        self.fail_save = False

    def execute(self, sql, args=(), missing_ok=False):
        if self.fail_save and sql.startswith("INSERT OR IGNORE INTO submission_intake_runs"):
            raise StoreError("Mock save failure")
        try:
            cursor = self.db.execute(sql, args)
        except sqlite3.OperationalError as error:
            if missing_ok and "no such table" in str(error):
                return {"rows": [], "affected_row_count": 0}
            raise
        rows = [[{"type": "text", "value": str(value)} for value in row] for row in cursor.fetchall()]
        return {"rows": rows, "affected_row_count": cursor.rowcount}


class IntakeTests(unittest.TestCase):
    def test_saved_review_issues_are_clear_without_technical_banners(self):
        from streamlit.testing.v1 import AppTest
        self.data["screening_checks"][0]["evidence"][0]["quote"] = "Unsupported mock quotation"
        run = self.run_action()
        self.assertEqual(run["status"], "needs_review")
        original = copy.deepcopy(run)
        script = '''import streamlit as st
from underwriting.ui.intake import controls
st.session_state.setdefault("document", False)
controls({"case_id": "TEST-001", "submission_version": "1.0"}, "test", st.session_state["view"], "document", lambda *args: None)
'''
        view = {"settings": self.settings, "saved": run, "latest_attempt": run, "store": self.store,
                "pending": [], "source_hash": "test-hash", "stale": False, "error": None}
        with patch("underwriting.ui.intake.configuration_metadata", return_value={"version": "new", "sha256": "new"}):
            app = AppTest.from_string(script)
            app.session_state["view"] = view
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.info), 0)
            self.assertEqual(len(app.warning), 0)
            self.assertTrue(any("Review required" in item.label for item in app.expander))
            self.assertTrue(any("SC01: unsupported quotation" in item.value for item in app.text))
        self.assertEqual(run, original)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.temp.name)
        (self.temp_root / "prompts").mkdir()
        for name in ("system", "user"):
            file = f"submission_intake_{name}_v1.txt"
            (self.temp_root / "prompts" / file).write_bytes((ROOT / "prompts" / file).read_bytes())
        self.root_patch = patch("underwriting.intake.service.ROOT", self.temp_root)
        self.root_patch.start()
        self.settings = Settings("test-key", "test-model", "https://test.invalid", "test-token", "local_owner")
        self.proof = None
        self.local_patch = patch("underwriting.configuration.access.local_server", return_value=True)
        self.local_patch.start()
        self.store = SQLiteStore(self.settings)
        self.case = {"case_id": "TEST-001", "submission_version": "1.0", "document_path": "unused.pdf"}
        self.source_patch = patch("underwriting.intake.service.source_context", return_value=("test-hash", [TEXT]))
        self.source_patch.start()
        self.data = response_data()
        self.request = Mock(side_effect=lambda *_: ({"status": "completed", "model": "mock-model", "usage": {"input_tokens": 10, "output_tokens": 20}}, json.dumps(self.data)))

    def tearDown(self):
        self.local_patch.stop()
        self.source_patch.stop()
        self.root_patch.stop()
        self.store.db.close()
        self.temp.cleanup()

    def run_action(self, **kwargs):
        return run_intake(self.settings, self.proof, self.case, self.store, request=self.request, **kwargs)

    def test_typed_output_mapping_missing_and_conflicting(self):
        parsed, issues = validate_output(self.data, [TEXT], "TEST-001")
        self.assertEqual(issues, [])
        self.assertIn("employee_count", {f["field_id"] for f in parsed["basic_information"]})
        company = next(f for f in self.data["basic_information"] if f["field_id"] == "company_name")
        company.update(value=None, status="conflicting", note="Competing test statements", evidence=[EVIDENCE, EVIDENCE])
        self.data["screening_checks"][0]["result"] = "unknown"
        self.data["screening_checks"][5]["result"] = "unknown"
        result, issues = validate_output(self.data, [TEXT], "TEST-001")
        self.assertIsNone(result["basic_information"][5]["value"])
        self.assertEqual(derive_outcome(result["screening_checks"], issues), "Pause for clarification")

    def test_policy_all_outcomes(self):
        checks = self.data["screening_checks"]
        self.assertEqual(derive_outcome(checks), "Proceed to assessment")
        for check in checks:
            check["result"] = "unknown"
            self.assertEqual(derive_outcome(checks), "Pause for clarification")
            check["result"] = "pass"
        for index in (1, 2, 3, 4):
            checks[index]["result"] = "fail"
            self.assertEqual(derive_outcome(checks), "Outside demo scope")
            checks[index]["result"] = "pass"
        for index in (0, 5):
            checks[index]["result"] = "fail"
            self.assertEqual(derive_outcome(checks), "Pause for clarification")
            checks[index]["result"] = "pass"

    def test_validation_ids_units_losses_quotes(self):
        bad = copy.deepcopy(self.data)
        bad["basic_information"].append(bad["basic_information"][0])
        with self.assertRaises(ValueError):
            validate_output(bad, [TEXT], "TEST-001")
        bad = copy.deepcopy(self.data)
        bad["screening_checks"].pop()
        with self.assertRaises(ValueError):
            validate_output(bad, [TEXT], "TEST-001")
        self.data["basic_information"][5]["evidence"][0]["quote"] = "An invented quote"
        result, issues = validate_output(self.data, [TEXT], "TEST-001")
        self.assertTrue(issues)
        self.assertEqual(derive_outcome(result["screening_checks"], issues), "Proceed to assessment")
        data = response_data()
        field = next(f for f in data["basic_information"] if f["field_id"] == "buildings_reinstatement_value")
        field.update(value=20, unit=None, status="found", evidence=[EVIDENCE])
        with self.assertRaises(ValueError):
            validate_output(data, [TEXT], "TEST-001")
        data = response_data()
        field = next(f for f in data["basic_information"] if f["field_id"] == "previous_losses")
        field.update(value=[{"loss_date": "2024-06", "description": "test", "amount_paid": 20.0,
                            "outstanding_amount": None, "remedial_action": None, "evidence": [EVIDENCE]}],
                     unit="GBP", status="found", evidence=[EVIDENCE])
        validate_output(data, [TEXT], "TEST-001")

    def test_normalisation_and_prompt_exception(self):
        data = response_data()
        data["screening_checks"][0]["evidence"][0]["quote"] = TEXT.replace(" ", "\n")
        _, issues = validate_output(data, [TEXT], "TEST-001")
        self.assertFalse(issues)
        system, user, hashes = prompt_context(self.case, [TEXT])
        self.assertIn("Synthetic demonstration case: false", user)
        self.assertIn("[PAGE 1]", user)
        self.assertIn("Synthetic demonstration case: true", prompt_context(dict(self.case, case_id="NB-2026-001"), [TEXT])[1])
        self.assertEqual(len(hashes), 2)
        self.assertIn("Do not assess the five Fire/Security attributes", system)

    def test_save_reload_isolation_no_paid_key(self):
        saved = self.run_action()
        self.assertEqual(saved["status"], "completed")
        self.assertEqual(self.store.latest("TEST-001", "1.0", "test-hash")["run_id"], saved["run_id"])
        self.assertIsNone(self.store.latest("OTHER", "1.0", "test-hash"))
        self.assertIsNone(self.store.latest("TEST-001", "1.0", "changed-hash"))
        self.assertFalse(pending("TEST-001", "1.0", "test-hash"))
        self.request.assert_called_once()
        self.assertEqual(self.run_action()["run_id"], saved["run_id"])
        self.assertEqual(self.request.call_count, 1)
        self.run_action(expected_run=saved["run_id"], confirmed=True)
        self.assertEqual(self.request.call_count, 2)

    def test_save_retry_never_repeats_gpt(self):
        self.store.fail_save = True
        with self.assertRaises(StoreError):
            self.run_action()
        retained = pending("TEST-001", "1.0", "test-hash")
        self.assertEqual(len(retained), 1)
        with self.assertRaises(ValueError):
            self.run_action()
        self.store.fail_save = False
        # A save needs authorisation and database access, but no OpenAI configuration.
        without_key = Settings(database_url="https://test.invalid", database_token="test", access_mode="local_owner")
        retry_save(without_key, self.proof, self.store, retained[0])
        self.assertEqual(self.request.call_count, 1)
        self.assertTrue(self.store.latest("TEST-001", "1.0", "test-hash"))

    def test_failed_run_preserves_success_and_owner_enforced(self):
        saved = self.run_action()
        self.request.side_effect = RuntimeError("Mock GPT failure")
        failed = self.run_action(expected_run=saved["run_id"], confirmed=True)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(self.store.latest("TEST-001", "1.0", "test-hash")["run_id"], saved["run_id"])
        from dataclasses import replace
        readonly = replace(self.settings, access_mode="public_readonly")
        with self.assertRaises(PermissionError):
            run_intake(readonly, None, self.case, self.store, request=self.request)
        with patch.object(self.store, "settings", readonly):
            with self.assertRaises(PermissionError):
                self.store.save(saved, None)

    def test_timeout_diagnostics_are_specific_without_secret_details(self):
        import httpx
        from openai import APITimeoutError, AuthenticationError
        from underwriting.intake.service import request_failure
        self.request.side_effect = APITimeoutError(request=httpx.Request("POST", "https://test.invalid"))
        failed = self.run_action()
        self.assertEqual(failed["error_diagnostics"]["kind"], "timeout")
        self.assertIn("120-second timeout", failed["error"])
        self.request.assert_called_once()
        error = AuthenticationError("must-not-display-private-body", response=httpx.Response(401, request=httpx.Request("POST", "https://test.invalid")), body=None)
        diagnostic = request_failure(error, 300)
        self.assertEqual(diagnostic["http_status"], 401)
        self.assertNotIn("must-not-display", str(diagnostic))

    def test_progress_and_loss_format_review_preserve_original_values(self):
        loss = next(field for field in self.data["basic_information"] if field["field_id"] == "previous_losses")
        loss.update(value=[{"loss_date": "June 2024", "description": "Mock loss", "amount_paid": 20.0,
                            "outstanding_amount": None, "remedial_action": None, "evidence": [EVIDENCE]}],
                    unit=None, status="found", evidence=[])
        stages = []
        saved = self.run_action(progress=lambda stage, detail: stages.append(stage))
        self.assertEqual(saved["status"], "needs_review")
        self.assertEqual(saved["outcome"], "Proceed to assessment")
        actual = next(field for field in saved["result"]["basic_information"] if field["field_id"] == "previous_losses")
        self.assertEqual(actual["value"][0]["loss_date"], "June 2024")
        self.assertIsNone(actual["unit"])
        self.assertTrue(any("currency" in issue for issue in saved["validation_issues"]))
        self.assertTrue(any("ISO" in issue for issue in saved["validation_issues"]))
        self.assertLess(stages.index("Database"), stages.index("GPT"))
        self.assertLess(stages.index("GPT"), stages.index("Validation"))
        self.assertLess(stages.index("Validation"), stages.index("Save"))
        self.request.assert_called_once()

    def test_consumed_claim_and_sdk_retries(self):
        self.store.initialise(self.proof)
        key = hashlib.sha256(json.dumps(["TEST-001", "1.0", "test-hash", None]).encode()).hexdigest()
        self.store.claim(key, "other-request", self.proof)
        with self.assertRaises(ValueError):
            self.run_action()
        self.request.assert_not_called()
        with patch("underwriting.intake.service.OpenAI") as client:
            result = client.return_value.__enter__.return_value.responses.create.return_value
            result.model_dump.return_value = {"status": "completed"}
            result.output_text = "{}"
            request_once(self.settings, "system", "user")
            self.assertEqual(client.call_args.kwargs["max_retries"], 0)
            client.return_value.__enter__.return_value.responses.create.assert_called_once()

    def test_simultaneous_request_is_blocked(self):
        # Hold the same process-wide request lock and verify a second handler cannot enter.
        import underwriting.intake.service as intake_service
        key = (self.case["case_id"], self.case["submission_version"], "test-hash")
        lock = intake_service._locks.setdefault(key, threading.Lock())
        lock.acquire()
        try:
            with self.assertRaisesRegex(ValueError, "already running"):
                self.run_action()
        finally:
            lock.release()
        self.request.assert_not_called()

    def test_ui_explicit_action_saved_reload_and_navigation(self):
        from streamlit.testing.v1 import AppTest
        self.case["case_id"] = "NB-2026-001"
        # Typed alias mapping is exercised with mock values, not the supplied case answers.
        employee = next(f for f in self.data["basic_information"] if f["field_id"] == "employee_count")
        employee.update(value=7, status="found", evidence=[EVIDENCE])
        self.store.initialise(self.proof)
        with patch("underwriting.ui.intake.settings_for_ui", return_value=self.settings), \
             patch("underwriting.ui.intake.source_context", return_value=("test-hash", [TEXT])), \
             patch("underwriting.ui.intake.TursoStore", return_value=self.store), \
             patch("underwriting.intake.service.request_once", self.request):
            app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
            self.assertFalse(app.exception)
            self.request.assert_not_called()
            self.assertFalse(app.button(key="case:NB-2026-001:extract_and_screen").disabled)
            app.button(key="case:NB-2026-001:extract_and_screen").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(self.request.call_count, 1)
            self.assertIn("Test-only company", "\n".join(item.value for item in app.html))
            self.assertIn('>7</td>', "\n".join(item.value for item in app.html))
            app.radio(key="case:NB-2026-001:information_category").set_value("Completeness").run()
            app.session_state["case:NB-2026-001:submission_section"] = "Screening"
            app.run()
            app.radio(key="case:NB-2026-001:screening_check").set_value("SC03").run()
            app.run()
            self.assertEqual(self.request.call_count, 1)
            # A new visitor session reloads stored data even without a paid API key.
            no_key = Settings(database_url="https://test.invalid", database_token="test-token")
            with patch("underwriting.ui.intake.settings_for_ui", return_value=no_key):
                visitor = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
                self.assertFalse(visitor.exception)
                self.assertNotIn("Test-only company", "\n".join(item.value for item in visitor.html))
                visitor.button(key="case:NB-2026-001:extract_and_screen").click().run()
                self.assertIn("Test-only company", "\n".join(item.value for item in visitor.html))
                self.assertFalse(visitor.button(key="case:NB-2026-001:extract_and_screen").disabled)
                self.assertEqual(self.request.call_count, 1)
                original = self.store.latest("NB-2026-001", "1.0", "test-hash")
                with patch("underwriting.ui.intake.configuration_metadata", return_value={"version": "changed-config", "sha256": "changed-hash"}):
                    visitor.run()
                    self.assertFalse(visitor.exception)
                    self.assertFalse(any("configuration differs" in item.value for item in visitor.info))
                    self.assertTrue(any("Saved with earlier settings" in item.value for item in visitor.caption))
                    self.assertIn("Test-only company", "\n".join(item.value for item in visitor.html))
                    self.assertEqual(self.store.latest("NB-2026-001", "1.0", "test-hash"), original)
                    self.assertEqual(self.request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
