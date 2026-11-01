"""Namespaced evidence/source/review tables using the existing Turso transport."""
import json

from underwriting.configuration.access import require_owner
from underwriting.persistence.intake import TursoStore


class EvidenceStore(TursoStore):
    def initialise_evidence(self):
        require_owner(self.settings)
        self.execute("CREATE TABLE IF NOT EXISTS attribute_evidence_runs_v1 (run_id TEXT PRIMARY KEY, case_id TEXT, source_version TEXT, source_hash TEXT, finished_at TEXT, status TEXT, payload TEXT)")
        self.execute("CREATE TABLE IF NOT EXISTS attribute_source_text_v1 (source_hash TEXT, extraction_version TEXT, payload TEXT, PRIMARY KEY(source_hash,extraction_version))")
        self.execute("CREATE TABLE IF NOT EXISTS attribute_evidence_reviews_v1 (event_id TEXT PRIMARY KEY, run_id TEXT, attribute_id TEXT, created_at TEXT, payload TEXT)")
        # Reuse the existing idempotency-claim table, with evidence-specific generation keys.
        self.execute("CREATE TABLE IF NOT EXISTS submission_intake_claims_v1 (generation_key TEXT PRIMARY KEY, run_id TEXT NOT NULL)")

    def latest_evidence(self, case, digest, successful=True):
        sql = "SELECT payload FROM attribute_evidence_runs_v1 WHERE case_id=? AND source_version=? AND source_hash=?"
        if successful:
            sql += " AND status='completed'"
        sql += " ORDER BY finished_at DESC, rowid DESC LIMIT 1"
        rows = self.execute(sql, [case["case_id"], case["submission_version"], digest], missing_ok=True)["rows"]
        return json.loads(rows[0][0]["value"]) if rows else None

    def save_evidence(self, envelope):
        require_owner(self.settings)
        # One atomic INSERT holds all five attributes and their original structured response.
        self.execute("INSERT OR IGNORE INTO attribute_evidence_runs_v1 VALUES (?,?,?,?,?,?,?)",
                     [envelope["run_id"], envelope["case_id"], envelope["source_version"], envelope["source_hash"], envelope["finished_at"], envelope["status"], json.dumps(envelope, ensure_ascii=False)])

    def source(self, digest, version):
        rows = self.execute("SELECT payload FROM attribute_source_text_v1 WHERE source_hash=? AND extraction_version=?", [digest, version], missing_ok=True)["rows"]
        return json.loads(rows[0][0]["value"]) if rows else None

    def save_source(self, source):
        require_owner(self.settings)
        self.execute("INSERT OR IGNORE INTO attribute_source_text_v1 VALUES (?,?,?)", [source["source_hash"], source["extraction_version"], json.dumps(source, ensure_ascii=False)])

    def reviews(self, run_id):
        rows = self.execute("SELECT payload FROM attribute_evidence_reviews_v1 WHERE run_id=? ORDER BY created_at,rowid", [run_id], missing_ok=True)["rows"]
        return [json.loads(row[0]["value"]) for row in rows]

    def save_review(self, event):
        require_owner(self.settings)
        self.execute("INSERT INTO attribute_evidence_reviews_v1 VALUES (?,?,?,?,?)", [event["event_id"], event["run_id"], event["attribute_id"], event["created_at"], json.dumps(event, ensure_ascii=False)])
