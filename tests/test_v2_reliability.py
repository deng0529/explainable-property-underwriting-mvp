"""V2 reliability: isolated SQLite, recorded responses and fault injection only."""
import copy
import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch, Mock
from underwriting.configuration.access import Settings, load_settings, require_owner, require_test_services
from underwriting.configuration.settings import get_config
from underwriting.persistence.intake import TursoStore, StoreError
from underwriting.persistence.decision import DecisionStore
from underwriting.reliability.prerequisites import assessment_entry, PrerequisiteError
from underwriting.reliability.inputs import InputSizeError, check_document, check_request
from underwriting.reliability.diagnostics import diagnostic
from underwriting.assessment.evidence_service import retry_evidence_save, retain_evidence, retained, review_attribute
from underwriting.assessment.belief_service import retry_belief_save, retain_beliefs, current_input, propose_beliefs
from underwriting.intake.service import retry_save, retain
import test_evidence as evidence_fixtures
import test_beliefs as belief_fixtures
import test_intake as intake_fixtures


class IsolationTests(unittest.TestCase):
    def test_default_offline_never_uses_v1_credential_names(self):
        from underwriting.configuration.settings import get_config
        config=copy.deepcopy(get_config());config['services']['mode']='offline'
        with patch('underwriting.configuration.access.get_config',return_value=config), patch('underwriting.configuration.access.load_dotenv'), patch.dict(os.environ,{
            'OPENAI_API_KEY':'v1-do-not-use','TURSO_DATABASE_URL':'https://production.invalid','TURSO_AUTH_TOKEN':'v1-do-not-use',
            'V2_OPENAI_API_KEY':'','V2_TURSO_DATABASE_URL':'','V2_TURSO_AUTH_TOKEN':''}):
            settings=load_settings({})
        self.assertEqual(settings.service_mode,'offline')
        self.assertEqual((settings.api_key,settings.database_url,settings.database_token),('','',''))
        self.assertEqual(settings.missing(paid=False),[])
        with patch('requests.post',side_effect=AssertionError('No egress')) as post:
            with self.assertRaisesRegex(PermissionError,'Offline mode'):
                require_test_services(settings,generation=True)
            post.assert_not_called()

    def test_external_test_services_require_explicit_scope_and_allowlist(self):
        settings=Settings(database_url='https://unexpected.invalid',service_mode='test_turso')
        with self.assertRaisesRegex(PermissionError,'confirmed test scope'):
            require_test_services(settings)
        with self.assertRaisesRegex(PermissionError,'hostname'):
            require_test_services(replace(settings,test_scope_confirmed=True,allowed_test_database_hosts=('test.invalid',)))

    def test_local_store_survives_fresh_client_and_readonly_blocks_writes(self):
        with tempfile.TemporaryDirectory() as directory, patch('underwriting.persistence.local.PROJECT_ROOT',Path(directory)), \
             patch('underwriting.configuration.access.local_server',return_value=True), patch('requests.post',side_effect=AssertionError('No network')):
            settings=Settings(access_mode='local_owner')
            store=DecisionStore(settings)
            case={'case_id':'OFFLINE','submission_version':'1'}
            intake_fixtures.seed_submission(store,case,'offline-hash')
            read=DecisionStore(replace(settings,access_mode='public_readonly'))
            self.assertEqual(read.latest('OFFLINE','1','offline-hash')['case_id'],'OFFLINE')
            with self.assertRaises(PermissionError): read.initialise(None)
            with self.assertRaises(PermissionError): read.transaction([('CREATE TABLE forbidden (x)',[])])

    def test_offline_database_error_does_not_become_missing_or_network_fallback(self):
        with tempfile.TemporaryDirectory() as directory, patch('underwriting.persistence.local.PROJECT_ROOT',Path(directory)):
            path=Path(directory)/'.runtime/v2.sqlite';path.parent.mkdir();path.touch()
            store=TursoStore(Settings())
            with patch('underwriting.persistence.local.sqlite3.connect',side_effect=sqlite3.OperationalError('fixture outage')), \
                 patch('requests.post',side_effect=AssertionError('No network')):
                with self.assertRaises(StoreError):store.latest('case','1')

    def test_diagnostics_never_include_exception_body_or_source_text(self):
        value=diagnostic('Evidence',{'run_id':'safe-run','status':'failed'},error=ValueError('secret-token-and-document-text'))
        self.assertNotIn('secret-token',json.dumps(value))
        self.assertEqual(set(value),{'schema_version','stage','run_id','status','result_source','duration_seconds','error','available_recovery_action'})


