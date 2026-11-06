"""Append-only proposal sets and human belief events on the existing repository."""
import json

from underwriting.configuration.access import require_owner
from underwriting.persistence.evidence import EvidenceStore


class BeliefStore(EvidenceStore):
    def initialise_beliefs(self):
        require_owner(self.settings)
        self.execute("CREATE TABLE IF NOT EXISTS attribute_belief_runs_v1 (run_id TEXT PRIMARY KEY, case_id TEXT, source_version TEXT, source_hash TEXT, reviewed_hash TEXT, finished_at TEXT, status TEXT, payload TEXT)")
        self.execute("CREATE TABLE IF NOT EXISTS attribute_belief_reviews_v1 (event_id TEXT PRIMARY KEY, run_id TEXT, attribute_id TEXT, created_at TEXT, payload TEXT)")
        self.execute("CREATE TABLE IF NOT EXISTS submission_intake_claims_v1 (generation_key TEXT PRIMARY KEY, run_id TEXT NOT NULL)")

    def latest_beliefs(self, case, digest, reviewed_hash, successful=True):
        sql = "SELECT payload FROM attribute_belief_runs_v1 WHERE case_id=? AND source_version=? AND source_hash=? AND reviewed_hash=?"
        if successful:
            sql += " AND status='completed'"
        sql += " ORDER BY finished_at DESC, rowid DESC LIMIT 1"
        rows = self.execute(sql, [case["case_id"], case["submission_version"], digest, reviewed_hash], missing_ok=True)["rows"]
        return json.loads(rows[0][0]["value"]) if rows else None

    def save_beliefs(self, run):
        require_owner(self.settings)
        self.execute("INSERT OR IGNORE INTO attribute_belief_runs_v1 VALUES (?,?,?,?,?,?,?,?)",
                     [run["run_id"], run["case_id"], run["source_version"], run["source_hash"], run["reviewed_hash"], run["finished_at"], run["status"], json.dumps(run, ensure_ascii=False, allow_nan=False)])

    def belief_reviews(self, run_id):
        rows = self.execute("SELECT payload FROM attribute_belief_reviews_v1 WHERE run_id=? ORDER BY created_at,rowid", [run_id], missing_ok=True)["rows"]
        return [json.loads(row[0]["value"]) for row in rows]

    def save_belief_review(self, event):
        require_owner(self.settings)
        self.execute("INSERT INTO attribute_belief_reviews_v1 VALUES (?,?,?,?,?)", [event["event_id"], event["run_id"], event["attribute_id"], event["created_at"], json.dumps(event, ensure_ascii=False, allow_nan=False)])

    def initialise_scores(self):
        require_owner(self.settings)
        self.execute("CREATE TABLE IF NOT EXISTS attribute_concern_score_runs_v2 (run_id TEXT PRIMARY KEY, case_id TEXT, source_version TEXT, source_hash TEXT, reviewed_hash TEXT, cache_key TEXT, finished_at TEXT, status TEXT, payload TEXT)")
        self.execute("CREATE TABLE IF NOT EXISTS submission_intake_claims_v1 (generation_key TEXT PRIMARY KEY, run_id TEXT NOT NULL)")

    def latest_scores(self,case,digest,reviewed_hash,cache_key=None,successful=True):
        sql="SELECT payload FROM attribute_concern_score_runs_v2 WHERE case_id=? AND source_version=? AND source_hash=? AND reviewed_hash=?"
        args=[case["case_id"],case["submission_version"],digest,reviewed_hash]
        if cache_key is not None:
            sql+=" AND cache_key=?";args.append(cache_key)
        if successful:sql+=" AND status='completed'"
        rows=self.execute(sql+" ORDER BY finished_at DESC,rowid DESC LIMIT 1",args,missing_ok=True)["rows"]
        return json.loads(rows[0][0]["value"]) if rows else None

    def save_scores(self,run):
        require_owner(self.settings)
        self.execute("INSERT OR IGNORE INTO attribute_concern_score_runs_v2 VALUES (?,?,?,?,?,?,?,?,?)",
            [run["run_id"],run["case_id"],run["source_version"],run["source_hash"],run["reviewed_hash"],run["cache_key"],run["finished_at"],run["status"],json.dumps(run,ensure_ascii=False,allow_nan=False)])
