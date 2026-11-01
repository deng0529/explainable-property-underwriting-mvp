"""Append-only human submission reviews on the shared Turso transport."""
import json
from underwriting.configuration.access import require_owner
from underwriting.persistence.intake import TursoStore


class SubmissionReviewStore(TursoStore):
    def save_review(self, record):
        require_owner(self.settings)
        self.execute("""CREATE TABLE IF NOT EXISTS submission_human_reviews_v1 (
            event_id TEXT PRIMARY KEY, case_id TEXT NOT NULL, source_hash TEXT NOT NULL,
            intake_run_id TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL)""")
        self.execute("INSERT INTO submission_human_reviews_v1 VALUES (?,?,?,?,?,?)",
                     [record["event_id"], record["case_id"], record["source_hash"], record["intake_run_id"],
                      record["created_at"], json.dumps(record, ensure_ascii=False, allow_nan=False)])

    def review_history(self, case, run):
        rows = self.execute("SELECT payload FROM submission_human_reviews_v1 WHERE case_id=? AND source_hash=? AND intake_run_id=? ORDER BY created_at DESC,rowid DESC",
                            [case["case_id"], run["source_hash"], run["run_id"]], missing_ok=True)["rows"]
        return [json.loads(row[0]["value"]) for row in rows]
