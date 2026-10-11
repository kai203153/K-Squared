"""What each Claude Code hook should say about open findings.

Every finding reaches the engineer once, through Claude: as hidden context that Claude
raises as a question (prompt and post-tool hooks), or by sending a plan back for revision.
A short visible line (`system_message`) tells the engineer directly. HIGH findings can also
pause file and shell tools (`ALIGN_GATE=once|strict`).
"""
import time

from . import db, engine
from .config import GATE_MODES, settings
from .detect import ALREADY_EXISTS

HOOKS = ("prompt", "plan", "pre_tool", "post_tool")
TOOL_WAIT = 2.0
MAX_WAIT = 30.0
RELATION_LABEL = {"contradicted": "contradicts", ALREADY_EXISTS: "already exists"}


def _pending(task_id: str, session: str, event_id: str | None) -> bool:
    with db.connect() as c:
        if event_id:
            row = c.execute("SELECT status FROM events WHERE id=?", (event_id,)).fetchone()
            return row is not None and row["status"] == "pending"
        return c.execute("SELECT 1 FROM events WHERE task_id=? AND session=? AND status='pending' LIMIT 1",
                         (task_id, session)).fetchone() is not None


def wait_for_analysis(task_id: str, session: str, event_id: str | None, wait: float) -> None:
    deadline = time.monotonic() + min(wait, MAX_WAIT)
    while _pending(task_id, session, event_id) and time.monotonic() < deadline:
        time.sleep(0.05)


def for_claude(findings: list[dict]) -> str:
    lines = ["Align checked the engineer's request against verified team knowledge (confirmed "
             "decisions and production code) and found assumptions worth raising:"]
    for i, f in enumerate(findings, 1):
        what = ("already exists in production code" if f["relation"] == ALREADY_EXISTS
                else "contradicts a confirmed " + (f["fact_kind"] or "decision"))
        lines.append(
            f"{i}. [{f['severity']}] Assumption: {f['assumption'] or f['event_quote']}\n"
            f"   Engineer said: \"{f['event_quote']}\"\n"
            f"   Verified fact ({f['fact_id']}, {f['fact_source']}) {what}: \"{f['fact_quote']}\"\n"
            f"   Why it matters: {f['explanation']}")
    lines.append("Before continuing, point this out to the engineer briefly, cite the source, and ask "
                 "how they want to proceed. They may have context Align lacks; let them decide. "
                 "Don't lecture, and don't repeat this if they have already answered it.")
    return "\n".join(lines)


def for_engineer(findings: list[dict]) -> str:
    parts = [f"[{f['severity']}] {f['fact_id']} {RELATION_LABEL.get(f['relation'], f['relation'])}: "
             f"\"{f['fact_quote'][:120]}\"" for f in findings]
    return "Align: check your assumptions. " + " | ".join(parts)


def _mark(column: str, ids: list[str]) -> None:
    with db.connect() as c:
        c.executemany(f"UPDATE findings SET {column}=? WHERE id=? AND {column} IS NULL",
                      [(time.time(), i) for i in ids])


def respond(task_id: str, session: str, hook: str, mode: str | None = None,
            event_id: str | None = None, wait: float | None = None) -> dict:
    if hook not in HOOKS:
        raise ValueError(f"hook must be one of {HOOKS}")
    mode = mode or settings().gate
    if mode not in GATE_MODES:
        raise ValueError(f"mode must be one of {GATE_MODES}")
    if wait is None:
        wait = settings().nudge_wait if hook in ("prompt", "plan") else TOOL_WAIT
    wait_for_analysis(task_id, session, event_id, wait)

    open_ = engine.open_findings(task_id, session)
    undelivered = [f for f in open_ if f["delivered_at"] is None]
    out: dict = {"decision": "allow"}

    if hook == "pre_tool":
        if mode != "off":
            gated = [f for f in open_ if f["severity"] == "HIGH"
                     and (mode == "strict" or f["gate_shown_at"] is None)]
            if gated:
                f = gated[0]
                out = {"decision": "ask", "finding_ids": [x["id"] for x in gated], "reason": (
                    f"Align: this work assumes {f['fact_id']} doesn't apply, but it says: "
                    f"\"{f['fact_quote']}\" (you said: \"{f['event_quote']}\"). Approve to continue "
                    f"anyway, or deny and respond to the finding on the Align dashboard.")}
                _mark("gate_shown_at", [x["id"] for x in gated])
        return out

    if not undelivered:
        return out
    out["system_message"] = for_engineer(undelivered)
    if hook == "plan":
        if mode == "off":
            return out  # Claude learns after the plan is approved, via post_tool.
        out.update(decision="deny", reason=for_claude(undelivered) +
                   "\nRevise the plan to address these, or ask the engineer to confirm, then present it again.")
    else:
        out["additional_context"] = for_claude(undelivered)
    _mark("delivered_at", [f["id"] for f in undelivered])
    return out
