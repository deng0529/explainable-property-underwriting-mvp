"""Current Direct AI -> ER -> BRB -> Decision/overview, temporary storage only."""
import copy
import json
import unittest
from unittest.mock import Mock,patch
import test_decision as fixtures
from comparison_fixtures import response
from underwriting.configuration.settings import get_config
from underwriting.configuration.decision_model import get_model
from underwriting.assessment.belief_service import current_input
from underwriting.assessment.score_service import compare_beliefs,propose_method
from underwriting.assessment.er_service import calculate_er
from underwriting.assessment.brb_service import calculate_brb
from underwriting.decision.snapshot import assemble_snapshot
from underwriting.assessment.overview import load_overview
from underwriting.persistence.overview import OverviewStore

class CurrentDecisionTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.DecisionTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.case,self.store,self.settings=self.f.case,self.f.store,self.f.settings
        mode=patch.dict(get_config()['belief_comparison'],{'calculation_input_method':'direct_ai'})
        mode.start();self.addCleanup(mode.stop)
        self.reviewed=current_input(self.case,self.store)[2]
        self.a_data=response(self.reviewed,'direct')
        self.a=Mock(side_effect=lambda *_:({'status':'completed','model':'test-model'},json.dumps(self.a_data)))
        self.b=Mock(side_effect=lambda *_:({'status':'completed','model':'test-model'},json.dumps(response(self.reviewed,'score'))))

    def prepare(self):
        pair=compare_beliefs(self.settings,self.case,self.store,request_a=self.a,request_b=self.b)
        er=calculate_er(self.settings,self.case,self.store,input_method='direct_ai')
        brb,_=calculate_brb(self.settings,self.case,er,self.store)
        return pair,er,brb

    def test_current_direct_snapshot_overview_review_and_saved_reuse(self):
        pair,er,brb=self.prepare()
        snapshot=assemble_snapshot(self.case,self.store)
        self.assertEqual(snapshot['beliefs']['metadata']['run_id'],pair['direct']['run_id'])
        self.assertEqual(snapshot['beliefs']['human_confirmed'],{})
        self.assertEqual(snapshot['beliefs']['calculation_inputs'],er['confirmed_beliefs'])
        self.assertEqual(snapshot['brb']['run_id'],brb['run_id'])
        overview_store=OverviewStore.__new__(OverviewStore);overview_store.settings=self.settings
        def readonly(sql,args=(),missing_ok=False):
            self.assertTrue(sql.startswith('SELECT '));return self.store.execute(sql,args,missing_ok)
        overview_store.execute=readonly
        before=self.store.db.total_changes
        with patch('underwriting.assessment.overview.document_hash',return_value='mock-source'):
            overview=load_overview(self.case,overview_store,get_model())
        self.assertEqual(before,self.store.db.total_changes)
        self.assertEqual(overview['attributes']['F1']['values'],er['confirmed_beliefs']['F1'])
        self.assertIn('Direct AI',overview['attributes']['F1']['state'])
        self.assertEqual(overview['overall']['result'],brb['result'])
        run,origin=self.f.generate();self.assertEqual(run['status'],'succeeded')
        self.assertEqual(run['input_snapshot']['er']['run_id'],er['run_id'])
        loaded,origin=self.f.generate();self.assertEqual(run['review_run_id'],loaded['review_run_id'])
        self.assertIn('no GPT',origin);self.f.request.assert_called_once()
        self.a.assert_called_once();self.b.assert_called_once()

    def test_current_direct_human_confirmation_is_separate_from_ai_calculation(self):
        from underwriting.assessment.belief_service import confirm_belief
        pair,er,brb=self.prepare()
        run=dict(pair['direct'],assessment_settings=dict(get_config()['belief_assessment']))
        event=confirm_belief(self.settings,self.case,self.store,run,'F1','edit',.3,.4,'Independent underwriter judgement')
        snapshot=assemble_snapshot(self.case,self.store)
        self.assertEqual(snapshot['beliefs']['human_confirmed']['F1'],event['values'])
        self.assertEqual(snapshot['beliefs']['calculation_inputs']['F1'],{'low':.2,'high':.6,'unknown':.2})
        self.assertEqual(snapshot['er']['run_id'],er['run_id'])

    def test_missing_current_direct_blocks_before_decision_gpt(self):
        with self.assertRaisesRegex(ValueError,'Direct AI'):self.f.generate()
        self.f.request.assert_not_called()

    def test_new_direct_values_update_downstream_review(self):
        pair,er,brb=self.prepare();first,_=self.f.generate()
        self.a_data['assessments'][0].update(low=.3,high=.5,unknown=.2)
        propose_method(self.settings,self.case,self.store,pair['input_snapshot'],'direct',request=self.a,
            regenerate=True,confirmed=True,expected_run=pair['direct']['run_id'])
        with self.assertRaises(ValueError):assemble_snapshot(self.case,self.store)
        new_er=calculate_er(self.settings,self.case,self.store,input_method='direct_ai')
        calculate_brb(self.settings,self.case,new_er,self.store)
        second,_=self.f.generate();self.assertNotEqual(first['review_run_id'],second['review_run_id'])
        self.assertEqual(second['input_snapshot']['beliefs']['calculation_inputs']['F1']['low'],.3)
        self.assertEqual(self.f.request.call_count,2)

    def test_decision_prompt_change_requires_one_explicit_new_review(self):
        self.prepare();first,_=self.f.generate()
        definition=get_config()['prompts']['decision_review']
        with patch.dict(definition,{'version':'test-new-review-version'}):
            second,_=self.f.generate();self.assertEqual(second['status'],'succeeded')
            self.assertNotEqual(first['review_run_id'],second['review_run_id'])
            loaded,_=self.f.generate();self.assertEqual(second['review_run_id'],loaded['review_run_id'])
        self.assertEqual(self.f.request.call_count,2)

if __name__=='__main__':unittest.main()
