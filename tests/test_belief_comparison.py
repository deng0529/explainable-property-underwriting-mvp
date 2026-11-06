"""Full-context comparison v2: mocks and temporary SQLite; no paid/live writes."""
import copy
import hashlib
import json
import os
import unittest
from decimal import Decimal
from dataclasses import replace
from unittest.mock import Mock,patch
from underwriting.assessment import score_service as service
from underwriting.assessment.score_schema import score_to_beliefs,validate_scores
from underwriting.assessment.direct_comparison_schema import validate_direct
from underwriting.assessment.comparison_context import freeze_context,text_hash
from underwriting.assessment.belief_service import current_input,confirm_belief
from underwriting.configuration.settings import get_config,validate,ConfigError
from underwriting.configuration.access import Settings,local_comparison_settings,require_owner,require_test_services
from underwriting.persistence.beliefs import BeliefStore
from underwriting.persistence.intake import StoreError
from underwriting.intake.service import structured_request
from comparison_fixtures import response
import test_beliefs as fixtures

class MappingTests(unittest.TestCase):
    def test_endpoints_and_partial_unknown(self):
        for score,u,expected in [(1,0,(1,0,0)),(5,0,(0,1,0)),(2,0,(.75,.25,0)),(2.6,0,(.6,.4,0)),(2,.3,(.525,.175,.3)),(1,.3,(.7,0,.3)),(5,.3,(0,.7,.3))]:
            values=score_to_beliefs(True,score,u);self.assertEqual(tuple(values.values()),expected)
        self.assertEqual(score_to_beliefs(False,None,1),{'low':0,'high':0,'unknown':1})

    def test_invalid_scores_unknown_and_assessability_are_rejected(self):
        for value in [None,True,'2',0,5.1,float('nan'),float('inf'),10**400,Decimal('NaN')]:
            with self.subTest(score=str(value)),self.assertRaises(ValueError):score_to_beliefs(True,value,.3)
        for u in [None,True,'0.3',-.1,1.1,float('nan'),float('inf')]:
            with self.subTest(unknown=str(u)),self.assertRaises(ValueError):score_to_beliefs(True,2,u)
        for args in [(True,2,1),(False,None,.5),(False,3,1),('false',None,1),(1,2,.3)]:
            with self.assertRaises(ValueError):score_to_beliefs(*args)

    def test_endpoints_are_configurable_without_clamping(self):
        settings=copy.deepcopy(get_config()['belief_comparison']);settings['score_scale'].update(minimum=0,maximum=10)
        self.assertEqual(score_to_beliefs(True,2,.3,settings),{'low':.56,'high':.14,'unknown':.3})

    def test_yaml_scale_validation(self):
        for key,value in [('minimum',True),('minimum',5),('maximum',float('inf'))]:
            cfg=copy.deepcopy(get_config());cfg['belief_comparison']['score_scale'][key]=value
            with self.assertRaises(ConfigError):validate(cfg)

