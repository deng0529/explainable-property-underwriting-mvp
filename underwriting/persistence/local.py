"""Isolated local SQLite transport for V2 offline mode; no network fallback."""
import sqlite3
from contextlib import closing
from pathlib import Path
from underwriting.paths import PROJECT_ROOT
from underwriting.configuration.access import require_owner
from underwriting.persistence.intake import StoreError


def database_path(settings):
    path = (PROJECT_ROOT / settings.local_database_path).resolve()
    if not path.is_relative_to((PROJECT_ROOT / '.runtime').resolve()):
        raise StoreError('Offline database path must stay inside Version 2 .runtime/.')
    return path


def result(cursor):
    rows = []
    for row in cursor.fetchall():
        rows.append([{'type':'null'} if value is None else {'type':'text','value':str(value)} for value in row])
    return {'rows':rows,'affected_row_count':max(0,cursor.rowcount)}


def execute(settings, sql, args=(), missing_ok=False):
    path = database_path(settings)
    reading = sql.lstrip().upper().startswith('SELECT ')
    if not reading:
        require_owner(settings)
        path.parent.mkdir(parents=True,exist_ok=True)
    if reading and not path.exists():
        if missing_ok:
            return {'rows':[],'affected_row_count':0}
        raise StoreError('No offline saved database exists yet.')
    try:
        target = path.as_uri()+'?mode=ro' if reading else str(path)
        with closing(sqlite3.connect(target,uri=reading,timeout=5)) as db, db:
            db.execute('PRAGMA foreign_keys=ON')
            return result(db.execute(sql,args))
    except sqlite3.Error as error:
        if missing_ok and 'no such table' in str(error).lower():
            return {'rows':[],'affected_row_count':0}
        raise StoreError('Offline database operation failed. No external fallback was attempted; retry local storage access.') from None


def transaction(settings, statements):
    require_owner(settings)
    path = database_path(settings)
    path.parent.mkdir(parents=True,exist_ok=True)
    try:
        with closing(sqlite3.connect(path,timeout=5)) as db, db:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('BEGIN IMMEDIATE')
            return [result(db.execute(sql,args)) for sql,args in statements]
    except sqlite3.Error:
        raise StoreError('Offline transaction failed and was rolled back. Retry save without repeating generation.') from None
