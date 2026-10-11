"""The authoritative check between a detector's claims and a raised finding.

A claim becomes a finding only if it cites a verified fact that is current and applies to
the task, quotes that fact verbatim, quotes the engineer verbatim, and the engineer has
committed rather than explored. A model cannot invent a conflict past this point.
"""
import re

from ..knowledge import Fact
from . import RELATIONS, Analysis, Claim

MIN_FACT_QUOTE = 8
MIN_EVENT_QUOTE = 6


def _norm(s: str) -> str:
    s = s.lower().replace("’", "'").replace("“", '"').replace("”", '"')
    s = re.sub(r"\s+", " ", s).strip()
    return s.strip(" \"'.,;:`")


def check(claim: Claim, analysis: Analysis, content: str, facts: dict[str, Fact]) -> str | None:
    """Return None if the claim is grounded, otherwise the reason it was rejected."""
    if claim.relation not in RELATIONS:
        return "invalid_relation"
    fact = facts.get(claim.fact_id)
    if fact is None:
        return "unknown_fact"
    if not fact.is_current():
        return "fact_not_current"
    fq, eq = _norm(claim.fact_quote), _norm(claim.event_quote)
    if len(fq) < MIN_FACT_QUOTE or (fq not in _norm(fact.statement) and fq not in _norm(fact.evidence)):
        return "fact_quote_not_in_fact"
    if len(eq) < MIN_EVENT_QUOTE or eq not in _norm(content):
        return "event_quote_not_in_event"
    if analysis.stage != "committed":
        return "not_committed"
    return None


def verify(analysis: Analysis, content: str, facts: list[Fact]) -> tuple[list[Claim], list[tuple[Claim, str]]]:
    by_id = {f.id: f for f in facts}
    grounded, rejected, seen = [], [], set()
    for claim in analysis.claims:
        reason = check(claim, analysis, content, by_id)
        if reason:
            rejected.append((claim, reason))
        elif claim.fact_id not in seen:
            seen.add(claim.fact_id)
            grounded.append(claim)
    return grounded, rejected
