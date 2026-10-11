"""Event ingestion, analysis, findings, and developer responses."""
import json
import logging
import time
import uuid
from dataclasses import asdict

from . import context, db
from .detect import Analysis, select_detector
from .detect.grounding import verify
from .detect.rules import RulesDetector

log = logging.getLogger("align.engine")

EVENT_KINDS = ("prompt", "pre_tool", "post_tool")
MAX_CONTENT = 6000
RESPONSES = {
    "use_existing": "resolved",
    "new_evidence": "needs_review",
    "ask_manager": "needs_review",
    "dismiss": "dismissed",
}


def ingest(event: dict) -> str:
    """Store an event for asynchronous analysis and return its id."""
    task = str(event.get("task_id", ""))
    kind = event.get("kind", "prompt")
    content = str(event.get("content", ""))[:MAX_CONTENT]
    if kind not in EVENT_KINDS:
        raise ValueError(f"kind must be one of {EVENT_KINDS}")
    if not context.task_exists(task):
        raise ValueError(f"unknown task: {task}")
    if not content.strip():
        raise ValueError("empty content")
    event_id = str(uuid.uuid4())
    with db.connect() as c:
        c.execute(
            "INSERT INTO events VALUES(?,?,?,?,?,?,?,?)",
            (event_id, task, str(event.get("session", "unknown"))[:128], kind, content,
             "pending", None, time.time()),
        )
    return event_id


def pending_event_ids() -> list[str]:
    with db.connect() as c:
        return [r["id"] for r in c.execute("SELECT id FROM events WHERE status='pending' ORDER BY created")]


def exposure(task_id: str) -> tuple[float, float]:
    """Dependency-weighted hours at risk if the direction is reversed (not time saved)."""
    with db.connect() as c:
        work = c.execute("SELECT * FROM work WHERE task_id=?", (task_id,)).fetchall()
    upper = round(sum(w["hours"] * w["dependency"] * w["invalidation"] for w in work), 2)
    return round(upper * 0.5, 2), upper


def _detect(content: str, kind: str, records: list[dict]) -> Analysis:
    detector = select_detector(kind)
    try:
        return detector.analyze(content, kind, records)
    except Exception as e:  # Any detector failure degrades to the offline rules.
        if isinstance(detector, RulesDetector):
            raise
        log.warning("%s detector failed, using rules: %s", detector.name, str(e)[:200])
        analysis = RulesDetector().analyze(content, kind, records)
        analysis.notes.append(f"{detector.name}_failed: {type(e).__name__}")
        return analysis


def analyze_event(event_id: str) -> dict:
    with db.connect() as c:
        ev = c.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if ev is None:
        raise ValueError("unknown event")
    if ev["kind"] == "post_tool":
        return _finish(event_id, {"result": "ignored_non_decision_event"})

    records = context.records_for(ev["task_id"], authoritative_only=True)
    analysis = _detect(ev["content"], ev["kind"], records)
    grounded, rejected = verify(analysis, ev["content"], records)
    low, high = exposure(ev["task_id"])
    now = time.time()
    finding_ids = []
    with db.connect() as c:
        commitment_id = None
        if analysis.stage == "committed" and analysis.statement:
            commitment_id = str(uuid.uuid4())
            c.execute(
                "INSERT INTO commitments VALUES(?,?,?,?,?,?,?,?)",
                (commitment_id, ev["task_id"], ev["session"], analysis.statement, analysis.stage,
                 event_id, analysis.detector, now),
            )
        for claim, reason in rejected:
            c.execute(
                "INSERT INTO unverified VALUES(?,?,?,?,?,?)",
                (str(uuid.uuid4()), event_id, claim.record_id, json.dumps(asdict(claim)), reason, now),
            )
        for claim in grounded:
            existing = c.execute(
                "SELECT id FROM findings WHERE task_id=? AND session=? AND record_id=? AND status='open'",
                (ev["task_id"], ev["session"], claim.record_id),
            ).fetchone()
            if existing:
                finding_ids.append(existing["id"])
                continue
            fid = str(uuid.uuid4())
            c.execute(
                "INSERT INTO findings VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (fid, ev["task_id"], ev["session"], commitment_id, claim.record_id, "HIGH", "open",
                 claim.decision_quote, claim.event_quote, claim.explanation, analysis.detector,
                 low, high, None, now, now),
            )
            finding_ids.append(fid)
    return _finish(event_id, {
        "result": "conflict" if finding_ids else
                  ("exploration" if analysis.stage == "exploring" else "no_supported_conflict"),
        "stage": analysis.stage,
        "statement": analysis.statement,
        "detector": analysis.detector,
        "findings": finding_ids,
        "rejected": [{"record_id": cl.record_id, "reason": r} for cl, r in rejected],
        "notes": analysis.notes,
    })


