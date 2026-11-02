"""Shared screening policy, snapshot freshness and concise saved-record PDF checks."""
import copy
import json
import unittest
from unittest.mock import patch
import pymupdf
from streamlit.testing.v1 import AppTest
from underwriting.intake.screening import resolve_checks, resolve_screening
from underwriting.decision.snapshot import assemble_snapshot, snapshot_hash
from underwriting.decision.service import load_generate
from underwriting.decision.schema import validate_review
from underwriting.decision.report import render_report
from underwriting.configuration.settings import ROOT
import test_decision as fixtures

class ScreeningResolutionTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.DecisionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store=self.fixture.store
        self.case=self.fixture.case

    def test_six_pass_and_nonblocking_issues_proceed_everywhere(self):
        intake=copy.deepcopy(self.fixture.intake)
        intake.update(status='needs_review',outcome='Pause for clarification',validation_issues=['Review flag: unsupported quotation','previous_losses: currency missing'])
        intake['result']['non_blocking_review_flags']=[{'issue':'Specialist protection scope needs clarification','information_required':'Confirm extension coverage','evidence':[]}]
        original=copy.deepcopy(intake)
        with patch.object(self.store,'latest',return_value=intake):
            resolution=resolve_screening(intake,self.case)
            snapshot=assemble_snapshot(self.case,self.store)
            self.assertEqual(resolution['outcome'],'Proceed to assessment')
            self.assertEqual(snapshot['submission']['screening_resolution'],resolution)
            self.assertEqual(snapshot['submission']['outcome'],'Proceed to assessment')
            self.assertEqual(resolution['validation_concerns'],intake['validation_issues'])
            self.assertEqual(intake,original)
            script='''import streamlit as st
from underwriting.ui.submission import render_screening
from underwriting.configuration.settings import get_config
render_screening(st.session_state['case'],'test',get_config(),st.session_state['view'])
'''
            app=AppTest.from_string(script)
            app.session_state['case']=self.case
            app.session_state['view']={'saved':intake,'settings':self.fixture.settings}
            app.run()
            self.assertFalse(app.exception)
            self.assertIn('**Outcome:** Proceed to assessment',[item.value for item in app.markdown])
            self.assertFalse(app.warning)

    def test_unknown_scope_failure_and_explicit_override_have_reasons(self):
        intake=copy.deepcopy(self.fixture.intake)
        intake['result']['screening_checks'][-1].update(result='unknown',reason='Essential site scope needs clarification',information_required='Confirm occupied site details')
        resolution=resolve_screening(intake,self.case)
        self.assertEqual(resolution['outcome'],'Pause for clarification')
        self.assertEqual(resolution['reasons'][0]['check_id'],'SC06')
        self.assertEqual(resolution['reasons'][0]['clarification_needed'],'Confirm occupied site details')
        intake=copy.deepcopy(self.fixture.intake)
        intake['result']['screening_checks'][1]['result']='fail'
        self.assertEqual(resolve_screening(intake,self.case)['outcome'],'Outside demo scope')
        intake=copy.deepcopy(self.fixture.intake)
        intake['screening_override']={'event_id':'explicit-human-override','case_id':self.case['case_id'],'source_version':'1','source_hash':'mock-source',
                                      'outcome':'Pause for clarification','reason':'Human reviewer explicitly requests essential identification clarification'}
        resolution=resolve_screening(intake,self.case)
        self.assertEqual(resolution['derived_outcome'],'Proceed to assessment')
        self.assertEqual(resolution['outcome'],'Pause for clarification')
        self.assertEqual(resolution['reasons'][0]['check_id'],'Human override')
        self.assertTrue(resolution['override']['reason'])
        with self.assertRaisesRegex(ValueError,'different company'):
            resolve_screening(dict(intake,screening_override=dict(intake['screening_override'],case_id='OTHER')),self.case)
        with self.assertRaisesRegex(ValueError,'another company/submission'):
            resolve_screening(intake,dict(self.case,submission_version='2'))

    def test_changed_snapshot_generates_once_instead_of_loading_old_paused_review(self):
        paused=copy.deepcopy(self.fixture.intake)
        paused.update(run_id='paused-intake',finished_at='2099-01-01')
        paused['result']['screening_checks'][-1].update(result='unknown',reason='Clarification required')
        paused['outcome']='Pause for clarification'
        self.store.save(paused,None)
        data=fixtures.review_data();data['screening_outcome']='Pause for clarification';data['assessment_interpretation']='Screening remains paused.'
        self.fixture.request.side_effect=lambda *_: ({'status':'completed'},json.dumps(data))
        old,_=self.fixture.generate()
        self.assertEqual(old['status'],'succeeded')
        current=copy.deepcopy(self.fixture.intake)
        current.update(run_id='passing-intake',finished_at='2100-01-01')
        self.store.save(current,None)
        new_snapshot=assemble_snapshot(self.case,self.store)
        self.assertNotEqual(old['input_snapshot_hash'],snapshot_hash(new_snapshot))
        self.fixture.request.side_effect=lambda *_: ({'status':'completed'},json.dumps(fixtures.review_data()))
        loaded,origin=self.fixture.generate()
        self.assertNotEqual(loaded['review_run_id'],old['review_run_id'])
        self.assertEqual(loaded['status'],'succeeded')
        self.assertTrue(origin.startswith('GPT'))
        self.assertEqual(self.fixture.request.call_count,2)
        with self.assertRaisesRegex(ValueError,'screening status|screening is paused'):
            validate_review(data,new_snapshot)
        script='''import streamlit as st
from underwriting.ui.decision import render_decision
render_decision({'case_id':'TEST-BELIEF','submission_version':'1','document_path':'unused.pdf'},'case:TEST-BELIEF')
'''
        with patch('underwriting.ui.decision.settings_for_ui',return_value=self.fixture.settings), patch('underwriting.ui.decision.DecisionStore',return_value=self.store), patch('underwriting.ui.decision.document_hash',return_value='mock-source'), patch('underwriting.decision.service.structured_request',side_effect=AssertionError('No paid calls on historical load')):
            app=AppTest.from_string(script).run()
            app.button(key='case:TEST-BELIEF:1:decision:load').click().run()
            self.assertFalse(app.exception)
            state=app.session_state['case:TEST-BELIEF:1:decision']
            self.assertEqual(len(app.metric),3)
            self.assertTrue(any('Key concerns' in item.value for item in app.subheader))
            self.assertTrue(any('AI-suggested next action' in item.value for item in app.subheader))
            self.assertEqual(len(app.get('download_button')),1)
            self.assertFalse(state['stale'])
            self.assertEqual(state['current_screening']['outcome'],'Proceed to assessment')
            self.assertFalse(any('screening remains paused' in item.value for item in app.warning))
            app.session_state['case:TEST-BELIEF:1:decision:sections']='Human decision'
            app.run()
            self.assertFalse(next(button for button in app.button if button.label=='Save decision').disabled)

    def test_concise_pdf_one_page_typical_two_max_legacy_and_no_appendix(self):
        run,_=self.fixture.generate()
        original=copy.deepcopy(run)
        pdf=render_report(run)
        with pymupdf.open(stream=pdf,filetype='pdf') as document:
            self.assertEqual(len(document),1)
            text='\n'.join(page.get_text() for page in document).replace('\xa0',' ')
            self.assertIn('Proceed to assessment',text)
            for unwanted in ('Source and provenance appendix','Activated rules','Human-confirmed attribute distributions','Review flags','mock-intake'):
                self.assertNotIn(unwanted,text)
        legacy=copy.deepcopy(run)
        legacy['review'].pop('screening_outcome')
        legacy['review']['key_concerns']=[dict(legacy['review']['key_concerns'][0],concern_id=f'C{i}',finding=f'Mock concern {i}: '+('Supported long finding. '*30),significance='Evidence-supported importance. '*30,suggested_follow_up='Ask for supported clarification. '*30) for i in range(9)]
        legacy['review']['missing_information']=[dict(legacy['review']['missing_information'][0],item_id=f'M{i}',information_or_document=f'Mock essential request {i}: '+('Detailed clarification. '*30)) for i in range(9)]
        pdf=render_report(legacy)
        (ROOT/'.runtime/concise-decision-review.pdf').write_bytes(pdf)
        with pymupdf.open(stream=pdf,filetype='pdf') as document:
            self.assertLessEqual(len(document),2)
            text='\n'.join(page.get_text() for page in document).replace('\xa0',' ')
            self.assertIn('additional concern items remain',text)
            self.assertIn('additional information requests remain',text)
            for page in document:
                for block in page.get_text('dict')['blocks']:
                    for line in block.get('lines',[]):
                        for span in line['spans']:
                            self.assertGreaterEqual(span['bbox'][0],30)
                            self.assertLess(span['bbox'][2],565)
                            self.assertLess(span['bbox'][3],835)
        self.assertEqual(run,original)


    def test_historical_pdf_is_explicit_and_requires_no_regeneration(self):
        from underwriting.decision.service import export_historical_report
        paused=copy.deepcopy(self.fixture.intake)
        paused.update(run_id='historic-intake',finished_at='2099-01-01',outcome='Pause for clarification')
        paused['result']['screening_checks'][-1].update(result='unknown',reason='Clarification required')
        self.store.save(paused,None)
        data=fixtures.review_data();data['screening_outcome']='Pause for clarification'
        self.fixture.request.side_effect=lambda *_: ({'status':'completed'},json.dumps(data))
        run,_=self.fixture.generate()
        current=copy.deepcopy(self.fixture.intake)
        current.update(run_id='current-pass-intake',finished_at='2100-01-01')
        self.store.save(current,None)
        with patch('underwriting.decision.service.structured_request',side_effect=AssertionError('No GPT on export')):
            pdf=export_historical_report(self.fixture.settings,self.case,self.store,run['review_run_id'])
        with pymupdf.open(stream=pdf,filetype='pdf') as document:
            text='\n'.join(page.get_text() for page in document).replace('\xa0',' ')
            self.assertIn('HISTORICAL REVIEW - NOT CURRENT',text.replace('\xad','-'))
            self.assertNotIn('Current screening:',text)
            self.assertIn('Screening recorded in historical snapshot: Pause for clarification',text)
            self.assertLessEqual(len(document),2)
        (ROOT/'.runtime/historical-decision-review.pdf').write_bytes(pdf)
        self.fixture.request.assert_called_once()
