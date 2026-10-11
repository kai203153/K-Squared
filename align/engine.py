"""Event ingestion, assumption analysis, findings, and engineer responses."""
import json
import logging
import time
import uuid
from dataclasses import asdict

from . import context, db, notify
from .detect import ALREADY_EXISTS, UNCONFIRMED, Analysis, Claim, select_detector
from .detect.grounding import verify
from .detect.rules import RulesDetector
from .knowledge import DECISION_KINDS, Fact, store

log = logging.getLogger("align.engine")

EVENT_KINDS = ("prompt", "plan", "pre_tool", "post_tool")
MAX_CONTENT = 6000
CANDIDATE_FACTS = 25
RESPONSES = {
    "acknowledge": "acknowledged",  # "Yes, I'm aware I'm assuming this."
    "use_existing": "resolved",
    "new_evidence": "needs_review",
    "ask_manager": "needs_review",
    "dismiss": "dismissed",
}
SEVERITY_RANK = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


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


def candidate_facts(task_id: str, content: str) -> list[Fact]:
    """Every decision for the task, plus the production-code facts most related to the event."""
    kb = store()
    decisions = kb.facts(task_id, DECISION_KINDS)
    related = [f for _, f in kb.search(content, task_id, CANDIDATE_FACTS) if f.kind not in DECISION_KINDS]
    return decisions + related


def severity(claim: Claim, fact: Fact | None) -> str:
    """HIGH: a confirmed decision rules it out. MEDIUM: production code disagrees or already
    has it. LOW: an unconfirmed assumption the engineer should acknowledge."""
    if claim.relation == UNCONFIRMED or fact is None:
        return "LOW"
    if claim.relation == ALREADY_EXISTS:
        return "MEDIUM"
    return "HIGH" if fact.kind in DECISION_KINDS else "MEDIUM"


def _detect(content: str, kind: str, facts: list[Fact]) -> Analysis:
    detector = select_detector(kind)
    try:
        return detector.analyze(content, kind, facts)
    except Exception as e:  # Any detector failure degrades to the offline rules.
        if isinstance(detector, RulesDetector):
            raise
        log.warning("%s detector failed, using rules: %s", detector.name, str(e)[:200])
        analysis = RulesDetector().analyze(content, kind, facts)
        analysis.notes.append(f"{detector.name}_failed: {type(e).__name__}")
        return analysis


