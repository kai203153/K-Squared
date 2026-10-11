"""SQLite storage with versioned migrations.

Vocabulary: a *record* is authorized context as written (an ADR, a requirement); a *fact* is
verified knowledge in the knowledge store (confirmed records and production code); a
*commitment* is a direction an engineer took in a session; a *finding* is an assumption
behind that direction which a fact contradicts or already covers.
"""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .config import settings

MIGRATIONS = [
    """
    CREATE TABLE tasks(
        id TEXT PRIMARY KEY, title TEXT NOT NULL, goal TEXT, source TEXT, imported_at REAL);
    CREATE TABLE records(
        id TEXT PRIMARY KEY, title TEXT, kind TEXT, status TEXT NOT NULL,
        applies_to TEXT NOT NULL, valid_from TEXT, valid_until TEXT, source TEXT,
        body TEXT NOT NULL, forbid TEXT NOT NULL, require TEXT NOT NULL, imported_at REAL);
    CREATE TABLE work(
        id TEXT PRIMARY KEY, task_id TEXT NOT NULL, description TEXT, hours REAL,
        dependency REAL, invalidation REAL);
    CREATE TABLE events(
        id TEXT PRIMARY KEY, task_id TEXT NOT NULL, session TEXT NOT NULL, kind TEXT NOT NULL,
        content TEXT NOT NULL, status TEXT NOT NULL, analysis TEXT, created REAL NOT NULL);
    CREATE INDEX events_session ON events(task_id, session, created);
    CREATE TABLE commitments(
        id TEXT PRIMARY KEY, task_id TEXT, session TEXT, statement TEXT, stage TEXT,
        source_event TEXT, detector TEXT, created REAL);
    CREATE TABLE findings(
        id TEXT PRIMARY KEY, task_id TEXT NOT NULL, session TEXT NOT NULL, commitment_id TEXT,
        record_id TEXT NOT NULL, severity TEXT NOT NULL, status TEXT NOT NULL,
        decision_quote TEXT, event_quote TEXT, explanation TEXT, detector TEXT,
        impact_min REAL, impact_max REAL, gate_shown_at REAL, created REAL, updated REAL);
    CREATE INDEX findings_open ON findings(task_id, session, status);
    CREATE TABLE unverified(
        id TEXT PRIMARY KEY, event_id TEXT, record_id TEXT, claim TEXT, reason TEXT, created REAL);
    CREATE TABLE responses(
        id TEXT PRIMARY KEY, finding_id TEXT NOT NULL, response TEXT NOT NULL, note TEXT,
        draft TEXT, created REAL);
    """,
    # v2: findings point at any verified fact (decision or production code), carry the
    # assumption behind them, and track when the engineer was nudged.
    """
    ALTER TABLE findings RENAME COLUMN record_id TO fact_id;
    ALTER TABLE findings RENAME COLUMN decision_quote TO fact_quote;
    ALTER TABLE findings ADD COLUMN relation TEXT NOT NULL DEFAULT 'contradicted';
    ALTER TABLE findings ADD COLUMN assumption TEXT;
    ALTER TABLE findings ADD COLUMN fact_kind TEXT;
    ALTER TABLE findings ADD COLUMN fact_source TEXT;
    ALTER TABLE findings ADD COLUMN delivered_at REAL;
    ALTER TABLE unverified RENAME COLUMN record_id TO fact_id;
    """,
]


def _path() -> Path:
    s = settings()
    s.home.mkdir(parents=True, exist_ok=True)
    return s.db_path


@contextmanager
def connect():
    """Open a connection that commits on success and always closes."""
    conn = sqlite3.connect(_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def migrate() -> int:
    with connect() as c:
        version = c.execute("PRAGMA user_version").fetchone()[0]
        for i, script in enumerate(MIGRATIONS[version:], start=version + 1):
            c.executescript(script)
            c.execute(f"PRAGMA user_version={i}")
        return len(MIGRATIONS)


def reset_activity() -> None:
    """Clear captured activity but keep imported context."""
    with connect() as c:
        for table in ("events", "commitments", "findings", "unverified", "responses"):
            c.execute(f"DELETE FROM {table}")
