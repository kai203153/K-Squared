"""Detectors turn one captured event (a prompt, a plan, a tool call) into an Analysis: the
engineer's stage, what they are doing, and the *assumptions* behind it that verified
knowledge contradicts or already covers.

Claims are never trusted directly; `grounding.verify` decides which become findings.
"""
from dataclasses import dataclass, field

from ..config import settings

# How an assumption relates to a verified fact.
CONTRADICTED = "contradicted"      # The fact says otherwise ("ADR-12: do not build a new Lambda service").
ALREADY_EXISTS = "already_exists"  # The engineer assumes something is missing that already exists.
RELATIONS = (CONTRADICTED, ALREADY_EXISTS)


@dataclass
class Claim:
    fact_id: str
    fact_quote: str
    event_quote: str
    explanation: str
    relation: str = CONTRADICTED
    assumption: str = ""


@dataclass
class Analysis:
    stage: str  # "committed" | "exploring" | "none"
    statement: str
    claims: list[Claim] = field(default_factory=list)
    detector: str = "rules"
    notes: list[str] = field(default_factory=list)


def select_detector(kind: str):
    """Pick the detector for an event. Tool events carry only metadata, so they use rules."""
    from .rules import RulesDetector

    mode = settings().detector
    if kind not in ("prompt", "plan") or mode == "rules":
        return RulesDetector()
    from .claude import ClaudeDetector

    detector = ClaudeDetector()
    if detector.available():
        return detector
    if mode == "claude":
        raise RuntimeError("ALIGN_DETECTOR=claude but the anthropic SDK is not installed")
    return RulesDetector()
