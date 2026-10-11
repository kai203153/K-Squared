"""Load authorized context (tasks and decision records) from files into the database.

A task is a JSON file: {"id", "title", "goal", "work": [{"id", "description", "hours",
"dependency", "invalidation"}]}.

A record is a Markdown file with a `---` frontmatter block of `key: value` lines. Repeated
keys (e.g. several `forbid:` lines) become lists. The body below the frontmatter is the
authoritative text that every finding must quote.
"""
import datetime as dt
import json
import re
import time
from pathlib import Path

from . import db
from .knowledge import DECISION_KINDS, Fact, store

RECORD_STATUSES = ("confirmed", "proposed", "superseded")
LIST_KEYS = ("applies_to", "forbid", "require")


def parse_frontmatter(text: str) -> tuple[dict, str]:
    match = re.match(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", text, re.S)
    if not match:
        return {}, text
    meta: dict = {}
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key, value = key.strip(), value.strip()
        if key in LIST_KEYS:
            parts = [value] if key != "applies_to" else [v.strip() for v in value.split(",")]
            meta.setdefault(key, []).extend(p for p in parts if p)
        else:
            meta[key] = value
    return meta, match.group(2).strip()


def parse_record(path: Path) -> dict | None:
    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    if not meta.get("id") or not body:
        return None
    status = meta.get("status", "proposed")
    if status not in RECORD_STATUSES:
        raise ValueError(f"{path}: status must be one of {RECORD_STATUSES}")
    for pattern in meta.get("forbid", []) + meta.get("require", []):
        re.compile(pattern)  # Fail at import time, not during analysis.
    return {
        "id": meta["id"],
        "title": meta.get("title", meta["id"]),
        "kind": meta.get("kind", "decision"),
        "status": status,
        "applies_to": meta.get("applies_to", ["*"]),
        "valid_from": meta.get("valid_from"),
        "valid_until": meta.get("valid_until"),
        "source": meta.get("source", str(path)),
        "body": body,
        "forbid": meta.get("forbid", []),
        "require": meta.get("require", []),
    }


def parse_task(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not data.get("id") or not data.get("title"):
        return None
    return data


def import_dir(directory: str | Path) -> dict:
    """Upsert every task and record found under `directory`."""
    root = Path(directory).resolve()
    if not root.is_dir():
        raise ValueError(f"not a directory: {root}")
    tasks = [t for p in sorted(root.rglob("*.json")) if (t := parse_task(p))]
    records = [r for p in sorted(root.rglob("*.md")) if (r := parse_record(p))]
    now = time.time()
    with db.connect() as c:
        for t in tasks:
            c.execute(
                "INSERT OR REPLACE INTO tasks VALUES(?,?,?,?,?)",
                (t["id"], t["title"], t.get("goal", ""), str(root), now),
            )
            c.execute("DELETE FROM work WHERE task_id=?", (t["id"],))
            for w in t.get("work", []):
                c.execute(
                    "INSERT INTO work VALUES(?,?,?,?,?,?)",
                    (f"{t['id']}-{w['id']}", t["id"], w["description"], float(w["hours"]),
                     float(w.get("dependency", 1)), float(w.get("invalidation", 1))),
                )
        for r in records:
            c.execute(
                "INSERT OR REPLACE INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (r["id"], r["title"], r["kind"], r["status"], json.dumps(r["applies_to"]),
                 r["valid_from"], r["valid_until"], r["source"], r["body"],
                 json.dumps(r["forbid"]), json.dumps(r["require"]), now),
            )
    sync_knowledge(records)
    return {"tasks": [t["id"] for t in tasks], "records": [r["id"] for r in records]}


def record_fact(r: dict) -> Fact:
    kind = r["kind"] if r["kind"] in DECISION_KINDS else "decision"
    return Fact(
        id=r["id"], kind=kind, subject=r["title"], statement=r["body"], source=r["source"],
        source_type="record", scope=r["applies_to"], forbid=r["forbid"], require=r["require"],
        valid_from=r["valid_from"], valid_until=r["valid_until"],
        verified_by=f"record-status:confirmed ({r['source']})",
    )


def sync_knowledge(records: list[dict]) -> None:
    """Confirmed records become ground truth; anything else is removed from it."""
    kb = store()
    kb.upsert([record_fact(r) for r in records if r["status"] == "confirmed"])
    for r in records:
        if r["status"] != "confirmed":
            kb.delete(r["id"])


def _row_to_record(row) -> dict:
    r = dict(row)
    for key in LIST_KEYS:
        r[key] = json.loads(r[key])
    return r


def is_current(record: dict, today: dt.date | None = None) -> bool:
    today = today or dt.date.today()
    if record.get("valid_from") and dt.date.fromisoformat(record["valid_from"]) > today:
        return False
    if record.get("valid_until") and dt.date.fromisoformat(record["valid_until"]) < today:
        return False
    return True


def records_for(task_id: str, authoritative_only: bool = False) -> list[dict]:
    with db.connect() as c:
        rows = c.execute("SELECT * FROM records ORDER BY id").fetchall()
    out = []
    for r in map(_row_to_record, rows):
        if task_id not in r["applies_to"] and "*" not in r["applies_to"]:
            continue
        if authoritative_only and not (r["status"] == "confirmed" and is_current(r)):
            continue
        out.append(r)
    return out


def task_exists(task_id: str) -> bool:
    with db.connect() as c:
        return c.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone() is not None
