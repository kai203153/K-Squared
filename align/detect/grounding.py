"""The authoritative check between a detector's claims and a raised finding.

A claim becomes a finding only if it cites a confirmed, current record that applies to the
task, quotes that record verbatim, quotes the event verbatim, and the developer has
committed rather than explored. A model cannot invent a conflict past this point.
"""
import re

from ..context import is_current
from . import Analysis, Claim

MIN_DECISION_QUOTE = 12
MIN_EVENT_QUOTE = 6


def _norm(s: str) -> str:
    s = s.lower().replace("’", "'").replace("“", '"').replace("”", '"')
    s = re.sub(r"\s+", " ", s).strip()
    return s.strip(" \"'.,;:")


def check(claim: Claim, analysis: Analysis, content: str, records: dict[str, dict]) -> str | None:
    """Return None if the claim is grounded, otherwise the reason it was rejected."""
    record = records.get(claim.record_id)
    if record is None:
        return "unknown_record"
    if record["status"] != "confirmed" or not is_current(record):
        return "record_not_authoritative"
    dq, eq = _norm(claim.decision_quote), _norm(claim.event_quote)
    if len(dq) < MIN_DECISION_QUOTE or dq not in _norm(record["body"]):
        return "decision_quote_not_in_record"
    if len(eq) < MIN_EVENT_QUOTE or eq not in _norm(content):
        return "event_quote_not_in_event"
    if analysis.stage != "committed":
        return "not_committed"
    return None


def verify(analysis: Analysis, content: str, records: list[dict]) -> tuple[list[Claim], list[tuple[Claim, str]]]:
    by_id = {r["id"]: r for r in records}
    grounded, rejected, seen = [], [], set()
    for claim in analysis.claims:
        reason = check(claim, analysis, content, by_id)
        if reason:
            rejected.append((claim, reason))
        elif claim.record_id not in seen:
            seen.add(claim.record_id)
            grounded.append(claim)
    return grounded, rejected
