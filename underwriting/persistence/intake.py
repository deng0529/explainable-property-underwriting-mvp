"""Append-only intake runs over Turso HTTP. Writes require owner proof."""
import json
import re
from contextlib import contextmanager, nullcontext
from functools import wraps
from urllib.parse import urlsplit

import requests

from underwriting.configuration.access import require_owner

RUNS_DDL = """CREATE TABLE IF NOT EXISTS submission_intake_runs_v1 (
 run_id TEXT PRIMARY KEY, case_id TEXT NOT NULL, source_version TEXT NOT NULL,
 source_hash TEXT NOT NULL, finished_at TEXT NOT NULL, status TEXT NOT NULL,
 payload TEXT NOT NULL)"""
CLAIMS_DDL = """CREATE TABLE IF NOT EXISTS submission_intake_claims_v1 (
 generation_key TEXT PRIMARY KEY, run_id TEXT NOT NULL)"""


class StoreError(RuntimeError):
    pass


def reuse_read_connection(function):
    """Reuse transport within one action, never cache query results across checks."""
    @wraps(function)
    def wrapped(settings,case,store,*args,**kwargs):
        context=store.read_connection() if hasattr(store,'read_connection') else nullcontext()
        with context:
            return function(settings,case,store,*args,**kwargs)
    return wrapped


class TursoStore:
    def __init__(self, settings):
        self.settings = settings
        if settings.service_mode == 'offline':
            self.endpoint = None
            return
        from underwriting.configuration.access import require_test_services
        require_test_services(settings)
        address = settings.database_url.replace("libsql://", "https://", 1).replace("turso://", "https://", 1)
        url = urlsplit(address)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise StoreError("TURSO_DATABASE_URL must be a credential-free HTTPS/libSQL database URL.")
        self.endpoint = address.rstrip("/") + "/v2/pipeline"

    @contextmanager
    def read_connection(self):
        # One TLS connection for repeated saved-input checks. Queries still reach
        # Turso each time, so concurrent-change checks remain effective.
        if self.settings.service_mode!='turso_readonly' or getattr(self,'_read_session',None) is not None:
            yield self
            return
        with requests.Session() as session:
            self._read_session=session
            try:
                yield self
            finally:
                self._read_session=None

    def execute(self, sql, args=(), missing_ok=False):
        if self.settings.service_mode=='comparison_owner':
            require_owner(self.settings)
            allowed=(r'^\s*SELECT\s',
                     r'^CREATE TABLE IF NOT EXISTS (?:attribute_belief_runs_v1|attribute_belief_reviews_v1|attribute_concern_score_runs_v2|submission_intake_claims_v1) \(',
                     r'^INSERT OR IGNORE INTO (?:attribute_belief_runs_v1|attribute_concern_score_runs_v2|submission_intake_claims_v1) (?:VALUES|\()')
            if ';' in sql or not any(re.match(pattern,sql,re.IGNORECASE) for pattern in allowed):
                raise PermissionError('Comparison owner access permits proposal storage only; other workflow writes are blocked.')
        if self.settings.service_mode=='turso_readonly':
            if not re.match(r'^\s*SELECT\s',sql,re.IGNORECASE) or ';' in sql:
                raise PermissionError('Read-only Turso access permits one SELECT query only. No database write was made.')
        if self.settings.service_mode == 'offline':
            from underwriting.persistence.local import execute
            return execute(self.settings,sql,args,missing_ok)
        from underwriting.configuration.access import require_test_services
        require_test_services(self.settings)
        encoded = [{"type": "text", "value": str(value)} for value in args]
        try:
            post = self._read_session.post if getattr(self,"_read_session",None) is not None else requests.post
            response = post(self.endpoint,
                headers={"Authorization": "Bearer " + self.settings.database_token},
                json={"requests": [{"type": "execute", "stmt": {"sql": sql, "args": encoded}}, {"type": "close"}]},
                timeout=(10, 30))
            response.raise_for_status()
            item = response.json()["results"][0]
            if item["type"] != "ok":
                if missing_ok and "no such table" in item.get("error", {}).get("message", "").lower():
                    return {"rows": [], "affected_row_count": 0}
                raise StoreError("Turso rejected the intake query. Check database access and schema permissions.")
            return item["response"]["result"]
        except (requests.RequestException, ValueError, KeyError, IndexError) as error:
            raise StoreError("Turso is unavailable. Check TURSO_DATABASE_URL / TURSO_AUTH_TOKEN and connectivity; no GPT retry is needed for a save failure.") from None

    def initialise(self, proof):
        require_owner(self.settings, proof)
        self.execute(RUNS_DDL)
        self.execute(CLAIMS_DDL)

    def latest(self, case_id, source_version, source_hash=None, successful=True):
        sql = "SELECT payload FROM submission_intake_runs_v1 WHERE case_id=?"
        args = [case_id]
        if source_version is not None:
            sql += " AND source_version=?"
            args.append(source_version)
        if source_hash is not None:
            sql += " AND source_hash=?"
            args.append(source_hash)
        if successful:
            sql += " AND status IN ('completed','needs_review','extracted','extraction_completed')"
        sql += " ORDER BY finished_at DESC, run_id DESC LIMIT 1"
        rows = self.execute(sql, args, missing_ok=True)["rows"]
        return json.loads(rows[0][0]["value"]) if rows else None

    def candidates(self, case_id, source_hash):
        rows = self.execute("SELECT payload FROM submission_intake_runs_v1 WHERE case_id=? AND source_hash=? AND status IN ('completed','needs_review','extracted','extraction_completed') ORDER BY finished_at DESC, run_id DESC LIMIT 20",
                            [case_id, source_hash], missing_ok=True)["rows"]
        return [json.loads(row[0]["value"]) for row in rows]

    def claim(self, key, run_id, proof):
        require_owner(self.settings, proof)
        # A consumed generation is never deleted. Delayed/double clicks cannot purchase it twice.
        result = self.execute("INSERT OR IGNORE INTO submission_intake_claims_v1 VALUES (?,?)", [key, run_id])
        return result["affected_row_count"] == 1

    def save(self, envelope, proof):
        require_owner(self.settings, proof)
        # One INSERT is one atomic SQLite transaction; the entire completed result is in the row.
        self.execute("INSERT OR IGNORE INTO submission_intake_runs_v1 VALUES (?,?,?,?,?,?,?)",
                     [envelope["run_id"], envelope["case_id"], envelope["source_version"],
                      envelope["source_hash"], envelope["finished_at"], envelope["status"],
                      json.dumps(envelope, ensure_ascii=False, allow_nan=False)])
