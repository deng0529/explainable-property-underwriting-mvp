"""Identical source/information reuses review/PDF across newly-created audit runs."""
import copy
import uuid
import unittest
from unittest.mock import patch
from underwriting.decision.content import review_content_hash,same_review_content
from underwriting.decision.snapshot import assemble_snapshot,snapshot_hash
from underwriting.decision.service import export_current_report
from underwriting.assessment.er_service import calculate_er
from underwriting.assessment.brb_service import calculate_brb
from underwriting.assessment.belief_service import current_input
from underwriting.intake.service import now
import test_decision as fixtures

class DecisionContentReuseTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.DecisionTests();self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store,self.case=self.fixture.store,self.fixture.case
    def rerun_identical_stages(self):
        digest,evidence,reviewed,review_hash,versions=current_input(self.case,self.store)
        proposal=self.store.latest_beliefs(self.case,digest,review_hash)
        evidence_events=self.store.reviews(evidence['run_id'])
        belief_events=self.store.belief_reviews(proposal['run_id'])
        new_evidence=copy.deepcopy(evidence)
        new_evidence.update(run_id=uuid.uuid4().hex,finished_at=now())
        self.store.save_evidence(new_evidence)
        for event in evidence_events:
            self.store.save_review(dict(event,event_id=uuid.uuid4().hex,run_id=new_evidence['run_id'],created_at=now()))
        _,_,new_reviewed,new_hash,new_versions=current_input(self.case,self.store)
        new_proposal=copy.deepcopy(proposal)
        new_proposal.update(run_id=uuid.uuid4().hex,evidence_run_id=new_evidence['run_id'],reviewed_hash=new_hash,
                            reviewed_evidence=new_reviewed,evidence_review_versions=new_versions,finished_at=now())
        self.store.save_beliefs(new_proposal)
        for event in belief_events:
            self.store.save_belief_review(dict(event,event_id=uuid.uuid4().hex,run_id=new_proposal['run_id'],reviewed_hash=new_hash,created_at=now()))
        intake=copy.deepcopy(self.fixture.intake)
        intake.update(run_id=uuid.uuid4().hex,finished_at=now())
        self.store.save(intake,None)
        er=calculate_er(self.fixture.settings,self.case,self.store)
        calculate_brb(self.fixture.settings,self.case,er,self.store)

    def test_all_stages_rerun_identical_results_reuses_review_and_pdf(self):
        run,_=self.fixture.generate()
        initial_snapshot=assemble_snapshot(self.case,self.store)
        pdf=export_current_report(self.fixture.settings,self.case,self.store)
        with patch('underwriting.decision.service.structured_request',side_effect=AssertionError('Unchanged company information must not call GPT')),patch('underwriting.decision.report.render_report',side_effect=AssertionError('Reuse the saved PDF')):
            for _ in range(3):
                self.rerun_identical_stages()
                updated=assemble_snapshot(self.case,self.store)
                self.assertNotEqual(snapshot_hash(initial_snapshot),snapshot_hash(updated))
                self.assertEqual(review_content_hash(initial_snapshot),review_content_hash(updated))
                loaded,source=self.fixture.generate()
                self.assertEqual(loaded['review_run_id'],run['review_run_id'])
                self.assertIn('no GPT call',source)
                self.assertEqual(export_current_report(self.fixture.settings,self.case,self.store),pdf)
        self.fixture.request.assert_called_once()
        self.assertEqual(self.store.review_by_id(run['review_run_id']),run)
        self.assertEqual(int(self.store.execute('SELECT COUNT(*) FROM decision_review_runs')['rows'][0][0]['value']),1)

    def test_real_document_information_or_belief_changes_are_not_audit_changes(self):
        snapshot=assemble_snapshot(self.case,self.store)
        for modify in (
            lambda value:value['source'].update(sha256='changed-document'),
            lambda value:value['beliefs']['human_confirmed']['F1'].update(low=.5,high=.3),
            lambda value:value['submission'].update(outcome='Pause for clarification'),
            lambda value:value['evidence']['human_reviewed']['attributes'][0].update(human_notes='New broker clarification'),
            lambda value:value['er']['groups']['fire']['assigned_beliefs'].update(high=.8),
        ):
            changed=copy.deepcopy(snapshot);modify(changed)
            self.assertFalse(same_review_content(snapshot,changed))

    def test_repeated_confirmations_and_dates_do_not_change_content(self):
        snapshot=assemble_snapshot(self.case,self.store)
        changed=copy.deepcopy(snapshot)
        changed['submission']['human_reviews']=[{'event_id':'new-id','created_at':now(),'status':'review_confirmed',
            'basic_information_notes':'','screening_notes':{'SC01':''},'reason':'','recipient':None,
            'basic_information_reviewed':True,'screening_reviewed':True}]
        for stage in ('evidence','beliefs'):
            for event in changed[stage]['review_events']:
                event.update(event_id=uuid.uuid4().hex,created_at=now())
        changed['er'].update(run_id='new-er-run',finished_at=now())
        changed['brb'].update(run_id='new-brb-run',er_run_id='new-er-run',finished_at=now())
        self.assertTrue(same_review_content(snapshot,changed))
