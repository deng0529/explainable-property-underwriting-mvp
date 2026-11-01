"""Append-only ER result sets through the existing Turso transport."""
import json

from underwriting.persistence.beliefs import BeliefStore
from underwriting.configuration.access import require_owner
from underwriting.configuration.decision_model import effective_er_model_hash


class ERStore(BeliefStore):
    def initialise_er(self):
        require_owner(self.settings)
        self.execute("CREATE TABLE IF NOT EXISTS er_aggregation_runs_v1 (run_id TEXT PRIMARY KEY, case_id TEXT, source_version TEXT, source_hash TEXT, input_hash TEXT, model_hash TEXT, engine_version TEXT, finished_at TEXT, payload TEXT)")

    def save_er(self, run):
        require_owner(self.settings)
        self.execute("INSERT INTO er_aggregation_runs_v1 VALUES (?,?,?,?,?,?,?,?,?)", [run["run_id"], run["case_id"], run["source_version"], run["source_hash"], run["input_belief_hash"], run["decision_model"]["sha256"], run["engine_version"], run["finished_at"], json.dumps(run, ensure_ascii=False, allow_nan=False)])

    def latest_er(self, case, digest, inputs, model_hash, engine_version):
        rows = self.execute("SELECT payload FROM er_aggregation_runs_v1 WHERE case_id=? AND source_version=? AND source_hash=? AND input_hash=? AND model_hash=? AND engine_version=? ORDER BY finished_at DESC,rowid DESC LIMIT 1",
            [case["case_id"], case["submission_version"], digest, inputs, model_hash, engine_version], missing_ok=True)["rows"]
        return json.loads(rows[0][0]["value"]) if rows else None

    def latest_er_compatible(self, case, digest, inputs, er_model_hash, engine_version):
        rows = self.execute("SELECT payload FROM er_aggregation_runs_v1 WHERE case_id=? AND source_version=? AND source_hash=? AND input_hash=? AND engine_version=? ORDER BY finished_at DESC,rowid DESC",
                            [case["case_id"], case["submission_version"], digest, inputs, engine_version], missing_ok=True)["rows"]
        for row in rows:
            run = json.loads(row[0]["value"])
            if effective_er_model_hash(run["decision_model_snapshot"]) == er_model_hash:
                return run
        return None
