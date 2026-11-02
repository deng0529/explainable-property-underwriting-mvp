"""Read-only fallback queries for diagram freshness; no schema creation."""
import json
from underwriting.persistence.brb import BRBStore


class OverviewStore(BRBStore):
    def latest_overview_stage(self, case, stage):
        tables = {'beliefs': 'attribute_belief_runs_v1', 'er': 'er_aggregation_runs_v1',
                  'brb': 'brb_assessment_runs_v1'}
        table = tables[stage]  # Fixed allowlist, never an external SQL identifier.
        status = " AND status='completed'" if stage == 'beliefs' else ''
        rows = self.execute(f'SELECT payload FROM {table} WHERE case_id=? AND source_version=?{status} ORDER BY finished_at DESC,rowid DESC LIMIT 1',
                            [case['case_id'], case['submission_version']], missing_ok=True)['rows']
        return json.loads(rows[0][0]['value']) if rows else None
