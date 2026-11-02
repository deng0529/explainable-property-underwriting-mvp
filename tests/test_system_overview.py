"""Diagram geometry/data checks; SQLite/mocks only, no paid calls or live writes."""
import copy
import unittest
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
from underwriting.paths import PROJECT_ROOT
import test_decision as fixtures
from underwriting.configuration.decision_model import get_model
from underwriting.configuration.settings import get_config
from underwriting.assessment.overview import load_overview, empty_overview
from underwriting.persistence.overview import OverviewStore
from underwriting.persistence.intake import StoreError
from underwriting.ui.overview_diagrams import hierarchy_diagram, workflow_configuration
from underwriting.ui.workflow_svg import workflow_svg
from xml.etree import ElementTree


class OverviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.DecisionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.case, self.model = self.fixture.case, get_model()
        self.store = OverviewStore.__new__(OverviewStore)
        self.store.settings = self.fixture.settings
        self.sql = []
        def read_only(sql, args=(), missing_ok=False):
            self.assertTrue(sql.startswith('SELECT '), sql)
            self.sql.append(sql)
            return self.fixture.store.execute(sql, args, missing_ok)
        self.store.execute = read_only
        self.patcher = patch('underwriting.assessment.overview.document_hash', return_value='mock-source')
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_saved_results_read_only_fresh_session_no_calculation_or_gpt(self):
        changes = self.fixture.store.db.total_changes
        with patch('underwriting.intake.service.structured_request', side_effect=AssertionError('GPT forbidden')), \
             patch('underwriting.models.er.combine_er', side_effect=AssertionError('ER forbidden')), \
             patch('underwriting.models.brb.infer_brb', side_effect=AssertionError('BRB forbidden')):
            view = load_overview(self.case, self.store, self.model)
        self.assertEqual(view['attributes']['F1']['values'], self.fixture.er['confirmed_beliefs']['F1'])
        self.assertEqual(view['groups']['fire']['result'], self.fixture.er['groups']['fire'])
        self.assertEqual(view['overall']['result'], self.fixture.brb['result'])
        self.assertEqual(changes, self.fixture.store.db.total_changes)
        self.assertTrue(self.sql)

    def test_unconfirmed_proposals_not_used_as_confirmed_results(self):
        with patch.object(self.store, 'belief_reviews', return_value=[]):
            view = load_overview(self.case, self.store, self.model)
        self.assertEqual(view['attributes']['F1']['state'], 'AI proposal — unconfirmed')
        self.assertIsNotNone(view['attributes']['F1']['values'])
        self.assertIsNone(view['groups']['fire']['result'])
        self.assertEqual(view['groups']['fire']['state'], 'Stale')
        self.assertEqual(view['overall']['state'], 'Stale')

    def test_changed_source_and_company_do_not_mix_saved_records(self):
        with patch('underwriting.assessment.overview.document_hash', return_value='changed-source'), \
             patch('underwriting.assessment.belief_service.document_hash', return_value='changed-source'):
            view = load_overview(self.case, self.store, self.model)
        self.assertEqual(view['attributes']['F1']['state'], 'Stale')
        self.assertIsNone(view['overall']['result'])
        other = dict(self.case, case_id='OTHER-COMPANY')
        view = load_overview(other, self.store, self.model)
        self.assertEqual(view, empty_overview(self.model))

    def test_changed_model_withholds_stale_results(self):
        model = copy.deepcopy(self.model)
        model['er']['groups']['fire']['importance_weights']['F1'] = 2
        view = load_overview(self.case, self.store, model)
        self.assertEqual(view['groups']['fire']['state'], 'Stale')
        self.assertIsNone(view['overall']['result'])

    def test_configuration_driven_ranks_arrows_weights_and_safe_labels(self):
        view = load_overview(self.case, self.store, self.model)
        source = hierarchy_diagram(self.model, view).source
        self.assertEqual(source.count('rank=same'), 3)
        self.assertIn('"overall"' if '"overall"' in source else 'overall', source)
        self.assertIn('overall -> group_fire [dir=back]', source)
        self.assertIn('group_fire -> attribute_F1 [label="Contribution weight\n0.33" dir=back]', source)
        self.assertIn('group_security -> attribute_S1 [label="Contribution weight\n0.50" dir=back]', source)
        self.assertIn('Human confirmed', source)
        model = copy.deepcopy(self.model)
        model['attributes'].append({'id':'F4','label':'<script>"\\N injected','definition':'Example','enabled':True})
        model['er']['groups']['fire']['members'].append('F4')
        model['er']['groups']['fire']['importance_weights']['F4'] = 1
        source = hierarchy_diagram(model, empty_overview(model)).source
        self.assertIn('attribute_F4', source)
        self.assertIn('Contribution weight\n0.25', source)
        self.assertIn('\\\\N', source)
        self.assertIn('Not assessed', source)

    def test_workflow_branches_source_shared_er_and_storage(self):
        config = workflow_configuration()
        source = workflow_svg(config, self.model, get_config()['screening'])
        root = ElementTree.fromstring(source)
        ns = {'s': 'http://www.w3.org/2000/svg'}
        nodes = {g.attrib['data-node']: g for g in root.findall('s:g', ns) if 'data-node' in g.attrib}
        edges = {g.attrib['data-edge']: g for g in root.findall('s:g', ns) if 'data-edge' in g.attrib}
        self.assertEqual(set(nodes), set(config['nodes']))
        self.assertEqual(set(edges), {a + ':' + b for a,b,_ in config['edges']})
        for text in ('routing:evidence','routing:clarification','routing:stop','pdf:evidence',
                     'Original full PDF text','shared combine_er','Human belief confirmation'):
            self.assertIn(text, source)
        for identifier, edge in edges.items():
            path = edge.find('s:path',ns)
            self.assertEqual(path.attrib['marker-end'],'url(#arrow)')
            self.assertEqual(path.attrib['stroke-linejoin'],'round')
            source_id, target_id = identifier.split(':')
            points = path.attrib['d'][1:].split(' L')
            for node_id, point in ((source_id, points[0]), (target_id, points[-1])):
                x, y = map(float, point.split(','))
                rect = nodes[node_id].find('s:rect', ns)
                left, top, width, height = [float(rect.attrib[key]) for key in ('x','y','width','height')]
                self.assertTrue(left <= x <= left + width and top <= y <= top + height)
                self.assertTrue(x in (left, left + width) or y in (top, top + height))
        self.assertNotIn('clarification:evidence', source)
        modified = copy.deepcopy(config)
        modified['nodes']['pdf']['label'] = '<script>alert("unsafe")</script>'
        safe = workflow_svg(modified, self.model, get_config()['screening'])
        self.assertNotIn('<script>', safe)
        self.assertIn('&lt;script&gt;', safe)

    def test_ui_navigation_and_outage_fallback(self):
        with patch('underwriting.ui.overview.load_overview', return_value=empty_overview(self.model)) as load, \
             patch('underwriting.ui.overview.settings_for_ui', return_value=self.fixture.settings), \
             patch('underwriting.ui.overview.OverviewStore', return_value=self.store):
            app = AppTest.from_file(str(PROJECT_ROOT / 'app.py')).run(timeout=20)
            self.assertFalse(app.exception)
            load.assert_not_called()
            app.session_state['active_workspace'] = 'System overview'
            app.run(timeout=20)
            self.assertFalse(app.exception)
            load.assert_called_once()
            self.assertEqual(len(app.get('graphviz_chart')), 1)
            case_id = app.session_state['current_case_id']
            app.session_state[f'case:{case_id}:overview:section'] = 'MVP workflow'
            app.run(timeout=20)
            self.assertFalse(app.exception)
            self.assertEqual(load.call_count, 1)
            self.assertEqual(len(app.image), 1)
            app.session_state[f'case:{case_id}:overview:section'] = 'Assessment hierarchy'
            load.side_effect = StoreError('Turso unavailable')
            app.run(timeout=20)
            self.assertFalse(app.exception)
            self.assertIn('Turso unavailable', app.warning[0].value)
            self.assertIn('Unavailable', app.get('graphviz_chart')[0].proto.spec)


if __name__ == '__main__':
    unittest.main()
