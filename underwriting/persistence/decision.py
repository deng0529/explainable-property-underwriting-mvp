"""Decision-only tables; existing upstream tables stay authoritative and unchanged."""
import json
import base64
import requests
from underwriting.persistence.brb import BRBStore
from underwriting.persistence.submission_reviews import SubmissionReviewStore
from underwriting.persistence.intake import StoreError
from underwriting.configuration.access import require_owner

REVIEW_DDL = '''CREATE TABLE IF NOT EXISTS decision_review_runs (
 review_run_id TEXT PRIMARY KEY, company_id TEXT NOT NULL, submission_id TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('running','succeeded','failed')),
 input_snapshot_json TEXT NOT NULL, input_snapshot_hash TEXT NOT NULL,
 upstream_record_references_json TEXT NOT NULL, prompt_version TEXT NOT NULL, prompt_hash TEXT NOT NULL,
 model_identifier TEXT NOT NULL, generation_settings_json TEXT NOT NULL,
 review_json TEXT, raw_response_json TEXT, token_usage_json TEXT,
 validation_errors_json TEXT NOT NULL, error_message TEXT NOT NULL, created_at TEXT NOT NULL, completed_at TEXT,
 payload TEXT NOT NULL)'''
DECISION_DDL = '''CREATE TABLE IF NOT EXISTS underwriting_decisions (
 decision_id TEXT PRIMARY KEY, review_run_id TEXT NOT NULL REFERENCES decision_review_runs(review_run_id),
 selected_action TEXT NOT NULL, underwriter_notes TEXT NOT NULL, reviewer_label TEXT NOT NULL,
 secondary_actions_json TEXT NOT NULL, created_at TEXT NOT NULL,
 supersedes_decision_id TEXT REFERENCES underwriting_decisions(decision_id), payload TEXT NOT NULL)'''

def encoded(value):
    if isinstance(value,bytes):
        return {'type':'blob','base64':base64.b64encode(value).decode('ascii')}
    return {'type': 'null'} if value is None else {'type': 'text', 'value': str(value)}

def dumps(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)

