"""SQLite-backed knowledge store, kept in its own file so it can be shared across repos."""
import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict, fields
from pathlib import Path

from ..config import settings
from . import KINDS, Fact, terms

SCHEMA = """
CREATE TABLE IF NOT EXISTS facts(
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, subject TEXT NOT NULL, statement TEXT NOT NULL,
    evidence TEXT, source_type TEXT NOT NULL, source TEXT NOT NULL, scope TEXT NOT NULL,
    repo TEXT, ref TEXT, "commit" TEXT, forbid TEXT NOT NULL, require TEXT NOT NULL,
    valid_from TEXT, valid_until TEXT, verified_by TEXT NOT NULL, verified_at REAL);
CREATE INDEX IF NOT EXISTS facts_repo ON facts(source_type, repo);
"""
LIST_FIELDS = ("scope", "forbid", "require")
COLUMNS = [f.name for f in fields(Fact)]


class LocalKnowledgeStore:
    def __init__(self, path: Path | None = None):
        self.path = path or settings().knowledge_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _validate(fact: Fact) -> None:
        if not fact.verified_by:
            raise ValueError(f"{fact.id}: only verified knowledge is stored (verified_by is empty)")
        if fact.kind not in KINDS:
            raise ValueError(f"{fact.id}: kind must be one of {KINDS}")
        if not fact.statement.strip():
            raise ValueError(f"{fact.id}: empty statement")

    def _insert(self, c, facts: list[Fact]) -> None:
        now = time.time()
        for f in facts:
            self._validate(f)
            row = asdict(f)
            row["verified_at"] = f.verified_at or now
            for key in LIST_FIELDS:
                row[key] = json.dumps(row[key])
            cols = ",".join(f'"{k}"' for k in COLUMNS)
            c.execute(f"INSERT OR REPLACE INTO facts({cols}) VALUES({','.join('?' * len(COLUMNS))})",
                      [row[k] for k in COLUMNS])

    def upsert(self, facts: list[Fact]) -> int:
        with self._connect() as c:
            self._insert(c, facts)
        return len(facts)

    def replace_code(self, repo: str, facts: list[Fact]) -> int:
        """Swap a repo's code facts atomically, so deleted code stops being 'truth'."""
        with self._connect() as c:
            c.execute("DELETE FROM facts WHERE source_type='code' AND repo=?", (repo,))
            self._insert(c, facts)
        return len(facts)

    def delete(self, fact_id: str) -> None:
        with self._connect() as c:
            c.execute("DELETE FROM facts WHERE id=?", (fact_id,))

    @staticmethod
    def _row(row) -> Fact:
        data = dict(row)
        for key in LIST_FIELDS:
            data[key] = json.loads(data[key])
        return Fact(**data)

    def get(self, fact_id: str) -> Fact | None:
        with self._connect() as c:
            row = c.execute("SELECT * FROM facts WHERE id=?", (fact_id,)).fetchone()
        return self._row(row) if row else None

    def facts(self, task_id=None, kinds=None) -> list[Fact]:
        with self._connect() as c:
            rows = c.execute("SELECT * FROM facts ORDER BY kind, id").fetchall()
        out = [self._row(r) for r in rows]
        return [f for f in out
                if (task_id is None or f.applies_to(task_id)) and (kinds is None or f.kind in kinds)
                and f.is_current()]

    def search(self, text, task_id=None, limit=20, kinds=None) -> list[tuple[int, Fact]]:
        """Rank facts by shared word stems with `text`. Small and local, so a scan is fine."""
        query = terms(text)
        if not query:
            return []
        scored = [(len(query & f.index_terms()), f) for f in self.facts(task_id, kinds)]
        scored = [(s, f) for s, f in scored if s > 0]
        scored.sort(key=lambda x: (-x[0], x[1].id))
        return scored[:limit]

    def stats(self) -> dict:
        with self._connect() as c:
            by_kind = {r["kind"]: r["n"] for r in c.execute("SELECT kind, COUNT(*) n FROM facts GROUP BY kind")}
            repos = [dict(r) for r in c.execute(
                "SELECT repo, ref, \"commit\", COUNT(*) n, MAX(verified_at) at FROM facts "
                "WHERE source_type='code' GROUP BY repo, ref, \"commit\"")]
        return {"total": sum(by_kind.values()), "by_kind": by_kind, "repos": repos}