class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.BeliefTests();self.f.setUp();self.addCleanup(self.f.tearDown)
        self.store,self.case,self.settings=self.f.store,self.f.case,self.f.settings
        self.reviewed=current_input(self.case,self.store)[2]
        self.a_data=response(self.reviewed,'direct');self.b_data=response(self.reviewed,'score')
        self.a=Mock(side_effect=lambda *_:({'status':'completed','model':'test-model'},json.dumps(self.a_data)))
        self.b=Mock(side_effect=lambda *_:({'status':'completed','model':'test-model'},json.dumps(self.b_data)))
        self.snapshot=freeze_context(self.settings,self.case,self.store)

    def run_pair(self,settings=None):
        return service.compare_beliefs(settings or self.settings,self.case,self.store,request_a=self.a,request_b=self.b)

    def test_interpolated_score_does_not_require_evidence_for_both_directions(self):
        for direction in ('low','high'):
            data=copy.deepcopy(self.b_data)
            data['assessments'][0][direction+'_support']['evidence_ids']=[]
            data['assessments'][0][direction+'_support']['explanation']='No separate directional finding; fractional membership is interpolated.'
            validated=validate_scores(data,self.reviewed,pages=self.snapshot['source']['pages'])
            self.assertEqual(validated['assessments'][0]['beliefs'],{'low':.525,'high':.175,'unknown':.3})
        data['assessments'][0]['low_support']['evidence_ids']=[]
        data['assessments'][0]['high_support']['evidence_ids']=[]
        with self.assertRaisesRegex(ValueError,'requires reviewed evidence'):
            validate_scores(data,self.reviewed,pages=self.snapshot['source']['pages'])
        direct=copy.deepcopy(self.a_data);direct['assessments'][0]['high_support']['evidence_ids']=[]
        with self.assertRaisesRegex(ValueError,'Assigned high'):
            validate_direct(direct,self.reviewed,pages=self.snapshot['source']['pages'])

    def test_failed_score_can_be_revalidated_without_provider_or_overwriting_history(self):
        self.b_data['assessments'][0]['high_support']['evidence_ids']=[]
        original=service.validate_result
        def old_validator(data,snapshot,method,settings):
            if method=='score':raise ValueError('Assigned high support requires reviewed evidence IDs.')
            return original(data,snapshot,method,settings)
        with patch.object(service,'validate_result',side_effect=old_validator):
            pair=self.run_pair()
        failed=pair['score'];self.assertEqual(failed['status'],'failed')
        with patch.object(service,'structured_request',side_effect=AssertionError('No GPT call')):
            recovered=self.run_pair()['score']
        self.assertEqual(recovered['status'],'completed')
        self.assertNotEqual(recovered['run_id'],failed['run_id'])
        self.assertEqual(recovered['recovered_from_run_id'],failed['run_id'])
        self.assertEqual(failed['status'],'failed');self.a.assert_called_once();self.b.assert_called_once()
        loaded=self.run_pair();self.assertEqual(loaded['score']['run_id'],recovered['run_id'])
        self.a.assert_called_once();self.b.assert_called_once()

    def test_independent_prompts_identical_full_input_and_no_output_leakage(self):
        result=self.run_pair();a,b=result['direct'],result['score']
        self.assertEqual(a['input_snapshot'],b['input_snapshot']);self.assertEqual(a['input_hash'],b['input_hash'])
        self.assertEqual(self.a.call_args.args[-1],'attribute_direct_comparison');self.assertEqual(self.b.call_args.args[-1],'attribute_concern_scores')
        self.assertNotEqual(self.a.call_args.args[2],self.b.call_args.args[2])
        for prompt in (self.a.call_args.args[2],self.b.call_args.args[2]):
            self.assertIn('[PAGE 1]',prompt);self.assertIn(self.reviewed['attributes'][0]['evidence_items'][0]['exact_quote'],prompt)
            self.assertIn('assessment_guidance',prompt);self.assertIn('information_gaps',prompt);self.assertIn('contradictions',prompt)
        self.assertNotIn(self.a_data['assessments'][0]['allocation_explanation'],self.b.call_args.args[2])
        self.assertEqual(a['prompt']['version'],'2.0');self.assertEqual(b['prompt']['version'],'2.0')
        self.assertEqual(b['result']['assessments'][0]['beliefs'],{'low':.525,'high':.175,'unknown':.3})

    def test_saved_source_is_reused_without_extraction(self):
        with patch('underwriting.assessment.comparison_context.source_context',side_effect=AssertionError('No PDF re-extraction')):
            self.run_pair();self.run_pair()
        self.a.assert_called_once();self.b.assert_called_once()

    def test_both_missing_source_caches_use_one_extraction_then_saved_snapshot(self):
        self.store.execute('DELETE FROM attribute_source_text_v1')
        from test_evidence import TEXT
        with patch('underwriting.assessment.comparison_context.source_context',return_value=('mock-source',[TEXT])) as extract:
            self.run_pair();self.run_pair();extract.assert_called_once()

    def test_company_submission_document_and_cache_mismatch_block_before_gpt(self):
        for field,value in [('case_id','another'),('source_version','wrong'),('source_hash','wrong')]:
            wrong=copy.deepcopy(self.f.evidence);wrong[field]=value
            with patch.object(self.store,'latest_evidence',return_value=wrong):
                with self.assertRaises(ValueError):self.run_pair()
        bad={'source_hash':'mock-source','extraction_version':self.snapshot['source']['extraction_version'],'pages':['bad'],'text_hash':'wrong'}
        with patch.object(self.store,'source',return_value=bad):
            with self.assertRaisesRegex(ValueError,'provenance'):self.run_pair()
        self.a.assert_not_called();self.b.assert_not_called()

    def test_each_method_reuses_its_own_result(self):
        first=self.run_pair();second=self.run_pair()
        self.assertEqual(first['direct']['run_id'],second['direct']['run_id']);self.assertEqual(first['score']['run_id'],second['score']['run_id'])
        self.a.assert_called_once();self.b.assert_called_once()

    def test_only_missing_method_is_requested(self):
        a=service.propose_method(self.settings,self.case,self.store,self.snapshot,'direct',request=self.a)
        self.a.reset_mock();result=self.run_pair();self.assertEqual(result['direct']['run_id'],a['run_id'])
        self.a.assert_not_called();self.b.assert_called_once()

    def test_missing_a_reuses_saved_b(self):
        b=service.propose_scores(self.settings,self.case,self.store,self.snapshot,request=self.b)
        self.b.reset_mock();result=self.run_pair();self.assertEqual(result['score']['run_id'],b['run_id'])
        self.b.assert_not_called();self.a.assert_called_once()

    def test_prompt_and_configuration_changes_invalidate_only_matching_method(self):
        first=self.run_pair();cfg=copy.deepcopy(get_config());cfg['prompts']['attribute_direct_comparison']['version']='2.1'
        with patch('underwriting.assessment.score_service.get_config',return_value=cfg):result=self.run_pair()
        self.assertNotEqual(result['direct']['run_id'],first['direct']['run_id']);self.assertEqual(result['score']['run_id'],first['score']['run_id'])
        self.assertEqual(self.a.call_count,2);self.b.assert_called_once()
        changed=self.run_pair(replace(self.settings,model='another-model'))
        self.assertNotEqual(changed['score']['run_id'],first['score']['run_id'])

    def test_old_score_version_never_reused_as_new(self):
        self.store.initialise_scores()
        old={'run_id':'old-v1','case_id':self.case['case_id'],'source_version':self.case['submission_version'],'source_hash':'mock-source',
             'reviewed_hash':current_input(self.case,self.store)[3],'cache_key':'old-key','finished_at':'2000-01-01','status':'completed','method_id':'concern_score_v1','result':{'assessments':[]}}
        self.store.save_scores(old);result=self.run_pair()
        self.assertNotEqual(result['score']['run_id'],'old-v1')
        self.assertEqual(self.store.execute('SELECT COUNT(*) FROM attribute_concern_score_runs_v2')['rows'][0][0]['value'],'2')

    def test_database_error_never_becomes_generation_fallback(self):
        with patch.object(self.store,'latest_comparison_direct',side_effect=StoreError('Mock outage')):
            with self.assertRaises(StoreError):self.run_pair()
        self.a.assert_not_called();self.b.assert_not_called()

    def test_one_method_failure_retains_other_and_targeted_retry_is_explicit(self):
        self.a_data['assessments'][0]['low']=.9
        first=self.run_pair();self.assertEqual(first['direct']['status'],'failed');self.assertEqual(first['score']['status'],'completed')
        self.run_pair();self.a.assert_called_once();self.b.assert_called_once()
        self.a_data=response(self.reviewed,'direct')
        recovered=service.propose_method(self.settings,self.case,self.store,self.snapshot,'direct',request=self.a,regenerate=True,confirmed=True,expected_run=first['direct']['run_id'])
        self.assertEqual(recovered['status'],'completed');self.assertEqual(self.a.call_count,2);self.b.assert_called_once()

    def test_score_failure_does_not_hide_direct(self):
        self.b_data['assessments'][0]['unknown']=1
        result=self.run_pair();self.assertEqual(result['score']['status'],'failed');self.assertEqual(result['direct']['status'],'completed')

    def test_save_retry_never_repeats_generation(self):
        original=self.store.execute
        def fail(sql,args=(),missing_ok=False):
            if sql.startswith('INSERT OR IGNORE INTO attribute_direct_comparison_runs_v2'):raise StoreError('Mock save outage')
            return original(sql,args,missing_ok)
        with patch.object(self.store,'execute',side_effect=fail):result=self.run_pair()
        self.assertTrue(result['direct']['save_pending']);self.assertEqual(result['score']['status'],'completed')
        pending=service.pending_comparisons(self.case,'mock-source','direct');self.assertEqual(len(pending),1)
        saved=service.retry_comparison_save(self.settings,self.store,pending[0]);self.assertEqual(saved['status'],'completed')
        self.run_pair();self.a.assert_called_once();self.b.assert_called_once()

    def test_interrupted_received_response_can_be_recovered(self):
        run=self.run_pair()['score'];interrupted=copy.deepcopy(run)
        interrupted.update(run_id='interrupted',status='running',result=None,started_at='2000-01-01T00:00:00+00:00',finished_at=None)
        service.retain(interrupted)
        saved=service.retry_comparison_save(self.settings,self.store,interrupted)
        self.assertEqual(saved['status'],'completed');self.assertTrue(saved['recovered_from_received_response'])
        self.a.assert_called_once();self.b.assert_called_once()

    def test_duplicate_lock_and_claim_prevent_extra_call(self):
        import threading
        schema,key,suffix,name=service.spec('direct');identity=service.method_identity(self.settings,self.snapshot,'direct')
        scope=service.content_hash(['direct',self.case['case_id'],self.case['submission_version'],'mock-source',identity])
        lock=threading.Lock();service._locks[scope]=lock;lock.acquire()
        try:
            with self.assertRaisesRegex(ValueError,'running'):service.propose_method(self.settings,self.case,self.store,self.snapshot,'direct',request=self.a)
        finally:lock.release()
        with patch.object(self.store,'claim',return_value=False):
            with self.assertRaisesRegex(ValueError,'reserved'):service.propose_method(self.settings,self.case,self.store,self.snapshot,'direct',request=self.a)
        self.a.assert_not_called()

    def test_canonical_confirmations_and_er_brb_inputs_unchanged(self):
        from underwriting.assessment.er_service import current_confirmed
        from underwriting.configuration.decision_model import get_model
        canonical=self.f.run_action()
        for item in canonical['result']['assessments']:confirm_belief(self.settings,self.case,self.store,canonical,item['attribute_id'],'accept')
        before=current_confirmed(self.case,self.store,get_model());reviews=copy.deepcopy(self.store.belief_reviews(canonical['run_id']))
        result=self.run_pair()
        self.assertEqual(result['canonical']['run_id'],canonical['run_id'])
        self.assertEqual(current_confirmed(self.case,self.store,get_model()),before);self.assertEqual(self.store.belief_reviews(canonical['run_id']),reviews)
        self.assertEqual(self.store.execute('SELECT COUNT(*) FROM attribute_belief_runs_v1')['rows'][0][0]['value'],'1')

    def test_public_readonly_never_generates_or_writes(self):
        readonly=replace(self.settings,service_mode='turso_readonly',access_mode='public_readonly')
        result=self.run_pair(readonly);self.assertIsNone(result['direct']);self.assertIsNone(result['score'])
        self.a.assert_not_called();self.b.assert_not_called()
        self.run_pair();loaded=self.run_pair(readonly)
        self.assertEqual(loaded['direct']['result_source'],'database');self.a.assert_called_once();self.b.assert_called_once()

    def test_local_comparison_access_stays_scoped(self):
        owner=replace(self.settings,service_mode='comparison_owner')
        with patch.dict(os.environ,{},clear=True),patch('requests.post') as post:
            response_mock=Mock();response_mock.json.return_value={'results':[{'type':'ok','response':{'result':{'rows':[],'affected_row_count':1}}}]};post.return_value=response_mock
            store=BeliefStore(owner);store.initialise_comparison_direct();store.initialise_scores()
            self.assertEqual(post.call_count,4)
            for sql in ('DELETE FROM attribute_direct_comparison_runs_v2','INSERT INTO er_aggregation_runs_v1 VALUES (1)','UPDATE attribute_concern_score_runs_v2 SET status=1'):
                with self.assertRaises(PermissionError):store.execute(sql)
            with patch('underwriting.intake.service.OpenAI',side_effect=AssertionError('No GPT')):
                with self.assertRaises(PermissionError):structured_request(owner,'','',{},'decision_review')
            with patch.dict(os.environ,{'UNDERWRITING_PROFILE':'public_readonly'}):
                with self.assertRaises(PermissionError):require_owner(owner)

    def test_direct_validation_and_material_unknown_consistency(self):
        for field,value in [('low',float('nan')),('low',True),('low',.225),('high',.99)]:
            data=copy.deepcopy(self.a_data);data['assessments'][0][field]=value
            with self.assertRaises(ValueError):validate_direct(data,self.reviewed,pages=self.snapshot['source']['pages'])
        data=copy.deepcopy(self.a_data);data['assessments'][0].update(low=.4,high=.6,unknown=0)
        with self.assertRaisesRegex(ValueError,'material'):validate_direct(data,self.reviewed,pages=self.snapshot['source']['pages'])
        data['assessments'][0]['unresolved_information']=[]
        validate_direct(data,self.reviewed,pages=self.snapshot['source']['pages'])

    def test_missing_duplicate_cross_attribute_ids_and_score_fields(self):
        for change in ('missing','duplicate','refs','unknown','unassessable'):
            data=copy.deepcopy(self.b_data)
            if change=='missing':data['assessments'].pop()
            elif change=='duplicate':data['assessments'][-1]=data['assessments'][0]
            elif change=='refs':data['assessments'][0]['low_support']['evidence_ids']=['unsupported']
            elif change=='unknown':data['assessments'][0].pop('unknown')
            else:data['assessments'][0]['assessable']=False
            with self.assertRaises(ValueError):validate_scores(data,self.reviewed,pages=self.snapshot['source']['pages'])
        row=self.b_data['assessments'][0];row.update(assessable=False,concern_score=None,unknown=1)
        row['low_support']['evidence_ids']=[];row['high_support']['evidence_ids']=[]
        self.assertEqual(validate_scores(self.b_data,self.reviewed,pages=self.snapshot['source']['pages'])['assessments'][0]['beliefs']['unknown'],1)

    def test_unreviewed_document_finding_requires_valid_source_and_is_not_adopted(self):
        evidence_before=copy.deepcopy(self.store.latest_evidence(self.case,'mock-source'))
        quote=self.snapshot['source']['pages'][0]
        flag={'issue':'Material contextual finding requires review','materiality':'material','evidence_ids':[],'page':1,'section':'Mock section','exact_quote':quote}
        self.a_data['assessments'][0]['evidence_review_flags']=[flag]
        result=self.run_pair();self.assertEqual(result['direct']['status'],'completed')
        self.assertEqual(self.store.latest_evidence(self.case,'mock-source'),evidence_before)
        for key,value in [('page',2),('exact_quote','Fabricated quotation'),('page',None)]:
            data=copy.deepcopy(self.a_data);data['assessments'][0]['evidence_review_flags'][0][key]=value
            with self.assertRaises(ValueError):validate_direct(data,self.reviewed,pages=self.snapshot['source']['pages'])

    def test_configured_attributes_and_source_text_are_not_templated_twice(self):
        reviewed=copy.deepcopy(self.reviewed);reviewed['attributes']=reviewed['attributes'][:2]
        from underwriting.assessment.evidence_schema import attribute_definitions
        self.assertEqual(len(validate_scores(response(reviewed,'score'),reviewed,attribute_definitions()[:2],pages=self.snapshot['source']['pages'])['assessments']),2)
        snapshot=copy.deepcopy(self.snapshot);snapshot['source']['page_labelled_text']+=' {{configured_concern_scale}}'
        prompt,_=service.comparison_prompt(self.case,snapshot,'score');self.assertIn('{{configured_concern_scale}}',prompt)

    def test_ui_compares_new_methods_and_keeps_confirmation_canonical(self):
        from streamlit.testing.v1 import AppTest
        canonical=self.f.run_action();result=self.run_pair()
        view={'run':canonical,'comparison_direct':result['direct'],'score_run':result['score'],'reviews':[],'stale':False,
              'settings':replace(self.settings,access_mode='public_readonly'),'store':self.store,'case':self.case,
              'comparison_snapshot_hash':result['snapshot_hash'],'comparison_reviewed_snapshot_hash':self.snapshot['upstream']['reviewed_snapshot_hash']}
        script="import streamlit as st\nfrom underwriting.ui.beliefs import belief_details\nbelief_details(st.session_state['view'],'F1')"
        app=AppTest.from_string(script);app.session_state['view']=view;app.run()
        self.assertFalse(app.exception)
        self.assertIn('**Score + Unknown**',[m.value for m in app.markdown])
        self.assertIn('52.5%',[m.value for m in app.metric]);self.assertIn('17.5%',[m.value for m in app.metric])
        for heading in ('**Low support**','**High support**','**Unknown support**'):
            self.assertIn(heading,[m.value for m in app.markdown])
        self.assertTrue(next(b for b in app.button if b.label=='Confirm beliefs').disabled)
        app.run();self.a.assert_called_once();self.b.assert_called_once()

if __name__=='__main__':unittest.main()
