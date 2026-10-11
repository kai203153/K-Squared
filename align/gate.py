"""Opt-in PreToolUse gate: ask the developer to confirm before acting against an open finding."""
import time

from . import db
from .config import GATE_MODES

MAX_WAIT = 2.0


def _pending(task_id: str, session: str) -> bool:
    with db.connect() as c:
        return c.execute(
            "SELECT 1 FROM events WHERE task_id=? AND session=? AND status='pending' LIMIT 1",
            (task_id, session),
        ).fetchone() is not None


def check(task_id: str, session: str, mode: str, wait: float = MAX_WAIT) -> dict:
    if mode not in GATE_MODES:
        raise ValueError(f"mode must be one of {GATE_MODES}")
    if mode == "off":
        return {"decision": "allow"}
    # The prompt that triggered this tool call may still be under analysis.
    deadline = time.monotonic() + min(wait, MAX_WAIT)
    while _pending(task_id, session) and time.monotonic() < deadline:
        time.sleep(0.1)

    sql = "SELECT * FROM findings WHERE task_id=? AND session=? AND status='open' AND severity='HIGH'"
    if mode == "once":
        sql += " AND gate_shown_at IS NULL"
    with db.connect() as c:
        findings = [dict(r) for r in c.execute(sql + " ORDER BY created", (task_id, session))]
        if not findings:
            return {"decision": "allow"}
        c.executemany("UPDATE findings SET gate_shown_at=? WHERE id=? AND gate_shown_at IS NULL",
                      [(time.time(), f["id"]) for f in findings])
    f = findings[0]
    reason = (
        f"Align: this work conflicts with confirmed {f['record_id']}: \"{f['decision_quote']}\" "
        f"(you said: \"{f['event_quote']}\"). Approve to continue anyway, or deny and respond to "
        f"the finding on the Align dashboard."
    )
    if len(findings) > 1:
        reason += f" {len(findings) - 1} more open finding(s)."
    return {"decision": "ask", "reason": reason, "finding_ids": [x["id"] for x in findings]}
