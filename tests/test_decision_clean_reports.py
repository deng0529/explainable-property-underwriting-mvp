"""Decision-only DB export, field mapping errors and clean current/history display."""
import copy
import json
import unittest
from unittest.mock import patch
import pymupdf
import streamlit as st
from streamlit.testing.v1 import AppTest
from underwriting.configuration.settings import ROOT
from underwriting.decision.fields import submission_field
from underwriting.decision.snapshot import assemble_snapshot
from underwriting.decision.service import export_current_report
from underwriting.persistence.intake import StoreError
import test_decision as fixtures

class CleanDecisionTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.DecisionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store,self.case=self.fixture.store,self.fixture.case
    def text(self,pdf):
        with pymupdf.open(stream=pdf,filetype='pdf') as document:
            self.assertLessEqual(len(document),2)
            return '\n'.join(page.get_text() for page in document).replace('\xa0',' ').replace('\xad','-')
    def test_export_after_restart_without_any_browser_stage_state(self):
        self.fixture.generate()
        with patch('underwriting.decision.service.structured_request',side_effect=AssertionError('No GPT on download')):
            pdf=export_current_report(self.fixture.settings,self.case,self.store)
        text=self.text(pdf)
        self.assertIn('Furniture storage/distribution without manufacturing',text)
        self.assertIn('Test site',text)
        self.assertIn('Proceed to assessment',text)
        self.assertNotIn('not provided',text.casefold())
        self.assertNotIn('provenance',text.casefold())
        self.assertNotIn('mock-intake',text)
        self.fixture.request.assert_called_once()
        (ROOT/'.runtime/clean-current-decision-report.pdf').write_bytes(pdf)
    def test_missing_mapping_is_error_and_not_provided_is_only_saved_status(self):
        snapshot=assemble_snapshot(self.case,self.store)
        rows=snapshot['submission']['extraction_and_screening']['basic_information']
        address=next(row for row in rows if row['field_id']=='site_address')
        self.assertEqual(submission_field(snapshot,'site_address'),'Test site')
        address.update(status='not_provided',value=None)
        self.assertEqual(submission_field(snapshot,'site_address'),'Not provided')
        address.update(status='conflicting',value=None)
        self.assertIn('Conflicting',submission_field(snapshot,'site_address'))
        address.update(status='found',value=None)
        with self.assertRaisesRegex(ValueError,'invalid value'):
            submission_field(snapshot,'site_address')
        rows.remove(address)
        with self.assertRaisesRegex(ValueError,'mapping'):
            submission_field(snapshot,'site_address')
        with patch.object(self.store,'execute',side_effect=StoreError('Database unavailable')):
            with self.assertRaises(StoreError):
                export_current_report(self.fixture.settings,self.case,self.store)
        self.fixture.request.assert_not_called()
    def test_historical_narrative_does_not_enter_current_pending_pdf(self):
        paused=copy.deepcopy(self.fixture.intake)
        paused['result']['screening_checks'][-1].update(result='unknown',reason='Clarify site scope')
        paused['outcome']='Pause for clarification'
        response=fixtures.review_data();response['screening_outcome']='Pause for clarification'
        response['review_summary']='Screening remains paused. Historical recommendation only.'
        self.fixture.request.side_effect=lambda *_: ({'status':'completed'},json.dumps(response))
        with patch.object(self.store,'latest',return_value=paused):
            old,_=self.fixture.generate()
        with self.assertRaisesRegex(ValueError,'Generate the current Decision review'):
            export_current_report(self.fixture.settings,self.case,self.store)
        self.assertEqual(self.store.review_by_id(old['review_run_id']),old)
        self.fixture.request.assert_called_once()
    def test_valid_current_review_beats_later_historical_and_invalid_records(self):
        current,_=self.fixture.generate()
        historical=copy.deepcopy(current)
        historical.update(review_run_id='later-historical',created_at='2099-01-01')
        historical['input_snapshot_hash']='historical-snapshot-hash'
        self.store.save_decision_review(historical)
        invalid=copy.deepcopy(current)
        invalid.update(review_run_id='later-inconsistent',created_at='2099-01-02')
        invalid['review']['screening_outcome']='Pause for clarification'
        self.store.save_decision_review(invalid)
        chosen,_=self.fixture.generate()
        self.assertEqual(chosen['review_run_id'],current['review_run_id'])
        self.assertIsNotNone(self.store.review_by_id(historical['review_run_id']))
        self.fixture.request.assert_called_once()
    def test_clean_decision_only_ui_one_summary_and_direct_download(self):
        current,_=self.fixture.generate()
        script='''import streamlit as st
from underwriting.ui.decision import render_decision
render_decision({'case_id':'TEST-BELIEF','submission_version':'1','document_path':'unused.pdf'},'case:TEST-BELIEF')
'''
        real_download=st.download_button
        callbacks=[]
        def download(label,**kwargs):
            if label=='Download review report':
                callbacks.append(kwargs['data'])
            return real_download(label,**kwargs)
        with patch('underwriting.ui.decision.settings_for_ui',return_value=self.fixture.settings), patch('underwriting.ui.decision.DecisionStore',return_value=self.store), patch('underwriting.ui.decision.document_hash',return_value='mock-source'), patch.object(st,'download_button',side_effect=download), patch('underwriting.decision.service.structured_request',side_effect=AssertionError('No GPT')):
            with patch.object(self.store,'execute',side_effect=AssertionError('No startup database query')):
                app=AppTest.from_string(script).run()
            self.assertFalse(app.exception)
            # Report works before loading the UI, without any upstream tab state.
            self.assertIn('Test site',self.text(callbacks[-1]()))
            app.button(key='case:TEST-BELIEF:1:decision:load').click().run()
            self.assertFalse(app.exception)
            self.assertEqual([tab.label for tab in app.tabs],['AI review summary','Human decision'])
            self.assertEqual(len(app.json),0)
            for title in ('Concern sources and references','Information-request sources and references','Action references, secondary suggestions and limitations','Saved snapshot and review versions'):
                self.assertFalse(any(item.label==title for item in app.expander))
            self.assertEqual(len(app.text_area),0)
            app.session_state['case:TEST-BELIEF:1:decision:sections']='Human decision'
            app.run()
            self.assertEqual(len(app.text_area),1)
            self.assertIn('Underwriter notes',app.text_area[0].label)
            self.assertFalse(next(button for button in app.button if button.label=='Save decision').disabled)
        self.fixture.request.assert_called_once()

    def test_loading_other_tabs_same_or_older_records_does_not_make_current_review_historical(self):
        current,_=self.fixture.generate()
        script='''import streamlit as st
from underwriting.ui.decision import render_decision
render_decision({'case_id':'TEST-BELIEF','submission_version':'1','document_path':'unused.pdf'},'case:TEST-BELIEF')
'''
        with patch('underwriting.ui.decision.settings_for_ui',return_value=self.fixture.settings), patch('underwriting.ui.decision.DecisionStore',return_value=self.store), patch('underwriting.ui.decision.document_hash',return_value='mock-source'):
            app=AppTest.from_string(script).run()
            app.button(key='case:TEST-BELIEF:1:decision:load').click().run()
            app.session_state['case:TEST-BELIEF:1:mock-source:evidence']={'run':{'run_id':'older-run','finished_at':'2020-01-01'},'reviews':[]}
            app.session_state['case:TEST-BELIEF:1:mock-source:intake_view']={'saved':self.fixture.intake}
            with patch.object(self.store,'execute',side_effect=AssertionError('Navigation must not query')):
                app.run()
            self.assertFalse(app.exception)
            self.assertFalse(app.session_state['case:TEST-BELIEF:1:decision']['stale'])
            self.assertEqual(app.session_state['case:TEST-BELIEF:1:decision']['run']['review_run_id'],current['review_run_id'])
            self.assertNotIn('Historical review — not current',[item.value for item in app.caption])