class RecoveryTests(unittest.TestCase):
    def evidence_fixture(self):
        f=evidence_fixtures.EvidenceTests();f.setUp();self.addCleanup(f.tearDown);return f
    def belief_fixture(self):
        f=belief_fixtures.BeliefTests();f.setUp();self.addCleanup(f.tearDown);return f
    def interrupted(self, run, identifier):
        value=copy.deepcopy(run)
        value.update(run_id=identifier,status='running',result=None,finished_at=None,started_at='2000-01-01T00:00:00+00:00')
        return value

    def test_received_evidence_recovers_after_restart_without_new_request(self):
        f=self.evidence_fixture();first=f.run_action()
        run=self.interrupted(first,'interrupted-evidence');retain_evidence(run)
        recovered=retry_evidence_save(f.settings,f.store,run)
        self.assertEqual(recovered['status'],'completed')
        self.assertTrue(recovered['recovered_from_received_response'])
        self.assertEqual(recovered['result'],first['result'])
        self.assertEqual(f.request.call_count,1)
        self.assertEqual(retained(f.case,'test-hash'),[])
        self.assertEqual(first['status'],'completed')

    def test_received_beliefs_recover_without_new_request(self):
        f=self.belief_fixture();first=f.run_action()
        run=self.interrupted(first,'interrupted-beliefs');retain_beliefs(run)
        recovered=retry_belief_save(f.settings,f.store,run)
        self.assertEqual(recovered['status'],'completed')
        self.assertEqual(recovered['result'],first['result'])
        self.assertEqual(f.request.call_count,1)

    def test_invalid_retained_response_does_not_replace_success(self):
        f=self.evidence_fixture();first=f.run_action()
        run=self.interrupted(first,'invalid-retained')
        data=copy.deepcopy(f.data);data['attributes'][0]['evidence_items'][0]['exact_quote']='Invented unsupported quote'
        run['response_text']=json.dumps(data);retain_evidence(run)
        recovered=retry_evidence_save(f.settings,f.store,run)
        self.assertEqual(recovered['status'],'failed')
        self.assertEqual(f.store.latest_evidence(f.case,'test-hash')['run_id'],first['run_id'])
        self.assertEqual(f.request.call_count,1)

    def test_active_recovery_is_blocked_and_save_failure_retains_response(self):
        f=self.evidence_fixture();first=f.run_action()
        import underwriting.assessment.evidence_service as service
        run=self.interrupted(first,'still-active')
        lock=service._locks[(f.case['case_id'],f.case['submission_version'],'test-hash')]
        lock.acquire()
        try:
            with self.assertRaisesRegex(ValueError,'still running'):
                retry_evidence_save(f.settings,f.store,run)
        finally:lock.release()
        f.store.fail_save=True
        with self.assertRaises(StoreError):retry_evidence_save(f.settings,f.store,dict(first,run_id='save-failed'))
        pending=retained(f.case,'test-hash')
        self.assertEqual(pending[0]['diagnostics']['status'],'save_failed')
        self.assertEqual(pending[0]['diagnostics']['available_recovery_action'],'retry_save')
        self.assertTrue(pending[0]['raw_response'])
        self.assertEqual(f.request.call_count,1)

    def test_no_recorded_response_is_ambiguous_and_never_automatically_repeated(self):
        f=intake_fixtures.IntakeTests();f.setUp();self.addCleanup(f.tearDown)
        first=f.run_action();run=self.interrupted(first,'ambiguous-intake')
        run.update(raw_response=None,response_text=None)
        retain(run)
        recovered=retry_save(f.settings,None,f.store,run,case=f.case)
        self.assertEqual(recovered['status'],'failed')
        self.assertIn('ambiguous',recovered['error'])
        self.assertIn('cannot be recovered',recovered['recovery_limit'])
        self.assertEqual(f.request.call_count,1)
        self.assertEqual(f.store.latest(f.case['case_id'],f.case['submission_version'],'test-hash')['run_id'],first['run_id'])


