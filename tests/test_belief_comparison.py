"""Temporary two-method experiment: mocked GPT and temporary SQLite only."""
import copy
import json
import math
import os
import unittest
from decimal import Decimal
from dataclasses import replace
from unittest.mock import Mock,patch
from pathlib import Path
from underwriting.assessment.score_schema import score_to_beliefs,validate_scores,response_only
from underwriting.assessment.score_service import compare_beliefs,propose_scores,pending_scores,retry_score_save,score_identity
from underwriting.assessment.belief_service import current_input,belief_prompt,content_hash,confirm_belief
from underwriting.configuration.settings import get_config
from underwriting.configuration.access import Settings,local_comparison_settings,require_owner,require_test_services
from underwriting.persistence.beliefs import BeliefStore
from underwriting.persistence.intake import StoreError
from underwriting.intake.service import structured_request
import test_beliefs as fixtures


def score_response(reviewed,value=2.6):
    return {'assessments':[{'attribute_id':a['attribute_id'],'assessable':True,'concern_score':value,
      'explanation':'Mock score based on reviewed evidence.','supporting_evidence_ids':[a['evidence_items'][0]['evidence_id']],
      'missing_information':['Mock missing detail'],'contradictions':[]} for a in reviewed['attributes']]}

class MappingTests(unittest.TestCase):
    def test_endpoints_fractional_examples_and_unassessable(self):
        for score,low,high in [(1,1,0),(5,0,1),(2,.75,.25),(2.6,.6,.4),(3,.5,.5)]:
            self.assertEqual(score_to_beliefs(True,score),{'low':low,'high':high,'unknown':0})
        self.assertEqual(score_to_beliefs(False,None),{'low':0,'high':0,'unknown':1})

    def test_invalid_scores_and_inconsistent_assessability_are_not_clamped(self):
        for score in [None,True,'2.6',0,5.1,float('nan'),float('inf'),-float('inf'),10**400,Decimal('NaN')]:
            with self.subTest(score=str(score)),self.assertRaises(ValueError):score_to_beliefs(True,score)
        for flag,score in [(False,3),(False,0),('false',None),(1,2)]:
            with self.assertRaises(ValueError):score_to_beliefs(flag,score)

