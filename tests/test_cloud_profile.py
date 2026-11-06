"""Cloud release checks use mocked services/local SQLite; no real credentials."""
import os
import unittest
from dataclasses import replace
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
from underwriting.configuration.settings import get_config, ConfigError
from underwriting.configuration.access import Settings, require_owner
from underwriting.paths import PROJECT_ROOT
from underwriting.decision.service import load_generate, export_report
import test_decision as decision_fixtures


class CloudProfileTests(unittest.TestCase):
    def tearDown(self):
        get_config.cache_clear()

    def test_profile_is_readonly_and_does_not_change_local_yaml_or_cache(self):
        path = PROJECT_ROOT / 'config/settings.yaml'
        original = path.read_bytes()
        with patch.dict(os.environ, {'UNDERWRITING_PROFILE': ''}):
            local = get_config()
            mode = local['access']['mode']
            with patch.dict(os.environ, {'UNDERWRITING_PROFILE': 'public_readonly'}):
                cloud = get_config()
                self.assertEqual(cloud['access']['mode'], 'public_readonly')
                self.assertEqual(cloud['intake'], local['intake'])
                self.assertEqual(cloud['screening'], local['screening'])
            self.assertEqual(get_config()['access']['mode'], mode)
        self.assertEqual(path.read_bytes(), original)

    def test_invalid_profile_fails_visibly_and_readonly_cannot_be_elevated(self):
        with patch.dict(os.environ, {'UNDERWRITING_PROFILE': 'invalid'}):
            with self.assertRaisesRegex(ConfigError, 'UNDERWRITING_PROFILE'):
                get_config()
        with patch.dict(os.environ, {'UNDERWRITING_PROFILE': 'public_readonly'}), \
             patch('underwriting.configuration.access.local_server', return_value=True):
            with self.assertRaises(PermissionError):
                require_owner(Settings(access_mode='local_owner'))

    def test_cloud_entrypoint_has_no_startup_requests_and_readonly_controls(self):
        with patch.dict(os.environ, {'UNDERWRITING_PROFILE': ''}), \
             patch('underwriting.configuration.access.load_dotenv'), \
             patch('underwriting.persistence.intake.TursoStore.execute', side_effect=AssertionError('No startup database queries')) as query, \
             patch('underwriting.intake.service.OpenAI', side_effect=AssertionError('No GPT calls')) as gpt:
            app = AppTest.from_file(str(PROJECT_ROOT / 'cloud_app.py')).run(timeout=30)
            self.assertFalse(app.exception)
            self.assertEqual(get_config()['access']['mode'], 'public_readonly')
            labels = {button.label for button in app.button}
            self.assertIn('Load saved submission', labels)
            self.assertIn('Load saved decision review', labels)
            self.assertNotIn('Extract information and screen', labels)
            gpt.assert_not_called()
            query.assert_not_called()


class CloudSavedResultsTests(unittest.TestCase):
    def setUp(self):
        self.fixture = decision_fixtures.DecisionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        configured=patch("underwriting.assessment.er_service.get_config",return_value=get_config())
        configured.start();self.addCleanup(configured.stop)

    def test_cloud_reuses_saved_review_and_pdf_without_key_gpt_or_writes(self):
        review, _ = self.fixture.generate()
        settings = replace(self.fixture.settings, api_key='', access_mode='public_readonly')
        original_execute = self.fixture.store.execute
        def select_only(sql, args=(), missing_ok=False):
            self.assertTrue(sql.startswith('SELECT '), sql)
            return original_execute(sql, args, missing_ok)
        with patch.dict(os.environ, {'UNDERWRITING_PROFILE': 'public_readonly'}), \
             patch.object(self.fixture.store, 'execute', side_effect=select_only), \
             patch('underwriting.decision.service.structured_request', side_effect=AssertionError('No GPT')):
            loaded, origin = load_generate(settings, self.fixture.case, self.fixture.store)
            self.assertEqual(loaded['review_run_id'], review['review_run_id'])
            self.assertIn('Turso database', origin)
            self.assertTrue(export_report(settings, self.fixture.case, self.fixture.store, loaded['review_run_id']).startswith(b'%PDF'))

    def test_missing_saved_review_fails_closed_without_paid_call(self):
        settings = replace(self.fixture.settings, api_key='', access_mode='public_readonly')
        with patch.dict(os.environ, {'UNDERWRITING_PROFILE': 'public_readonly'}), \
             patch('underwriting.decision.service.structured_request', side_effect=AssertionError('No GPT')):
            with self.assertRaises(PermissionError):
                load_generate(settings, self.fixture.case, self.fixture.store)


if __name__ == '__main__':
    unittest.main()