class DecisionStore(BRBStore, SubmissionReviewStore):
    def transaction(self, statements):
        """One Hrana batch/connection, FK enforcement and rollback on any failed step."""
        require_owner(self.settings)
        if self.settings.service_mode == 'offline':
            from underwriting.persistence.local import transaction
            return transaction(self.settings, statements)
        from underwriting.configuration.access import require_test_services
        require_test_services(self.settings)
        commands = [('PRAGMA foreign_keys=ON', []), ('BEGIN IMMEDIATE', []), *statements, ('COMMIT', [])]
        steps = []
        for index, (sql, args) in enumerate(commands):
            step = {'stmt': {'sql': sql, 'args': [encoded(value) for value in args]}}
            if index:
                step['condition'] = {'type': 'ok', 'step': index - 1}
            steps.append(step)
        steps.append({'condition': {'type': 'not', 'cond': {'type': 'ok', 'step': len(commands) - 1}}, 'stmt': {'sql': 'ROLLBACK'}})
        try:
            response = requests.post(self.endpoint, headers={'Authorization': 'Bearer ' + self.settings.database_token},
                json={'requests': [{'type': 'batch', 'batch': {'steps': steps}}, {'type': 'close'}]}, timeout=(10,30))
            response.raise_for_status()
            item = response.json()['results'][0]
            if item['type'] != 'ok':
                raise StoreError('Turso rejected the decision transaction; no GPT retry is needed.')
            batch = item['response']['result']
            if len(batch['step_errors']) != len(steps) or len(batch['step_results']) != len(steps) or any(batch['step_errors'][:len(commands)]) or any(result is None for result in batch['step_results'][:len(commands)]):
                raise StoreError('Decision transaction failed and was rolled back. Retry saving; do not repeat GPT.')
            return batch['step_results'][2:len(commands)-1]
        except (requests.RequestException, ValueError, KeyError, IndexError):
            raise StoreError('Turso decision transaction unavailable. Retry saving; do not repeat GPT.') from None

    def initialise_decision(self):
        self.transaction([(REVIEW_DDL, []), (DECISION_DDL, []),
            ('CREATE TABLE IF NOT EXISTS submission_intake_claims_v1 (generation_key TEXT PRIMARY KEY, run_id TEXT NOT NULL)', [])])

    def latest_review(self, case, digest, successful=True):
        rows = self.execute('SELECT payload FROM decision_review_runs WHERE company_id=? AND submission_id=? AND input_snapshot_hash=?' +
            (" AND status='succeeded'" if successful else '') + ' ORDER BY created_at DESC,rowid DESC LIMIT 1',
            [case['case_id'], case['submission_version'], digest], missing_ok=True)['rows']
        return json.loads(rows[0][0]['value']) if rows else None

    def reviews_for_snapshot(self, case, digest):
        rows=self.execute("SELECT payload FROM decision_review_runs WHERE company_id=? AND submission_id=? AND input_snapshot_hash=? AND status='succeeded' ORDER BY created_at DESC,rowid DESC",
                          [case['case_id'],case['submission_version'],digest],missing_ok=True)['rows']
        return [json.loads(row[0]['value']) for row in rows]

    def review_runs_for_submission(self, case, successful=True):
        sql="SELECT payload FROM decision_review_runs WHERE company_id=? AND submission_id=?"
        if successful:
            sql+=" AND status='succeeded'"
        rows=self.execute(sql+' ORDER BY created_at DESC,rowid DESC',[case['case_id'],case['submission_version']],missing_ok=True)['rows']
        return [json.loads(row[0]['value']) for row in rows]

    def latest_review_for_submission(self, case):
        rows = self.execute("SELECT payload FROM decision_review_runs WHERE company_id=? AND submission_id=? AND status='succeeded' ORDER BY created_at DESC,rowid DESC LIMIT 1",
                            [case['case_id'],case['submission_version']],missing_ok=True)['rows']
        return json.loads(rows[0][0]['value']) if rows else None

    def review_by_id(self, identifier):
        rows = self.execute('SELECT payload FROM decision_review_runs WHERE review_run_id=?', [identifier], missing_ok=True)['rows']
        return json.loads(rows[0][0]['value']) if rows else None

    def save_decision_review(self, run):
        require_owner(self.settings)
        values = [run['review_run_id'], run['company_id'], run['submission_id'], run['status'], dumps(run['input_snapshot']), run['input_snapshot_hash'],
            dumps(run['upstream_record_references']), run['prompt']['version'], run['prompt']['sha256'], run['model_identifier'], dumps(run['generation_settings']),
            dumps(run['review']), dumps(run['raw_response']), dumps(run['token_usage']), dumps(run['validation_errors']), run['error_message'], run['created_at'], run['completed_at'], dumps(run)]
        # A running audit may finalise once. Successful/failed history is immutable.
        return self.transaction([('INSERT OR IGNORE INTO decision_review_runs VALUES (' + ','.join('?' for _ in values) + ')', values),
            ('''UPDATE decision_review_runs SET status=?, review_json=?, raw_response_json=?, token_usage_json=?,
                validation_errors_json=?,error_message=?,completed_at=?,model_identifier=?,payload=? WHERE review_run_id=? AND status='running' ''',
             [run['status'], dumps(run['review']), dumps(run['raw_response']), dumps(run['token_usage']), dumps(run['validation_errors']), run['error_message'], run['completed_at'], run['model_identifier'], dumps(run), run['review_run_id']])])

    def decision_history(self, review_id):
        rows = self.execute('SELECT payload FROM underwriting_decisions WHERE review_run_id=? ORDER BY created_at DESC,rowid DESC', [review_id], missing_ok=True)['rows']
        return [json.loads(row[0]['value']) for row in rows]

    def save_human_decision(self, record, expected):
        values = [record['decision_id'], record['review_run_id'], record['selected_action'], record['underwriter_notes'], record['reviewer_label'],
                  dumps(record['secondary_actions']), record['created_at'], record['supersedes_decision_id'], dumps(record), record['review_run_id'], expected or '']
        result = self.transaction([('''INSERT INTO underwriting_decisions SELECT ?,?,?,?,?,?,?,?,?
            WHERE COALESCE((SELECT decision_id FROM underwriting_decisions WHERE review_run_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1),'')=?''', values)])
        if not result[0]['affected_row_count']:
            raise ValueError('Decision history changed. Reload before saving a new decision version.')


    def initialise_reports(self):
        self.transaction([("""CREATE TABLE IF NOT EXISTS decision_reports (
            report_id TEXT PRIMARY KEY, artifact_key TEXT NOT NULL UNIQUE,
            review_run_id TEXT NOT NULL REFERENCES decision_review_runs(review_run_id),
            snapshot_hash TEXT NOT NULL, template_version TEXT NOT NULL,
            human_decision_id TEXT REFERENCES underwriting_decisions(decision_id),
            report_hash TEXT NOT NULL, created_at TEXT NOT NULL, pdf_blob BLOB NOT NULL)""",[])])

    def saved_report(self, artifact_key):
        rows=self.execute("SELECT report_id,review_run_id,snapshot_hash,template_version,human_decision_id,report_hash,created_at,hex(pdf_blob) FROM decision_reports WHERE artifact_key=?",[artifact_key],missing_ok=True)['rows']
        if not rows:
            return None
        values=[cell.get('value') for cell in rows[0]]
        result=dict(zip(('report_id','review_run_id','snapshot_hash','template_version','human_decision_id','report_hash','created_at'),values[:7]))
        if result['human_decision_id'] in (None,'None',''):
            result['human_decision_id']=None
        result['pdf']=bytes.fromhex(values[7])
        return result

    def save_report(self, record):
        require_owner(self.settings)
        self.transaction([('INSERT OR IGNORE INTO decision_reports VALUES (?,?,?,?,?,?,?,?,?)',[
            record['report_id'],record['artifact_key'],record['review_run_id'],record['snapshot_hash'],record['template_version'],
            record['human_decision_id'],record['report_hash'],record['created_at'],record['pdf']])])