class WorkflowReuseTests(unittest.TestCase):
    def test_missing_stale_or_blocked_submission_prevents_paid_evidence(self):
        f=evidence_fixtures.EvidenceTests();f.setUp();self.addCleanup(f.tearDown)
        with self.assertRaises(PrerequisiteError) as caught:assessment_entry(f.case,f.store,'new-source')
        self.assertEqual(caught.exception.status,'stale')
        saved=f.store.latest(f.case['case_id'],'1','test-hash');saved=copy.deepcopy(saved)
        saved['run_id']='scope-blocked';saved['finished_at']='2099-01-01';saved['result']['screening_checks'][2]['result']='fail'
        f.store.save(saved,None)
        with self.assertRaisesRegex(PrerequisiteError,'Outside demo scope'):f.run_action()
        f.request.assert_not_called()
        f.store.db.execute('DELETE FROM submission_intake_runs_v1')
        with self.assertRaisesRegex(PrerequisiteError,'First load'):f.run_action()
        f.request.assert_not_called()

    def test_identical_evidence_run_id_change_reuses_actual_proposal_service(self):
        f=belief_fixtures.BeliefTests();f.setUp();self.addCleanup(f.tearDown)
        first=f.run_action();before=current_input(f.case,f.store)[3]
        newer=copy.deepcopy(f.evidence);newer.update(run_id='new-audit-id',finished_at='2099-01-01')
        f.store.save_evidence(newer)
        for attribute in newer['result']['attributes']:
            review_attribute(f.settings,f.store,newer,attribute['attribute_id'],
                {item['evidence_id']:'Human reviewed mock fact' for item in attribute['evidence_items']},'Mock human note',True)
        self.assertEqual(current_input(f.case,f.store)[3],before)
        loaded=f.run_action()
        self.assertEqual(first['run_id'],loaded['run_id'])
        self.assertEqual(f.request.call_count,1)
        self.assertEqual(first['reviewed_evidence']['evidence_run_id'],'mock-evidence-run')
        attribute=newer['result']['attributes'][0]
        review_attribute(f.settings,f.store,newer,attribute['attribute_id'],
            {item['evidence_id']:'Changed material fact' for item in attribute['evidence_items']},'Mock human note',True)
        self.assertNotEqual(current_input(f.case,f.store)[3],before)

    def test_oversized_source_stops_before_claim_or_provider_without_truncation(self):
        f=evidence_fixtures.EvidenceTests();f.setUp();self.addCleanup(f.tearDown)
        config=copy.deepcopy(get_config());config['reliability']['input_limits']['models']={}
        config['reliability']['input_limits']['default']['max_pdf_characters']=1
        with patch('underwriting.reliability.inputs.get_config',return_value=config),patch.object(f.store,'claim',wraps=f.store.claim) as claim:
            with self.assertRaisesRegex(InputSizeError,'No evidence was truncated'):f.run_action()
            claim.assert_not_called()
        f.request.assert_not_called()
        config['reliability']['input_limits']['default']['max_request_utf8_bytes']=4
        with patch('underwriting.reliability.inputs.get_config',return_value=config):
            with self.assertRaises(InputSizeError):check_request('', '£££', 'test-model')

    def test_recorded_contracts_are_separate_from_expert_approval(self):
        from scripts.evaluate_offline import evaluate
        with patch('requests.post',side_effect=AssertionError('No network')):
            result=evaluate()
        self.assertTrue(all(row['software_contract_passed'] for row in result['software_contracts']))
        self.assertEqual(result['expert_judgement']['reviewed_cases'],0)
        self.assertIsNone(result['expert_judgement']['accuracy'])

    def test_numerical_sources_and_configuration_match_preserved_v1_commit(self):
        root=Path(__file__).resolve().parents[1]
        for name in ('underwriting/models/er.py','underwriting/models/brb.py','underwriting/models/er_adapter.py','underwriting/models/brb_adapter.py','config/decision_model.yaml'):
            baseline=subprocess.check_output(['git','show','c9caf217bf323bd18c58e42d08f840fce55c9314:'+name],cwd=root)
            self.assertEqual((root/name).read_bytes().replace(b'\r\n',b'\n'),baseline.replace(b'\r\n',b'\n'),name)


if __name__=='__main__':unittest.main()
