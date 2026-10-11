"""The ground-truth knowledge base.

Only *verified* knowledge lives here: decisions someone confirmed, and facts read from
committed production code. Every assumption an engineer makes is compared against it.

`store()` returns the configured backend. Today that is a local SQLite file; the
`KnowledgeStore` protocol is the seam for a shared, global backend later.
"""
import datetime as dt
import hashlib
import re
from dataclasses import dataclass, field
from typing import Protocol

DECISION_KINDS = ("decision", "requirement", "convention")
CODE_KINDS = ("component", "dependency", "infra")
KINDS = DECISION_KINDS + CODE_KINDS

# Words too common in engineering prose to signal that two things are the same.
STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "now", "new", "add", "use",
    "using", "create", "build", "write", "make", "implement", "file", "files", "code", "class",
    "function", "module", "service", "data", "aws", "def", "src", "lib", "app", "main", "init",
    "test", "tests", "our", "your", "their", "all", "any", "not", "but", "can", "should",
    "will", "just", "then", "also", "some", "one", "existing", "standalone", "separate",
    # Boilerplate in generated fact statements.
    "defines", "exports", "provisions", "depends", "declared", "production", "const", "type",
}


def terms(text: str) -> set[str]:
    """Comparable word stems: camelCase/snake_case split, stopwords dropped, 5-char prefixes."""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    words = re.findall(r"[a-z][a-z0-9]+", text.lower().replace("_", " "))
    return {w[:5] for w in words if len(w) >= 3 and w not in STOPWORDS}


@dataclass
class Fact:
    id: str
    kind: str
    subject: str
    statement: str
    source: str
    verified_by: str
    evidence: str = ""
    source_type: str = "record"  # "record" | "code" | "manual"
    scope: list[str] = field(default_factory=lambda: ["*"])
    repo: str = ""
    ref: str = ""
    commit: str = ""
    forbid: list[str] = field(default_factory=list)
    require: list[str] = field(default_factory=list)
    valid_from: str | None = None
    valid_until: str | None = None
    verified_at: float = 0.0

    def applies_to(self, task_id: str) -> bool:
        return "*" in self.scope or task_id in self.scope

    def is_current(self, today: dt.date | None = None) -> bool:
        today = today or dt.date.today()
        if self.valid_from and dt.date.fromisoformat(self.valid_from) > today:
            return False
        if self.valid_until and dt.date.fromisoformat(self.valid_until) < today:
            return False
        return True

    def index_terms(self) -> set[str]:
        # Subject, file path, and statement (which carries the docstring for code facts).
        return terms(f"{self.subject} {self.source.split(':')[0]} {self.statement}")


def code_fact_id(repo: str, source: str, subject: str) -> str:
    return "code:" + hashlib.sha1(f"{repo}|{source}|{subject}".encode()).hexdigest()[:12]


class KnowledgeStore(Protocol):
    def upsert(self, facts: list[Fact]) -> int: ...
    def replace_code(self, repo: str, facts: list[Fact]) -> int: ...
    def delete(self, fact_id: str) -> None: ...
    def get(self, fact_id: str) -> Fact | None: ...
    def facts(self, task_id: str | None = None, kinds: tuple[str, ...] | None = None) -> list[Fact]: ...
    def search(self, text: str, task_id: str | None = None, limit: int = 20,
               kinds: tuple[str, ...] | None = None) -> list[tuple[int, Fact]]: ...
    def stats(self) -> dict: ...


def store() -> KnowledgeStore:
    from .local import LocalKnowledgeStore

    return LocalKnowledgeStore()
