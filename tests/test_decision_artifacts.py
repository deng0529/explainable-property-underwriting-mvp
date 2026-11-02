"""Versioned Turso PDF artifacts and one-request Decision workflow checks."""
import hashlib
import unittest
from unittest.mock import patch
import pymupdf
from streamlit.testing.v1 import AppTest
from underwriting.decision.reports import ensure_report
from underwriting.decision.service import export_current_report, save_decision
from underwriting.persistence.intake import StoreError
from underwriting.persistence.decision import encoded
from underwriting.configuration.settings import ROOT
import test_decision as fixtures

class DecisionArtifactTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.DecisionTests();self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store,self.case=self.fixture.store,self.fixture.case
    def count(self):
        return int(self.store.execute('SELECT COUNT(*) FROM decision_reports')['rows'][0][0]['value'])
    def test_first_generation_persists_blob_then_load_and_download_reuse_it(self):
        run,_=self.fixture.generate()
        self.assertEqual(run['status'],'succeeded')
        self.assertEqual(self.count(),1)
        artifact=ensure_report(self.fixture.settings,self.case,self.store,run)
        self.assertEqual(artifact['review_run_id'],run['review_run_id'])
        self.assertEqual(artifact['snapshot_hash'],run['input_snapshot_hash'])
        self.assertEqual(artifact['report_hash'],hashlib.sha256(artifact['pdf']).hexdigest())
        with patch('underwriting.decision.report.render_report',side_effect=AssertionError('Do not rebuild a cached PDF')):
            again,_=self.fixture.generate()
            pdf=export_current_report(self.fixture.settings,self.case,self.store)
        self.assertEqual(again,run)
        self.assertEqual(pdf,artifact['pdf'])
        self.fixture.request.assert_called_once()
        self.assertEqual(self.count(),1)
        self.assertEqual(encoded(b'%PDF')['type'],'blob')
        (ROOT/'.runtime/cached-decision-review.pdf').write_bytes(pdf)
    def test_missing_pdf_and_new_template_rebuild_without_gpt(self):
        run,_=self.fixture.generate()
        # Remove only the fixture's local artifact, simulating a never-saved PDF.
        self.store.execute('DELETE FROM decision_reports')
        with patch('underwriting.decision.service.structured_request',side_effect=AssertionError('No GPT on report miss')):
            first=ensure_report(self.fixture.settings,self.case,self.store,run)
            newer=ensure_report(self.fixture.settings,self.case,self.store,run,template_version='concise-decision-test-next')
        self.assertNotEqual(first['report_id'],newer['report_id'])
        self.assertEqual(self.count(),2)
        self.fixture.request.assert_called_once()
    def test_human_version_changes_only_pdf_and_preserves_draft(self):
        run,_=self.fixture.generate()
        draft=ensure_report(self.fixture.settings,self.case,self.store,run)
        decision=save_decision(self.fixture.settings,self.case,self.store,run['review_run_id'],action='request_further_information',notes='Saved reviewer notes',reviewer_label='Reviewer',secondary_actions=[])
        final=ensure_report(self.fixture.settings,self.case,self.store,run)
        self.assertNotEqual(draft['report_id'],final['report_id'])
        self.assertEqual(final['human_decision_id'],decision['decision_id'])
        self.assertEqual(self.count(),2)
        self.assertEqual(self.store.review_by_id(run['review_run_id']),run)
        with pymupdf.open(stream=draft['pdf'],filetype='pdf') as document:
            self.assertIn('Draft review',' '.join(page.get_text() for page in document).replace('\xa0',' '))
        with pymupdf.open(stream=final['pdf'],filetype='pdf') as document:
            text=' '.join(page.get_text() for page in document).replace('\xa0',' ')
            self.assertIn('Saved human review',text)
            self.assertNotIn('Saved reviewer notes',text)
        self.fixture.request.assert_called_once()
    def test_pdf_save_failure_keeps_review_and_retry_never_repeats_gpt(self):
        events=[]
        with patch.object(self.store,'save_report',side_effect=StoreError('Mock artifact save outage')):
            run,_=self.fixture.generate(progress=lambda stage,message:events.append(stage))
        self.assertEqual(run['status'],'succeeded')
        self.assertEqual(self.store.review_by_id(run['review_run_id']),run)
        self.assertIn('Report retry needed',events)
        with patch('underwriting.decision.service.structured_request',side_effect=AssertionError('Retry must not call GPT')):
            artifact=ensure_report(self.fixture.settings,self.case,self.store,run)
            self.assertTrue(artifact['pdf'].startswith(b'%PDF'))
        self.fixture.request.assert_called_once()
    def test_two_tabs_and_unsaved_human_drafts_survive_navigation(self):
        run,_=self.fixture.generate()
        script='''from underwriting.ui.decision import render_decision
render_decision({'case_id':'TEST-BELIEF','submission_version':'1','document_path':'unused.pdf'},'case:TEST-BELIEF')
'''
        with patch('underwriting.ui.decision.settings_for_ui',return_value=self.fixture.settings),patch('underwriting.ui.decision.DecisionStore',return_value=self.store),patch('underwriting.ui.decision.document_hash',return_value='mock-source'),patch('underwriting.decision.service.structured_request',side_effect=AssertionError('No GPT on navigation')):
            app=AppTest.from_string(script,default_timeout=30).run()
            app.button(key='case:TEST-BELIEF:1:decision:load').click().run()
            self.assertFalse(app.exception)
            self.assertEqual([tab.label for tab in app.tabs],['AI review summary','Human decision'])
            self.assertFalse(app.text_area)
            app.session_state['case:TEST-BELIEF:1:decision:sections']='Human decision';app.run()
            self.assertEqual(len(app.text_area),1)
            app.text_area[0].input('Unsaved human draft').run()
            with patch.object(self.store,'execute',side_effect=AssertionError('Tabs must not query/write')):
                app.session_state['case:TEST-BELIEF:1:decision:sections']='AI review summary';app.run()
                self.assertFalse(app.exception)
                self.assertFalse(app.text_area)
                app.session_state['case:TEST-BELIEF:1:decision:sections']='Human decision';app.run()
            self.assertEqual(app.text_area[0].value,'Unsaved human draft')
            self.fixture.request.assert_called_once()

    def test_readonly_retrieves_blob_but_cannot_build_missing_version(self):
        from dataclasses import replace
        run,_=self.fixture.generate()
        readonly=replace(self.fixture.settings,access_mode='public_readonly')
        self.store.settings=readonly
        with patch('underwriting.decision.report.render_report',side_effect=AssertionError('Readonly must not format PDF')):
            self.assertTrue(export_current_report(readonly,self.case,self.store).startswith(b'%PDF'))
            with self.assertRaises(PermissionError):
                ensure_report(readonly,self.case,self.store,run,template_version='missing-readonly-version')
        self.fixture.request.assert_called_once()

    def test_pdf_creation_error_does_not_change_successful_review(self):
        events=[]
        with patch('underwriting.decision.report.render_report',side_effect=RuntimeError('Mock formatting failure')):
            run,_=self.fixture.generate(progress=lambda stage,message:events.append(stage))
        self.assertEqual(run['status'],'succeeded')
        self.assertIn('Report retry needed',events)
        self.assertEqual(self.store.review_by_id(run['review_run_id']),run)
        self.assertTrue(ensure_report(self.fixture.settings,self.case,self.store,run)['pdf'].startswith(b'%PDF'))
        self.fixture.request.assert_called_once()

    def test_repeat_button_loads_and_fresh_session_use_database_with_zero_extra_gpt(self):
        run,_=self.fixture.generate()
        script='''from underwriting.ui.decision import render_decision
render_decision({'case_id':'TEST-BELIEF','submission_version':'1','document_path':'unused.pdf'},'case:TEST-BELIEF')
'''
        with patch('underwriting.ui.decision.settings_for_ui',return_value=self.fixture.settings),patch('underwriting.ui.decision.DecisionStore',return_value=self.store),patch('underwriting.ui.decision.document_hash',return_value='mock-source'),patch('underwriting.decision.service.structured_request',side_effect=AssertionError('Cached button load must never call GPT')):
            for _ in range(2):
                app=AppTest.from_string(script,default_timeout=30).run()
                for _ in range(2):
                    app.button(key='case:TEST-BELIEF:1:decision:load').click().run()
                    self.assertFalse(app.exception)
                    self.assertEqual(app.session_state['case:TEST-BELIEF:1:decision']['run']['review_run_id'],run['review_run_id'])
                    self.assertIn('Loaded from database — no GPT call on this load',[item.value for item in app.caption])
        self.fixture.request.assert_called_once()
