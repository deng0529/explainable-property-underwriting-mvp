"""Human intake review tests against local SQLite; no paid or hosted calls."""
import copy
import unittest
from dataclasses import replace
from unittest.mock import patch

from underwriting.persistence.submission_reviews import SubmissionReviewStore
from underwriting.intake.reviews import save_submission_review
import test_intake as fixtures


class LocalReviewStore(SubmissionReviewStore):
    def __init__(self, store):
        self.settings, self.db = store.settings, store.db
        self.fail_save = False

    execute = fixtures.SQLiteStore.execute


class SubmissionReviewTests(unittest.TestCase):
    setUp = fixtures.IntakeTests.setUp
    tearDown = fixtures.IntakeTests.tearDown
    run_action = fixtures.IntakeTests.run_action

    def draft(self):
        return {"action": "confirm", "basic_information_notes": "Verify floor area with broker.",
                "screening_notes": {f"SC{i:02}": "Check noted" if i == 4 else "" for i in range(1, 7)},
                "basic_information_reviewed": True, "screening_reviewed": True,
                "recipient": None, "reason": ""}

    def save(self, run, draft, settings=None):
        with patch("underwriting.intake.reviews.source_context", return_value=("test-hash", [fixtures.TEXT])):
            return save_submission_review(settings or self.settings, self.case, run, draft, LocalReviewStore(self.store))

    def test_confirm_and_referral_append_without_altering_ai_results(self):
        run = self.run_action()
        original = copy.deepcopy(run)
        first = self.save(run, self.draft())
        self.assertEqual(first["status"], "review_confirmed")
        referral = dict(self.draft(), action="request_review", recipient="Broker", reason="Please confirm occupancy.")
        second = self.save(run, referral)
        self.assertEqual(second["status"], "review_requested")
        self.assertEqual(second["dispatch_status"], "not_sent")
        self.assertEqual(run, original)
        self.assertNotEqual(first["event_id"], second["event_id"])
        history = LocalReviewStore(self.store).review_history(self.case, run)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["screening_notes"]["SC04"], "Check noted")
        self.assertEqual(LocalReviewStore(self.store).review_history(self.case, dict(run, run_id="other-run")), [])
        self.assertEqual(self.request.call_count, 1)  # Mock intake only, no review GPT request.

    def test_confirmation_requires_both_acknowledgements_and_referral_reason(self):
        run = self.run_action()
        for flag in ("basic_information_reviewed", "screening_reviewed"):
            with self.assertRaisesRegex(ValueError, "both basic information"):
                self.save(run, dict(self.draft(), **{flag: False}))
        with self.assertRaisesRegex(ValueError, "recipient"):
            self.save(run, dict(self.draft(), action="request_review", recipient="Broker", reason=""))
        with self.assertRaisesRegex(ValueError, "complete set"):
            self.save(run, dict(self.draft(), screening_notes={}))

    def test_review_does_not_clear_pause_or_validation_issues(self):
        run = self.run_action()
        run.update(status="needs_review", outcome="Pause for clarification", validation_issues=["Mock quotation issue"])
        saved = self.save(run, self.draft())
        self.assertEqual(saved["original_screening_outcome"], "Pause for clarification")
        self.assertEqual(saved["validation_issues"], ["Mock quotation issue"])
        self.assertEqual(run["status"], "needs_review")

    def test_changed_source_and_readonly_block_saving(self):
        run = self.run_action()
        with self.assertRaisesRegex(ValueError, "source changed"):
            self.save(dict(run, source_hash="old-document"), self.draft())
        with self.assertRaises(PermissionError):
            self.save(run, self.draft(), replace(self.settings, access_mode="public_readonly"))

    def test_ui_notes_survive_check_selection_and_failed_save_then_confirm(self):
        from streamlit.testing.v1 import AppTest
        from underwriting.persistence.intake import StoreError
        run = self.run_action()
        view = {"settings": self.settings, "saved": run}
        store = LocalReviewStore(self.store)
        script = '''import streamlit as st
from underwriting.ui.submission_review import basic_review_notes, screening_review_notes, review_controls
case = {"case_id": "TEST-001", "submission_version": "1.0", "document_path": "unused.pdf"}
view = st.session_state["view"]
basic_review_notes(case, view)
check = st.radio("Selected check", ["SC01", "SC02"])
screening_review_notes(case, view, check)
review_controls(case, view)
'''
        key = "case:TEST-001:submission_review:" + run["run_id"]
        with patch("underwriting.ui.submission_review.SubmissionReviewStore", return_value=store), \
             patch("underwriting.intake.reviews.source_context", return_value=("test-hash", [fixtures.TEXT])), \
             patch("underwriting.intake.service.OpenAI", side_effect=AssertionError("No review GPT calls")) as client:
            app = AppTest.from_string(script)
            app.session_state["view"] = view
            app.run()
            self.assertFalse(app.exception)
            app.text_area(key=key + ":basic_notes").input("Please verify declared values.").run()
            app.text_area(key=key + ":check:SC01").input("Confirm address.").run()
            next(widget for widget in app.radio if widget.label == "Selected check").set_value("SC02").run()
            app.text_area(key=key + ":check:SC02").input("Territory reviewed.").run()
            next(widget for widget in app.radio if widget.label == "Selected check").set_value("SC01").run()
            self.assertEqual(app.text_area(key=key + ":check:SC01").value, "Confirm address.")
            app.button(key=key + ":save").click().run()
            self.assertTrue(any("both basic information" in item.value for item in app.error))
            app.checkbox(key=key + ":basic_checked").check().run()
            app.checkbox(key=key + ":screening_checked").check().run()
            with patch.object(store, "save_review", side_effect=StoreError("Mock save failure")):
                app.button(key=key + ":save").click().run()
                self.assertTrue(any("Mock save failure" in item.value for item in app.error))
                self.assertEqual(app.text_area(key=key + ":basic_notes").value, "Please verify declared values.")
            app.button(key=key + ":save").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any("open the Assessment" in item.value for item in app.success))
            record = store.review_history(self.case, run)[0]
            self.assertEqual(record["screening_notes"]["SC01"], "Confirm address.")
            self.assertEqual(record["screening_notes"]["SC02"], "Territory reviewed.")
            app.radio(key=key + ":action").set_value("Request clarification or specialist review").run()
            app.selectbox(key=key + ":recipient").set_value("Senior underwriter").run()
            app.text_area(key=key + ":reason").input("Please review the occupancy discrepancy.").run()
            app.button(key=key + ":save").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any("No message has been sent" in item.value for item in app.success))
            self.assertEqual(len(store.review_history(self.case, run)), 2)
            self.assertEqual(store.review_history(self.case, run)[0]["recipient"], "Senior underwriter")
            client.assert_not_called()