def analyze_event(event_id: str) -> dict:
    with db.connect() as c:
        ev = c.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if ev is None:
        raise ValueError("unknown event")
    if ev["kind"] == "post_tool":
        return _finish(event_id, {"result": "ignored_non_decision_event"})

    facts = candidate_facts(ev["task_id"], ev["content"])
    by_id = {f.id: f for f in facts}
    analysis = _detect(ev["content"], ev["kind"], facts)
    grounded, rejected = verify(analysis, ev["content"], facts)
    low, high = exposure(ev["task_id"])
    now = time.time()
    finding_ids, new_findings = [], []
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
                (str(uuid.uuid4()), event_id, claim.fact_id, json.dumps(asdict(claim)), reason, now),
            )
        for claim in grounded:
            # Once seen in this session (open, acknowledged, under review or dismissed), don't raise it
            # again. Only a resolved finding can recur, e.g. the engineer drifts back to the old plan.
            existing = c.execute(
                "SELECT id FROM findings WHERE task_id=? AND session=? AND fact_id=? AND status!='resolved'",
                (ev["task_id"], ev["session"], claim.fact_id),
            ).fetchone()
            if existing:
                finding_ids.append(existing["id"])
                continue
            fact = by_id.get(claim.fact_id)
            sev = severity(claim, fact)
            row = {
                "id": str(uuid.uuid4()), "task_id": ev["task_id"], "session": ev["session"],
                "commitment_id": commitment_id, "fact_id": claim.fact_id, "severity": sev,
                "status": "open", "fact_quote": claim.fact_quote, "event_quote": claim.event_quote,
                "explanation": claim.explanation, "detector": analysis.detector,
                # Exposure models reworking a committed direction; it doesn't apply to a pointer.
                "impact_min": low if sev == "HIGH" else 0, "impact_max": high if sev == "HIGH" else 0,
                "created": now, "updated": now, "relation": claim.relation,
                "assumption": claim.assumption, "fact_kind": fact.kind if fact else "assumption",
                "fact_source": fact.source if fact else "",
            }
            c.execute(f"INSERT INTO findings({','.join(row)}) VALUES({','.join('?' * len(row))})",
                      list(row.values()))
            finding_ids.append(row["id"])
            new_findings.append(row)
    for f in new_findings:
        notify.notify_finding(f)
    return _finish(event_id, {
        "result": "finding" if finding_ids else
                  ("exploration" if analysis.stage == "exploring" else "no_supported_finding"),
        "stage": analysis.stage,
        "statement": analysis.statement,
        "detector": analysis.detector,
        "findings": finding_ids,
        "rejected": [{"fact_id": cl.fact_id, "reason": r} for cl, r in rejected],
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
    if finding["relation"] == UNCONFIRMED:
        ask = (f"I'm assuming that {finding['assumption'][0].lower()}{finding['assumption'][1:].rstrip('.')}. "
               f"Can you confirm that, or point me to where it's documented?")
    elif finding["relation"] == ALREADY_EXISTS:
        ask = (f"I was about to build something new, but {finding['fact_source']} already has "
               f"\"{finding['fact_quote']}\". Should I reuse that, or is there a reason it doesn't fit?")
    else:
        ask = (f"It looks like it conflicts with {finding['fact_id']}: \"{finding['fact_quote']}\". "
               f"Does an approved exception apply here, or should I stay with the current decision?")
    return (f"Hi, while working on {task_name} I'm considering this direction: "
            f"\"{finding['event_quote']}\". {ask} Happy to share the evidence I have.")


def respond(finding_id: str, response: str, note: str = "") -> dict:
    if response not in RESPONSES:
        raise ValueError(f"response must be one of {tuple(RESPONSES)}")
    with db.connect() as c:
        row = c.execute("SELECT * FROM findings WHERE id=?", (finding_id,)).fetchone()
        if row is None:
            raise ValueError("unknown finding")
        finding = dict(row)
        task = c.execute("SELECT * FROM tasks WHERE id=?", (finding["task_id"],)).fetchone()
        # Draft only: Align never sends messages on the engineer's behalf.
        draft = clarification_draft(finding, dict(task) if task else None) if response == "ask_manager" else None
        c.execute("INSERT INTO responses VALUES(?,?,?,?,?,?)",
                  (str(uuid.uuid4()), finding_id, response, note[:2000], draft, time.time()))
        c.execute("UPDATE findings SET status=?, updated=? WHERE id=?",
                  (RESPONSES[response], time.time(), finding_id))
    return {"ok": True, "response": response, "status": RESPONSES[response], "draft": draft}


def acknowledge_delivered(task_id: str, session: str) -> list[str]:
    """The engineer replied after Claude raised an unconfirmed assumption: record that they saw it."""
    with db.connect() as c:
        ids = [r["id"] for r in c.execute(
            "SELECT id FROM findings WHERE task_id=? AND session=? AND status='open' AND relation=? "
            "AND delivered_at IS NOT NULL", (task_id, session, UNCONFIRMED))]
    for fid in ids:
        respond(fid, "acknowledge", "Replied in the chat after Align raised it.")
    return ids


def open_findings(task_id: str, session: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM findings WHERE task_id=? AND status='open'", [task_id]
    if session is not None:
        sql, args = sql + " AND session=?", args + [session]
    with db.connect() as c:
        rows = [dict(r) for r in c.execute(sql + " ORDER BY created", args)]
    return sorted(rows, key=lambda f: SEVERITY_RANK.get(f["severity"], 9))


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
            r["fact_id"]: r["n"] for r in c.execute(
                "SELECT fact_id, COUNT(*) n FROM findings WHERE task_id=? AND status='dismissed' GROUP BY fact_id",
                (task_id,))
        }
        events = q("SELECT * FROM events WHERE task_id=? ORDER BY created DESC LIMIT 40")
        for e in events:
            e["analysis"] = json.loads(e["analysis"]) if e["analysis"] else None
        commitments = q("SELECT * FROM commitments WHERE task_id=? ORDER BY created DESC LIMIT 20")
        work = q("SELECT * FROM work WHERE task_id=? ORDER BY id")
    low, high = exposure(task_id) if task_id else (0, 0)
    return {
        "task_id": task_id,
        "tasks": tasks,
        "records": [
            {**r, "authoritative": r["status"] == "confirmed" and context.is_current(r),
             "dismissals": dismissals.get(r["id"], 0)}
            for r in (context.records_for(task_id) if task_id else [])
        ],
        "knowledge": store().stats(),
        "events": events,
        "commitments": commitments,
        "findings": findings,
        "work": work,
        "exposure": [low, high],
    }


def search_knowledge(query: str, task_id: str | None, limit: int = 20) -> list[dict]:
    kb = store()
    hits = kb.search(query, task_id, limit) if query.strip() else [(0, f) for f in kb.facts(task_id)[:limit]]
    return [{**asdict(f), "score": s} for s, f in hits]
