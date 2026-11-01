"""Append-only BRB assessments using the existing owner-gated Turso transport."""
import json

from underwriting.persistence.er import ERStore
from underwriting.configuration.access import require_owner


class BRBStore(ERStore):
    def initialise_brb(self):
        require_owner(self.settings)
        self.execute("""CREATE TABLE IF NOT EXISTS brb_assessment_runs_v1 (
            run_id TEXT PRIMARY KEY, case_id TEXT, source_version TEXT, source_hash TEXT,
            er_run_id TEXT, er_hash TEXT, rule_hash TEXT, engine_version TEXT, finished_at TEXT, payload TEXT)""")

    def save_brb(self, run):
        require_owner(self.settings)
        self.execute("INSERT INTO brb_assessment_runs_v1 VALUES (?,?,?,?,?,?,?,?,?,?)", [
            run["run_id"], run["case_id"], run["source_version"], run["source_hash"], run["er_run_id"],
            run["er_result_hash"], run["rule_configuration"]["sha256"], run["engine_version"], run["finished_at"],
            json.dumps(run, ensure_ascii=False, allow_nan=False)])

    def latest_brb(self, case, digest, er_run_id, er_hash, rule_hash, engine_version):
        rows = self.execute("SELECT payload FROM brb_assessment_runs_v1 WHERE case_id=? AND source_version=? AND source_hash=? AND er_run_id=? AND er_hash=? AND rule_hash=? AND engine_version=? ORDER BY finished_at DESC,rowid DESC LIMIT 1",
            [case["case_id"], case["submission_version"], digest, er_run_id, er_hash, rule_hash, engine_version], missing_ok=True)["rows"]
        return json.loads(rows[0][0]["value"]) if rows else None
