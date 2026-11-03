"""Existing-database mode: all calls mocked, no real credentials or mutations."""
import copy
import os
import unittest
from dataclasses import replace
from unittest.mock import patch,Mock
from underwriting.configuration.settings import get_config
from underwriting.configuration.access import Settings,load_settings,require_owner,require_test_services
from underwriting.persistence.decision import DecisionStore
from underwriting.intake.service import structured_request
from underwriting.assessment.belief_service import content_hash,current_input,reviewed_set
from underwriting.assessment.er_service import input_belief_hash,compatible_input_hashes
import test_beliefs as fixtures

class ReadonlyTests(unittest.TestCase):
    def test_explicit_file_uses_database_only_and_does_not_copy_or_export_secrets(self):
        cfg=copy.deepcopy(get_config());cfg['services'].update(mode='turso_readonly',credential_env_path='explicit.env')
        with patch.dict(os.environ,{},clear=True),patch('underwriting.configuration.access.get_config',return_value=cfg),patch('underwriting.configuration.access.load_dotenv'),patch('underwriting.configuration.access.dotenv_values',return_value={'TURSO_DATABASE_URL':'https://test.invalid','TURSO_AUTH_TOKEN':'mock-db-token','OPENAI_API_KEY':'must-not-use'}) as values:
            settings=load_settings()
        values.assert_called_once_with('explicit.env')
        self.assertEqual(settings.api_key,'');self.assertEqual(settings.database_token,'mock-db-token')
        self.assertEqual(settings.access_mode,'public_readonly')
        self.assertNotIn('mock-db-token',repr(settings))
        with self.assertRaises(PermissionError):require_owner(replace(settings,access_mode='local_owner'))
        with self.assertRaises(PermissionError):require_test_services(settings,generation=True)

    def test_cloud_secrets_work_without_local_path_or_gpt_credential(self):
        cfg=copy.deepcopy(get_config());cfg['services']['credential_env_path']='C:/not-on-cloud/.env'
        with patch('underwriting.configuration.access.get_config',return_value=cfg),patch('underwriting.configuration.access.load_dotenv'),patch.dict(os.environ,{'UNDERWRITING_PROFILE':'public_readonly'},clear=True),patch('underwriting.configuration.access.dotenv_values',side_effect=AssertionError('Cloud must not open local credential path')):
            settings=load_settings({'TURSO_DATABASE_URL':'https://cloud-test.invalid','TURSO_AUTH_TOKEN':'mock-cloud-token','OPENAI_API_KEY':'must-not-use'})
            self.assertEqual(settings.database_url,'https://cloud-test.invalid')
            self.assertEqual(settings.database_token,'mock-cloud-token')
            self.assertEqual(settings.api_key,'');self.assertEqual(settings.missing(),[])
            missing=load_settings({})
            self.assertEqual(missing.missing(),['TURSO_DATABASE_URL','TURSO_AUTH_TOKEN'])
        with self.assertRaises(PermissionError):require_owner(settings)
        with self.assertRaises(PermissionError):require_test_services(settings,generation=True)

    def test_readonly_yaml_does_not_require_windows_file_on_cloud(self):
        from underwriting.configuration.settings import validate
        cfg=copy.deepcopy(get_config());cfg['services']['credential_env_path']='C:/not-on-cloud/.env'
        validate(cfg)
        del cfg['services']['credential_env_path'];validate(cfg)

    def test_select_only_transport_blocks_writes_batches_and_gpt_before_network(self):
        settings=Settings(database_url='https://test.invalid',database_token='mock',service_mode='turso_readonly',access_mode='local_owner')
        store=DecisionStore(settings)
        response=Mock();response.json.return_value={'results':[{'type':'ok','response':{'result':{'rows':[]}}}]}
        with patch('requests.post',return_value=response) as post,patch('underwriting.intake.service.OpenAI',side_effect=AssertionError('No GPT')) as client:
            store.execute('SELECT payload FROM submission_intake_runs_v1 WHERE case_id=?',['mock-case'])
            self.assertEqual(post.call_count,1)
            for sql in ('CREATE TABLE x(a)','INSERT INTO x VALUES (1)','DELETE FROM x','UPDATE x SET a=1','PRAGMA query_only=OFF','SELECT 1; DELETE FROM x','WITH x AS (SELECT 1) DELETE FROM x'):
                with self.assertRaises(PermissionError):store.execute(sql)
            with self.assertRaises(PermissionError):store.transaction([('SELECT 1',[])])
            with self.assertRaises(PermissionError):structured_request(settings,'','','','')
            self.assertEqual(post.call_count,1);client.assert_not_called()

    def test_legacy_reviewed_evidence_is_readable_without_rewriting(self):
        fixture=fixtures.BeliefTests();fixture.setUp();self.addCleanup(fixture.tearDown)
        reviewed,modern,_=reviewed_set(fixture.evidence,fixture.store.reviews(fixture.evidence['run_id']))
        legacy=content_hash(reviewed)
        with patch.object(fixture.store,'latest_beliefs',side_effect=lambda case,digest,key: {'run_id':'legacy'} if key==legacy else None):
            self.assertEqual(current_input(fixture.case,fixture.store)[3],legacy)
        self.assertNotEqual(legacy,modern)

    def test_er_legacy_identity_requires_matching_record_content(self):
        run={'source_hash':'source','reviewed_hash':'review','belief_run_id':'old-run','confirmed_beliefs':{'F1':{'low':.2,'high':.3,'unknown':.5}}}
        run['input_belief_hash']=content_hash({'source_hash':run['source_hash'],'reviewed_hash':run['reviewed_hash'],'belief_run_id':run['belief_run_id'],'confirmed_beliefs':run['confirmed_beliefs']})
        current=input_belief_hash('source','review','old-run',run['confirmed_beliefs'])
        self.assertIn(current,compatible_input_hashes(run))
        changed=copy.deepcopy(run);changed['confirmed_beliefs']['F1']['low']=.4
        self.assertEqual(compatible_input_hashes(changed),set())

if __name__=='__main__':unittest.main()
