"""Decision workflow checks use mocked GPT and in-memory SQLite only."""
import copy
import json
import sqlite3
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch
import pymupdf
from streamlit.testing.v1 import AppTest
from underwriting.configuration.settings import get_config, validate, ConfigError, ROOT as PROJECT_ROOT
from underwriting.configuration.access import require_owner
from underwriting.persistence.decision import DecisionStore
from underwriting.persistence.intake import StoreError
from underwriting.decision.snapshot import assemble_snapshot, snapshot_hash
from underwriting.decision.schema import validate_review
from underwriting.decision.service import load_generate, save_decision, retry_save, pending, export_report, prompt_for
import test_brb as brb_tests
import test_intake as intake_tests


class LocalDecisionStore(DecisionStore):
    def __init__(self, upstream):
        self.settings, self.db = upstream.settings, upstream.db
        self.fail_final_save = False
    execute = intake_tests.SQLiteStore.execute
    fail_save = False
    def transaction(self, statements):
        require_owner(self.settings)
        self.db.execute('PRAGMA foreign_keys=ON')
        results = []
        self.db.execute('BEGIN IMMEDIATE')
        try:
            for sql, args in statements:
                if self.fail_final_save and sql.startswith('INSERT OR IGNORE INTO decision_review_runs') and args[3] == 'succeeded':
                    raise StoreError('Mock final save failure')
                cursor = self.db.execute(sql, args)
                results.append({'affected_row_count': cursor.rowcount})
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK')
            raise
        return results


def review_data():
    return {'report_conclusion':'Mock conclusion: clarify the supported scope gap.','screening_outcome':'Proceed to assessment','review_summary': 'Mock evidence-grounded review for a human underwriter.',
            'assessment_interpretation': 'Concern beliefs are not event probabilities. Unknown remains separate from residual unassigned output.',
            'key_concerns': [{'concern_id':'C1','topic':'Recorded scope gap','finding':'Mock evidence identifies an unresolved detail.',
                'significance':'Clarification is needed before interpreting the protection scope.', 'supporting_references':['F1'],
                'suggested_follow_up':'Clarify the recorded scope with the broker.'}],
            'missing_information':[{'item_id':'M1','information_or_document':'Clarify the recorded protection scope', 'request_type':'clarification_needed',
                'reason':'A missing detail is not proof of absent protection.', 'supporting_references':['F1']}],
            'suggested_action': {'action':'request_further_information','rationale':'Resolve the supported scope gap.','supporting_references':['F1','SC01']},
            'secondary_actions':[{'action':'refer_to_senior_underwriter','rationale':'Consider senior review of the recorded concern.'}],
            'limitations':['Synthetic research model; no insurance acceptance, quote or policy is provided.']}


class DecisionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = brb_tests.BRBIntegrationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.upstream, self.er = self.fixture.prepare_er()
        self.settings, self.case = self.fixture.settings, self.fixture.case
        self.store = LocalDecisionStore(self.upstream)
        self.brb, _ = brb_tests.calculate_brb(self.settings,self.case,self.er,self.upstream)
        self.store.initialise(None)
        self.intake = {'run_id':'mock-intake','case_id':self.case['case_id'],'source_version':'1','source_hash':'mock-source',
                       'status':'completed','finished_at':'2026-01-01','result':intake_tests.response_data(),'outcome':'Proceed to assessment',
                       'validation_issues':[], 'policy_version':get_config()['screening']['policy_version']}
        self.store.save(self.intake,None)
        self.patch_root = patch('underwriting.decision.service.ROOT',Path(self.fixture.temp.name))
        self.patch_root.start()
        self.addCleanup(self.patch_root.stop)
        self.request = Mock(side_effect=lambda *_: ({'status':'completed','model':'mock-model','usage':{'input_tokens':10}},json.dumps(review_data())))
    def generate(self, **kwargs):
        return load_generate(self.settings,self.case,self.store,request=self.request,**kwargs)

    def test_snapshot_current_records_versions_reference_validation_and_prompt(self):
        snapshot = assemble_snapshot(self.case,self.store)
        self.assertEqual(snapshot['beliefs']['human_confirmed'],self.er['confirmed_beliefs'])
        self.assertEqual(snapshot['brb']['result'],self.brb['result'])
        self.assertIn('F1',snapshot['reference_catalogue'])
        self.assertIn('mock-intake',snapshot['reference_catalogue'])
        validate_review(review_data(),snapshot)
        invalid=review_data();invalid['key_concerns'][0]['supporting_references']=['invented-reference']
        with self.assertRaisesRegex(ValueError,'reference'):
            validate_review(invalid,snapshot)
        before=copy.deepcopy(snapshot)
        prompt,metadata=prompt_for(snapshot)
        self.assertIn('Low and High are concern assessments',prompt)
        self.assertEqual(metadata['version'],'1.3')
        self.assertNotIn('test-key',prompt)
        self.assertNotIn('test-token',prompt)
        self.assertEqual(before,snapshot)
        config=copy.deepcopy(get_config());config['prompts']['decision_review']['template']='missing placeholders'
        with self.assertRaisesRegex(ConfigError,'prompts.decision_review'):
            validate(config)

    def test_saved_first_reuse_and_configuration_changes_do_not_call_gpt(self):
        run,origin=self.generate()
        self.assertEqual(run['status'],'succeeded')
        self.assertIn('GPT',origin)
        again,origin=self.generate()
        self.assertEqual(run,again)
        self.assertIn('no GPT call',origin)
        changed=copy.deepcopy(get_config());changed['prompts']['decision_review']['version']='future-version'
        with patch('underwriting.decision.service.get_config',return_value=changed):
            self.assertEqual(self.generate()[0]['review_run_id'],run['review_run_id'])
        self.request.assert_called_once()
        self.assertEqual(self.store.review_by_id(run['review_run_id']),run)

    def test_human_saves_are_immutable_and_do_not_regenerate(self):
        run,_=self.generate()
        kwargs=dict(action='continue_underwriting_review',notes='',reviewer_label='Reviewer A',secondary_actions=[])
        with self.assertRaisesRegex(ValueError,'reason'):
            save_decision(self.settings,self.case,self.store,run['review_run_id'],**kwargs)
        kwargs['notes']='Human explanation for a different workflow action.'
        first=save_decision(self.settings,self.case,self.store,run['review_run_id'],**kwargs)
        kwargs['notes']='Updated human notes.'
        second=save_decision(self.settings,self.case,self.store,run['review_run_id'],expected_decision=first['decision_id'],**kwargs)
        self.assertEqual(second['supersedes_decision_id'],first['decision_id'])
        self.assertEqual(len(self.store.decision_history(run['review_run_id'])),2)
        self.assertEqual(self.store.review_by_id(run['review_run_id']),run)
        self.request.assert_called_once()
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.save_human_decision(dict(second,decision_id='orphan',review_run_id='not-a-review',supersedes_decision_id=None),None)

    def test_database_outage_no_gpt_and_save_retry_no_repeat(self):
        with patch.object(self.store,'execute',side_effect=StoreError('Mock outage')):
            with self.assertRaises(StoreError):
                self.generate()
        self.request.assert_not_called()
        self.store.fail_final_save=True
        with self.assertRaises(StoreError):
            self.generate()
        self.request.assert_called_once()
        retained=pending(self.case)
        self.assertEqual(len(retained),1)
        self.assertEqual(retained[0]['status'],'succeeded')
        self.store.fail_final_save=False
        saved=retry_save(self.settings,self.store,retained[0])
        self.assertEqual(self.generate()[0]['review_run_id'],saved['review_run_id'])
        self.assertEqual(pending(self.case),[])
        self.request.assert_called_once()

    def test_invalid_response_and_explicit_regeneration_preserve_history(self):
        first,_=self.generate()
        invalid=review_data();invalid['suggested_action']['supporting_references']=['not-real']
        self.request.side_effect=lambda *_: ({'status':'completed'},json.dumps(invalid))
        failed,_=self.generate(regenerate=True,confirmed=True,expected_run=first['review_run_id'])
        self.assertEqual(failed['status'],'failed')
        self.assertIsNotNone(failed['raw_response'])
        self.assertEqual(self.generate()[0]['review_run_id'],first['review_run_id'])
        self.assertEqual(self.store.review_by_id(first['review_run_id']),first)
        self.assertEqual(self.request.call_count,2)

    def test_upstream_changes_and_company_isolation_fail_closed(self):
        run,_=self.generate()
        changed=copy.deepcopy(self.intake)
        changed.update(run_id='new-intake',finished_at='2099-01-01')
        next(field for field in changed['result']['basic_information'] if field['field_id']=='site_address')['value']='Updated broker site address'
        self.store.save(changed,None)
        with self.assertRaisesRegex(ValueError,'stale'):
            save_decision(self.settings,self.case,self.store,run['review_run_id'],action='request_further_information',notes='Note',reviewer_label='A',secondary_actions=[])
        self.assertNotEqual(snapshot_hash(assemble_snapshot(self.case,self.store)),run['input_snapshot_hash'])
        with self.assertRaisesRegex(ValueError,'company'):
            export_report(self.settings,dict(self.case,case_id='OTHER'),self.store,run['review_run_id'])
        self.request.assert_called_once()

    def test_readonly_can_load_export_but_not_generate_or_save(self):
        readonly=replace(self.settings,access_mode='public_readonly')
        self.store.settings=readonly
        with self.assertRaises(PermissionError):
            load_generate(readonly,self.case,self.store,request=self.request)
        self.request.assert_not_called()
        self.store.settings=self.settings
        run,_=self.generate()
        self.store.settings=readonly
        self.assertEqual(load_generate(readonly,self.case,self.store,request=self.request)[0],run)
        with self.assertRaises(PermissionError):
            save_decision(readonly,self.case,self.store,run['review_run_id'],action='request_further_information',notes='',reviewer_label='A',secondary_actions=[])
        pdf=export_report(readonly,self.case,self.store,run['review_run_id'])
        self.assertTrue(pdf.startswith(b'%PDF'))
        self.request.assert_called_once()

    def test_duplicate_concurrent_request_and_double_click(self):
        def request(*_):
            with self.assertRaisesRegex(ValueError,'already running'):
                self.generate()
            return {'status':'completed'},json.dumps(review_data())
        self.request.side_effect=request
        run,_=self.generate()
        self.assertEqual(self.generate()[0],run)
        self.request.assert_called_once()

    def test_draft_and_saved_pdf_reports_exclude_unsaved_notes_and_wrap(self):
        run,_=self.generate()
        pdf=export_report(self.settings,self.case,self.store,run['review_run_id'])
        with pymupdf.open(stream=pdf,filetype='pdf') as document:
            text='\n'.join(page.get_text() for page in document).replace('\xa0',' ').replace('\xad','-')
            self.assertIn('Draft review',text)
            self.assertIn('Human decision pending',text)
            self.assertIn('Overall',text)
            self.assertNotIn('Source and provenance appendix',text)
            self.assertNotIn('Activated rules',text)
            self.assertLessEqual(len(document),2)
        record=save_decision(self.settings,self.case,self.store,run['review_run_id'],action='request_further_information',notes='Saved human notes. '+('Long wrapped review note. '*100),reviewer_label='Representative reviewer',secondary_actions=['refer_to_senior_underwriter'])
        pdf=export_report(self.settings,self.case,self.store,run['review_run_id'])
        output=PROJECT_ROOT/'.runtime/decision-review-sample.pdf'
        output.write_bytes(pdf)
        with pymupdf.open(stream=pdf,filetype='pdf') as document:
            text='\n'.join(page.get_text() for page in document).replace('\xa0',' ').replace('\xad','-')
            self.assertIn('Saved human review',text)
            self.assertNotIn(record['decision_id'],text)
            self.assertNotIn('Long wrapped review note',text)
            self.assertNotIn('Draft review',text)
            self.assertNotIn('UNSAVED NOTE',text)
            for page in document:
                self.assertIn('Page ',page.get_text().replace('\xa0',' '))
                for block in page.get_text('dict')['blocks']:
                    for line in block.get('lines',[]):
                        for span in line['spans']:
                            self.assertGreaterEqual(span['bbox'][0],30)
                            self.assertLess(span['bbox'][2],565)
                            self.assertLess(span['bbox'][3],835)

    def test_ui_no_auto_queries_load_rerun_edits_and_decision_save(self):
        script='''import streamlit as st
from underwriting.ui.decision import render_decision
render_decision({'case_id':'TEST-BELIEF','submission_version':'1','document_path':'unused.pdf'},'case:TEST-BELIEF')
'''
        with patch('underwriting.ui.decision.settings_for_ui',return_value=self.settings), \
             patch('underwriting.ui.decision.DecisionStore',return_value=self.store), \
             patch('underwriting.ui.decision.document_hash',return_value='mock-source'), \
             patch('underwriting.decision.service.structured_request',self.request):
            with patch.object(self.store,'execute',side_effect=AssertionError('No startup query')):
                app=AppTest.from_string(script).run()
                self.assertFalse(app.exception)
                app.run()
                self.assertFalse(app.exception)
            self.request.assert_not_called()
            app.button(key='case:TEST-BELIEF:1:decision:load').click().run()
            self.assertFalse(app.exception)
            self.request.assert_called_once()
            app.session_state['case:TEST-BELIEF:1:decision:sections']='Human decision'
            app.run()
            self.assertIsNone(app.selectbox[0].value)
            with patch.object(self.store,'execute',side_effect=AssertionError('No rerun query')):
                app.run()
                self.assertFalse(app.exception)
                app.text_area[0].input('UNSAVED NOTE').run()
                self.assertFalse(app.exception)
            app.selectbox[0].set_value('request_further_information')
            app.text_input[0].input('UI reviewer')
            next(button for button in app.button if button.label=='Save decision').click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any('Human decision saved' in item.value for item in app.success))
            self.request.assert_called_once()

    def test_changed_confirmed_beliefs_and_source_require_upstream_completion(self):
        run,_=self.generate()
        from underwriting.assessment.belief_service import confirm_belief
        proposal=self.store.latest_beliefs(self.case,'mock-source',self.er['reviewed_hash'])
        confirm_belief(self.settings,self.case,self.store,proposal,'F1','edit',.5,.3,'Mock changed confirmation')
        with self.assertRaisesRegex(ValueError,'ER'):
            self.generate()
        with patch('underwriting.assessment.belief_service.document_hash',return_value='new-source'):
            with self.assertRaisesRegex(ValueError,'Submission extraction'):
                self.generate()
        self.request.assert_called_once()

    def test_failed_first_attempt_can_be_regenerated_only_explicitly(self):
        self.request.side_effect=lambda *_: ({'status':'completed'},json.dumps({'not':'the schema'}))
        failed,_=self.generate()
        self.assertEqual(failed['status'],'failed')
        self.assertEqual(self.generate()[0]['review_run_id'],failed['review_run_id'])
        self.request.assert_called_once()
        with self.assertRaises(PermissionError):
            self.generate(regenerate=True,expected_run=failed['review_run_id'])
        self.request.side_effect=lambda *_: ({'status':'completed'},json.dumps(review_data()))
        success,_=self.generate(regenerate=True,confirmed=True,expected_run=failed['review_run_id'])
        self.assertEqual(success['status'],'succeeded')
        self.assertEqual(self.store.review_by_id(failed['review_run_id'])['status'],'failed')
        self.assertEqual(self.request.call_count,2)

    def test_session_upstream_edits_mark_loaded_review_stale_without_query(self):
        run,_=self.generate()
        script='''import streamlit as st
from underwriting.ui.decision import render_decision
render_decision({'case_id':'TEST-BELIEF','submission_version':'1','document_path':'unused.pdf'},'case:TEST-BELIEF')
'''
        with patch('underwriting.ui.decision.settings_for_ui',return_value=self.settings), \
             patch('underwriting.ui.decision.DecisionStore',return_value=self.store), \
             patch('underwriting.ui.decision.document_hash',return_value='mock-source'):
            app=AppTest.from_string(script).run()
            app.button(key='case:TEST-BELIEF:1:decision:load').click().run()
            self.assertFalse(app.exception)
            app.session_state['case:TEST-BELIEF:upstream:evidence']={'run':{'run_id':'changed','finished_at':'2099-01-01'},'reviews':[]}
            with patch.object(self.store,'execute',side_effect=AssertionError('No automatic query')):
                app.run()
            self.assertFalse(app.exception)
            self.assertIn('Historical review — not current',[item.value for item in app.caption])
            app.session_state['case:TEST-BELIEF:1:decision:sections']='Human decision'
            app.run()
            self.assertTrue(next(button for button in app.button if button.label=='Save decision').disabled)

    def test_production_transaction_batch_conditions_and_sanitized_errors(self):
        store=DecisionStore(self.settings)
        response=Mock()
        response.raise_for_status.return_value=None
        response.json.return_value={'results':[{'type':'ok','response':{'result':{
            'step_results':[{}, {}, {'affected_row_count':1}, {}, None],
            'step_errors':[None]*5}}}]}
        with patch('underwriting.persistence.decision.requests.post',return_value=response) as post:
            result=store.transaction([('INSERT INTO demo VALUES (?,?)',["untrusted ' text",None])])
            self.assertEqual(result[0]['affected_row_count'],1)
            steps=post.call_args.kwargs['json']['requests'][0]['batch']['steps']
            self.assertEqual(steps[0]['stmt']['sql'],'PRAGMA foreign_keys=ON')
            self.assertEqual(steps[1]['stmt']['sql'],'BEGIN IMMEDIATE')
            self.assertEqual(steps[2]['stmt']['args'][1],{'type':'null'})
            self.assertEqual(steps[-1]['stmt']['sql'],'ROLLBACK')
            self.assertNotIn("untrusted",steps[2]['stmt']['sql'])
            response.json.return_value['results'][0]['response']['result']['step_errors'][2]={'message':'SECRET-LIKE-ERROR'}
            with self.assertRaises(StoreError) as error:
                store.transaction([('INSERT INTO demo VALUES (?)',['safe'])])
            self.assertNotIn('SECRET-LIKE-ERROR',str(error.exception))

    def test_model_schema_enforces_bare_catalogue_ids(self):
        from jsonschema import Draft202012Validator
        from underwriting.decision.schema import review_output_schema
        snapshot=assemble_snapshot(self.case,self.store)
        schema=review_output_schema(snapshot)
        validator=Draft202012Validator(schema)
        self.assertEqual(list(validator.iter_errors(review_data())),[])
        for reference in ('F1 (broker visit)', 'F1, SC01', 'snapshot.submission.extraction_and_screening.basic_information[0]', 'invented'):
            data=review_data()
            data['suggested_action']['supporting_references']=[reference]
            self.assertTrue(list(validator.iter_errors(data)))
        run,_=self.generate()
        sent_schema=self.request.call_args.args[3]
        for name in ('Concern','MissingInformation','SuggestedAction'):
            self.assertEqual(sent_schema['$defs'][name]['properties']['supporting_references']['items']['enum'],sorted(snapshot['reference_catalogue']))
        self.assertEqual(run['schema_version'],'decision-review-v1.4')

    def test_failed_reference_details_remain_visible_without_paid_retry(self):
        data=review_data();data['suggested_action']['supporting_references']=['F1 (broker visit)']
        self.request.side_effect=lambda *_: ({'status':'completed'},json.dumps(data))
        self.generate()
        self.request.assert_called_once()
        script='''import streamlit as st
from underwriting.ui.decision import render_decision
render_decision({'case_id':'TEST-BELIEF','submission_version':'1','document_path':'unused.pdf'},'case:TEST-BELIEF')
'''
        with patch('underwriting.ui.decision.settings_for_ui',return_value=self.settings), \
             patch('underwriting.ui.decision.DecisionStore',return_value=self.store), \
             patch('underwriting.ui.decision.document_hash',return_value='mock-source'), \
             patch('underwriting.decision.service.structured_request',self.request):
            app=AppTest.from_string(script).run()
            app.button(key='case:TEST-BELIEF:1:decision:load').click().run()
            self.assertFalse(app.exception)
            self.assertTrue(app.error)
            self.assertTrue(any('F1 (broker visit)' in item.value for item in app.text))
            self.assertTrue(any(item.label=='Decision review error details' and item.proto.expanded for item in app.expander))
            app.run()
            self.assertFalse(app.exception)
            self.assertTrue(app.error)
            self.request.assert_called_once()

    def test_paused_saved_screening_is_reviewable_and_constrains_actions_before_gpt(self):
        from jsonschema import Draft202012Validator
        paused=copy.deepcopy(self.intake)
        paused.update(status='needs_review',outcome='Pause for clarification',validation_issues=['Unsupported source quotation requires review'])
        paused['result']['screening_checks'][-1]['result']='unknown'
        response=review_data();response['screening_outcome']='Pause for clarification'
        self.request.side_effect=lambda *_: ({'status':'completed'},json.dumps(response))
        with patch.object(self.store,'latest',return_value=paused):
            run,_=self.generate()
            self.assertEqual(run['status'],'succeeded')
            self.assertEqual(run['input_snapshot']['submission']['outcome'],'Pause for clarification')
            self.assertEqual(self.request.call_count,1)
            schema=self.request.call_args.args[3]
            for name in ('SuggestedAction','SecondaryAction'):
                self.assertEqual(schema['$defs'][name]['properties']['action']['enum'],['request_further_information','refer_to_senior_underwriter'])
            invalid=review_data();invalid['suggested_action']['action']='continue_underwriting_review'
            self.assertTrue(list(Draft202012Validator(schema).iter_errors(invalid)))
            invalid=review_data();invalid['secondary_actions']=[{'action':'continue_underwriting_review','rationale':'Wrong route'}]
            self.assertTrue(list(Draft202012Validator(schema).iter_errors(invalid)))
            self.assertEqual(self.generate()[0]['review_run_id'],run['review_run_id'])
            self.request.assert_called_once()

    def test_missing_assessment_stages_block_before_paid_calls(self):
        for method in ('latest_evidence','latest_er_compatible','latest_brb'):
            with patch.object(self.store,method,return_value=None), patch.object(self.store,'initialise_decision') as initialise:
                with self.assertRaisesRegex(ValueError,'No GPT request was made'):
                    self.generate()
                initialise.assert_not_called()
        self.request.assert_not_called()

    def test_ui_missing_submission_warns_before_gpt_and_blocks_regenerate(self):
        script='''import streamlit as st
from underwriting.ui.decision import render_decision
render_decision({'case_id':'TEST-BELIEF','submission_version':'1','document_path':'unused.pdf'},'case:TEST-BELIEF')
'''
        with patch('underwriting.ui.decision.settings_for_ui',return_value=self.settings), \
             patch('underwriting.ui.decision.DecisionStore',return_value=self.store), \
             patch('underwriting.ui.decision.document_hash',return_value='mock-source'), \
             patch.object(self.store,'latest',return_value=None), \
             patch('underwriting.decision.service.structured_request',self.request):
            app=AppTest.from_string(script).run()
            app.button(key='case:TEST-BELIEF:1:decision:load').click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any('Submission extraction and screening first' in item.value for item in app.warning))
            app.checkbox(key='case:TEST-BELIEF:1:decision:regenerate_confirm').check().run()
            self.assertTrue(app.button(key='case:TEST-BELIEF:1:decision:regenerate').disabled)
            self.request.assert_not_called()

    def test_blank_session_loads_paused_review_from_database_without_gpt(self):
        paused=copy.deepcopy(self.intake)
        paused.update(status='needs_review',outcome='Pause for clarification',validation_issues=['Source quotation needs review'])
        script='''import streamlit as st
from underwriting.ui.decision import render_decision
render_decision({'case_id':'TEST-BELIEF','submission_version':'1','document_path':'unused.pdf'},'case:TEST-BELIEF')
'''
        paused['result']['screening_checks'][-1]['result']='unknown'
        response=review_data();response['screening_outcome']='Pause for clarification'
        self.request.side_effect=lambda *_: ({'status':'completed'},json.dumps(response))
        with patch.object(self.store,'latest',return_value=paused):
            saved,_=self.generate()
            self.assertEqual(saved['status'],'succeeded')
            with patch('underwriting.ui.decision.settings_for_ui',return_value=self.settings), \
                 patch('underwriting.ui.decision.DecisionStore',return_value=self.store), \
                 patch('underwriting.ui.decision.document_hash',return_value='mock-source'), \
                 patch('underwriting.decision.service.structured_request',side_effect=AssertionError('Saved review must not call GPT')):
                app=AppTest.from_string(script).run()
                self.assertFalse(app.exception)
                # No Submission or Assessment widgets or loaded browser records exist.
                app.button(key='case:TEST-BELIEF:1:decision:load').click().run()
                self.assertFalse(app.exception)
                self.assertFalse(app.error)
                self.assertTrue(any('AI review saved' in row.value for row in app.caption))
                self.assertTrue(any('SC06' in row.value for row in app.markdown))
                self.assertEqual(app.session_state['case:TEST-BELIEF:1:decision']['run']['review_run_id'],saved['review_run_id'])
                self.assertFalse(app.session_state['case:TEST-BELIEF:1:decision'].get('blocked'))
        self.request.assert_called_once()