class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.BeliefTests();self.f.setUp();self.addCleanup(self.f.tearDown)
        self.store,self.case,self.settings=self.f.store,self.f.case,self.f.settings
        self.data=score_response(self.f.reviewed)
        self.b=Mock(side_effect=lambda *_:({'status':'completed','model':'test-model'},json.dumps(self.data)))
        self.context=current_input(self.case,self.store)

    def run_pair(self):
        return compare_beliefs(self.settings,self.case,self.store,request_a=self.f.request,request_b=self.b)

    def test_separate_yaml_prompt_routing_and_identical_frozen_snapshot(self):
        result=self.run_pair();a,b=result['direct'],result['score']
        self.assertEqual(self.f.request.call_args.args[-1],'attribute_beliefs')
        self.assertEqual(self.b.call_args.args[-1],'attribute_concern_scores')
        self.assertNotEqual(self.f.request.call_args.args[2],self.b.call_args.args[2])
        self.assertNotIn('concern_score',self.f.request.call_args.args[2])
        self.assertIn('Do not calculate belief degrees',self.b.call_args.args[2])
        self.assertEqual(a['reviewed_evidence'],b['reviewed_evidence'])
        self.assertEqual(a['reviewed_snapshot_hash'],b['reviewed_snapshot_hash'])
        self.assertEqual(a['evidence_review_versions'],b['evidence_review_versions'])
        self.assertEqual(a['method_id'],'direct_ai_beliefs_v1');self.assertEqual(b['method_id'],'concern_score_v1')
        self.assertEqual(b['result']['assessments'][0]['beliefs'],{'low':.6,'high':.4,'unknown':0})
        self.assertEqual(a['prompt']['sha256'],belief_prompt(self.case,self.f.reviewed)[1]['sha256'])

    def test_saved_results_reused_with_zero_additional_calls(self):
        first=self.run_pair();second=self.run_pair()
        self.assertEqual(first['direct']['run_id'],second['direct']['run_id'])
        self.assertEqual(first['score']['run_id'],second['score']['run_id'])
        self.f.request.assert_called_once();self.b.assert_called_once()
        self.assertEqual(self.store.execute('SELECT COUNT(*) FROM attribute_belief_runs_v1')['rows'][0][0]['value'],'1')
        self.assertEqual(self.store.execute('SELECT COUNT(*) FROM attribute_concern_score_runs_v2')['rows'][0][0]['value'],'1')

    def test_existing_direct_only_calls_missing_score(self):
        a=self.f.run_action();self.f.request.reset_mock()
        result=self.run_pair();self.assertEqual(result['direct']['run_id'],a['run_id'])
        self.f.request.assert_not_called();self.b.assert_called_once()

    def test_existing_score_only_calls_missing_direct(self):
        score=propose_scores(self.settings,self.case,self.store,self.context,request=self.b)
        self.b.reset_mock();result=self.run_pair()
        self.assertEqual(result['score']['run_id'],score['run_id']);self.b.assert_not_called();self.f.request.assert_called_once()

    def test_database_failure_prevents_both_provider_calls(self):
        with patch.object(self.store,'latest_scores',side_effect=StoreError('Mock outage')):
            with self.assertRaises(StoreError):self.run_pair()
        self.f.request.assert_not_called();self.b.assert_not_called()

    def test_legacy_direct_result_is_preserved(self):
        first=self.f.run_action();self.store.execute('DELETE FROM attribute_belief_runs_v1')
        legacy=copy.deepcopy(first);legacy.update(run_id='legacy-direct',reviewed_hash=content_hash(self.f.reviewed))
        legacy.pop('method_id');legacy.pop('reviewed_snapshot_hash');self.store.save_beliefs(legacy)
        self.f.request.reset_mock();result=self.run_pair()
        self.assertEqual(result['direct']['run_id'],'legacy-direct');self.assertNotIn('method_id',result['direct'])
        self.f.request.assert_not_called();self.b.assert_called_once()

    def test_score_save_retry_does_not_repeat_gpt_or_change_direct(self):
        a=self.f.run_action();original=self.store.execute
        def fail(sql,args=(),missing_ok=False):
            if sql.startswith('INSERT OR IGNORE INTO attribute_concern_score_runs_v2'):raise StoreError('Mock score save failure')
            return original(sql,args,missing_ok)
        with patch.object(self.store,'execute',side_effect=fail):
            compared=self.run_pair();self.assertIn('save failure',compared['score_error'])
        retained=pending_scores(self.case,self.context[0]);self.assertEqual(len(retained),1)
        saved=retry_score_save(self.settings,self.store,retained[0]);self.assertEqual(saved['status'],'completed')
        self.b.assert_called_once();self.assertEqual(pending_scores(self.case,self.context[0]),[])
        self.assertEqual(self.store.latest_beliefs(self.case,self.context[0],self.context[3])['run_id'],a['run_id'])

    def test_bad_score_preserves_direct_and_does_not_repeat_automatically(self):
        self.data['assessments'][0]['concern_score']=None
        result=self.run_pair();self.assertEqual(result['direct']['status'],'completed');self.assertEqual(result['score']['status'],'failed')
        self.run_pair();self.b.assert_called_once();self.f.request.assert_called_once()

    def test_missing_duplicate_and_cross_attribute_fields_are_invalid(self):
        for change in ['missing','duplicate','refs','bool','string','unassessable']:
            data=copy.deepcopy(self.data)
            if change=='missing':data['assessments'].pop()
            elif change=='duplicate':data['assessments'][-1]=data['assessments'][0]
            elif change=='refs':data['assessments'][0]['supporting_evidence_ids']=['unsupported-id']
            elif change=='bool':data['assessments'][0]['concern_score']=True
            elif change=='string':data['assessments'][0]['concern_score']='2.6'
            else:data['assessments'][0]['assessable']=False
            with self.subTest(change=change),self.assertRaises(ValueError):validate_scores(data,self.f.reviewed)
        data=copy.deepcopy(self.data);data['assessments'][0].update(assessable=False,concern_score=None,supporting_evidence_ids=[])
        self.assertEqual(validate_scores(data,self.f.reviewed)['assessments'][0]['beliefs']['unknown'],1)

    def test_score_prompt_or_model_change_does_not_regenerate_direct(self):
        first=self.run_pair();changed=replace(self.settings,model='another-test-model')
        result=compare_beliefs(changed,self.case,self.store,request_a=self.f.request,request_b=self.b)
        self.assertEqual(first['direct']['run_id'],result['direct']['run_id'])
        self.assertNotEqual(first['score']['run_id'],result['score']['run_id'])
        self.f.request.assert_called_once();self.assertEqual(self.b.call_count,2)

    def test_changed_review_blocks_generation_for_old_snapshot(self):
        with patch('underwriting.assessment.score_service.current_input',return_value=(self.context[0],self.context[1],self.context[2],'changed-review',self.context[4])):
            with self.assertRaisesRegex(ValueError,'changed'):propose_scores(self.settings,self.case,self.store,self.context,request=self.b)
        self.b.assert_not_called()

    def test_duplicate_claim_and_running_lock_block_generation(self):
        import threading
        from underwriting.assessment import score_service as service
        key=score_identity(self.settings,self.context[2]);lock=threading.Lock();service._locks[key]=lock;lock.acquire()
        try:
            with self.assertRaisesRegex(ValueError,'already running'):propose_scores(self.settings,self.case,self.store,self.context,request=self.b)
        finally:lock.release()
        with patch.object(self.store,'claim',return_value=False):
            with self.assertRaisesRegex(ValueError,'reserved'):propose_scores(self.settings,self.case,self.store,self.context,request=self.b)
        self.b.assert_not_called()

    def test_confirmations_and_downstream_inputs_stay_direct(self):
        from underwriting.assessment.er_service import current_confirmed
        from underwriting.configuration.decision_model import get_model
        a=self.f.run_action()
        for identifier in [row['attribute_id'] for row in a['result']['assessments']]:confirm_belief(self.settings,self.case,self.store,a,identifier,'accept')
        before=current_confirmed(self.case,self.store,get_model());reviews=copy.deepcopy(self.store.belief_reviews(a['run_id']))
        self.run_pair()
        self.assertEqual(current_confirmed(self.case,self.store,get_model()),before)
        self.assertEqual(self.store.belief_reviews(a['run_id']),reviews)
        self.assertTrue(all(v=={'low':.4,'high':.4,'unknown':.2} for v in before[3].values()))

    def test_public_readonly_loads_direct_and_reports_score_missing_without_generation(self):
        a=self.f.run_action();self.f.request.reset_mock();readonly=replace(self.settings,access_mode='public_readonly',service_mode='turso_readonly')
        result=compare_beliefs(readonly,self.case,self.store,request_a=self.f.request,request_b=self.b)
        self.assertEqual(result['direct']['run_id'],a['run_id']);self.assertIsNone(result['score'])
        self.assertIn('disabled',result['score_error']);self.f.request.assert_not_called();self.b.assert_not_called()

    def test_local_owner_scope_is_restricted_to_proposals_and_cloud_is_denied(self):
        base=Settings(database_url='https://test.invalid',database_token='mock',service_mode='turso_readonly')
        with patch('underwriting.configuration.access.local_server',return_value=True),patch.dict(os.environ,{},clear=True),patch('underwriting.configuration.access.dotenv_values',return_value={'OPENAI_API_KEY':'mock-key'}):
            owner=local_comparison_settings(base);self.assertEqual(owner.service_mode,'comparison_owner');require_owner(owner);require_test_services(owner,generation=True)
            with patch('requests.post',side_effect=AssertionError('No network')):
                from underwriting.persistence.intake import TursoStore
                store=BeliefStore(owner)
                for sql in ['DELETE FROM attribute_belief_runs_v1','UPDATE attribute_belief_runs_v1 SET status=1','INSERT INTO attribute_belief_reviews_v1 VALUES (1)','CREATE TABLE other (x)','INSERT INTO er_aggregation_runs_v1 VALUES (1)']:
                    with self.assertRaises(PermissionError):store.execute(sql)
            with patch('underwriting.intake.service.OpenAI',side_effect=AssertionError('No GPT')):
                with self.assertRaises(PermissionError):structured_request(owner,'','',{},'decision_review')
            with patch.dict(os.environ,{'UNDERWRITING_PROFILE':'public_readonly'}):
                self.assertEqual(local_comparison_settings(base),base)
                with self.assertRaises(PermissionError):require_owner(owner)

    def test_interrupted_recorded_score_recovers_without_repeating_request(self):
        from underwriting.assessment.score_service import retain_score
        run=self.run_pair()['score'];interrupted=copy.deepcopy(run)
        interrupted.update(run_id='interrupted-score',status='running',result=None,started_at='2000-01-01T00:00:00+00:00',finished_at=None)
        retain_score(interrupted)
        recovered=retry_score_save(self.settings,self.store,interrupted)
        self.assertEqual(recovered['status'],'completed');self.assertTrue(recovered['recovered_from_received_response'])
        self.b.assert_called_once();self.f.request.assert_called_once()

    def test_failed_explicit_score_regeneration_preserves_success_and_confirmations(self):
        first=self.run_pair();score=first['score']
        confirmed=confirm_belief(self.settings,self.case,self.store,first['direct'],'F1','accept')
        self.data['assessments'][0]['concern_score']=9
        failed=propose_scores(self.settings,self.case,self.store,self.context,request=self.b,regenerate=True,confirmed=True,expected_run=score['run_id'])
        self.assertEqual(failed['status'],'failed')
        loaded=self.run_pair();self.assertEqual(loaded['score']['run_id'],score['run_id'])
        self.assertEqual(self.store.belief_reviews(first['direct']['run_id'])[0],confirmed)
        self.assertEqual(self.b.call_count,2);self.f.request.assert_called_once()

    def test_enabled_attribute_set_is_configuration_driven(self):
        from underwriting.assessment.evidence_schema import attribute_definitions
        reviewed=copy.deepcopy(self.f.reviewed);reviewed['attributes']=reviewed['attributes'][:2]
        definitions=attribute_definitions()[:2]
        result=validate_scores(score_response(reviewed),reviewed,definitions)
        self.assertEqual(len(result['assessments']),2)

    def test_stale_score_is_labelled_without_making_direct_stale(self):
        from underwriting.ui.score_comparison import score_stale
        result=self.run_pair();view={'score_run':result['score'],'settings':self.settings,'stale':False,'comparison_snapshot_hash':result['snapshot_hash']}
        self.assertFalse(score_stale(view))
        view['comparison_snapshot_hash']='changed';self.assertTrue(score_stale(view));self.assertFalse(view['stale'])

    def test_comparison_transport_allows_only_required_proposal_inserts(self):
        result=self.run_pair()
        owner=replace(self.settings,service_mode='comparison_owner')
        with patch.dict(os.environ,{},clear=True),patch('requests.post') as post:
            response=Mock();response.json.return_value={'results':[{'type':'ok','response':{'result':{'rows':[],'affected_row_count':1}}}]};post.return_value=response
            store=BeliefStore(owner);store.initialise_beliefs();store.initialise_scores()
            store.save_beliefs(result['direct']);store.save_scores(result['score'])
            self.assertEqual(post.call_count,7)

    def test_cloud_can_view_stale_score_without_regeneration(self):
        first=self.run_pair();readonly=replace(self.settings,service_mode='turso_readonly',access_mode='public_readonly',model='changed-model')
        result=compare_beliefs(readonly,self.case,self.store,request_a=self.f.request,request_b=self.b)
        self.assertEqual(result['score']['run_id'],first['score']['run_id']);self.assertTrue(result['score']['comparison_stale'])
        self.f.request.assert_called_once();self.b.assert_called_once()

    def test_comparison_ui_and_membership_examples(self):
        from streamlit.testing.v1 import AppTest
        result=self.run_pair();view={'run':result['direct'],'score_run':result['score'],'reviews':[],'stale':False,
          'settings':replace(self.settings,access_mode='public_readonly'),'store':self.store,'case':self.case,
          'origin':'Loaded from database','comparison_snapshot_hash':result['snapshot_hash']}
        script="import streamlit as st\nfrom underwriting.ui.beliefs import belief_details\nbelief_details(st.session_state['view'],'F1')"
        app=AppTest.from_string(script);app.session_state['view']=view;app.run()
        self.assertFalse(app.exception)
        headings=[m.value for m in app.markdown];self.assertIn('**Direct AI beliefs**',headings);self.assertIn('**AI score → beliefs**',headings)
        self.assertIn('60.0%',[m.value for m in app.metric]);self.assertIn('40.0%',[m.value for m in app.metric])
        self.assertTrue(next(b for b in app.button if b.label=='Confirm beliefs').disabled)
        self.f.request.assert_called_once();self.b.assert_called_once()

if __name__=='__main__':unittest.main()