def _finish(event_id: str, analysis: dict, status: str = "analyzed") -> dict:
    with db.connect() as c:
        c.execute("UPDATE events SET status=?, analysis=? WHERE id=?",
                  (status, json.dumps(analysis), event_id))
    return analysis


def mark_failed(event_id: str, error: str) -> None:
    _finish(event_id, {"result": "error", "error": error[:300]}, status="failed")


def clarification_draft(finding: dict, task: dict | None) -> str:
    task_name = f"{finding['task_id']} ({task['title']})" if task else finding["task_id"]
    return (
        f"Hi, while working on {task_name} I'm considering this direction: "
        f"\"{finding['event_quote']}\". It looks like it conflicts with {finding['record_id']}: "
        f"\"{finding['decision_quote']}\". Does an approved exception apply here, or should I "
        f"stay with the current decision? Happy to share the evidence I have."
    )


def respond(finding_id: str, response: str, note: str = "") -> dict:
    if response not in RESPONSES:
        raise ValueError(f"response must be one of {tuple(RESPONSES)}")
    with db.connect() as c:
        row = c.execute("SELECT * FROM findings WHERE id=?", (finding_id,)).fetchone()
        if row is None:
            raise ValueError("unknown finding")
        finding = dict(row)
        task = c.execute("SELECT * FROM tasks WHERE id=?", (finding["task_id"],)).fetchone()
        # Draft only: Align never sends messages on the developer's behalf.
        draft = clarification_draft(finding, dict(task) if task else None) if response == "ask_manager" else None
        c.execute("INSERT INTO responses VALUES(?,?,?,?,?,?)",
                  (str(uuid.uuid4()), finding_id, response, note[:2000], draft, time.time()))
        c.execute("UPDATE findings SET status=?, updated=? WHERE id=?",
                  (RESPONSES[response], time.time(), finding_id))
    return {"ok": True, "response": response, "status": RESPONSES[response], "draft": draft}


def snapshot(task_id: str | None = None) -> dict:
    with db.connect() as c:
        tasks = [dict(r) for r in c.execute("SELECT * FROM tasks ORDER BY id")]
        if task_id is None and tasks:
            task_id = tasks[0]["id"]
        q = lambda sql, *a: [dict(r) for r in c.execute(sql, (task_id, *a))]
        findings = q("SELECT * FROM findings WHERE task_id=? ORDER BY created DESC LIMIT 30")
        responses = {}
        for r in c.execute("SELECT * FROM responses ORDER BY created"):
            responses.setdefault(r["finding_id"], []).append(dict(r))
        for f in findings:
            f["responses"] = responses.get(f["id"], [])
        dismissals = {
            r["record_id"]: r["n"] for r in c.execute(
                "SELECT record_id, COUNT(*) n FROM findings WHERE task_id=? AND status='dismissed' GROUP BY record_id",
                (task_id,))
        }
        events = q("SELECT * FROM events WHERE task_id=? ORDER BY created DESC LIMIT 40")
        for e in events:
            e["analysis"] = json.loads(e["analysis"]) if e["analysis"] else None
        low, high = exposure(task_id) if task_id else (0, 0)
        return {
            "task_id": task_id,
            "tasks": tasks,
            "records": [
                {**r, "authoritative": r["status"] == "confirmed" and context.is_current(r),
                 "dismissals": dismissals.get(r["id"], 0)}
                for r in (context.records_for(task_id) if task_id else [])
            ],
            "events": events,
            "commitments": q("SELECT * FROM commitments WHERE task_id=? ORDER BY created DESC LIMIT 20"),
            "findings": findings,
            "work": q("SELECT * FROM work WHERE task_id=? ORDER BY id"),
            "exposure": [low, high],
        }
