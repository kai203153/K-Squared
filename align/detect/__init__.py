"""Detectors turn one captured event into an Analysis: the developer's stage, a short
statement of what they are doing, and *claims* of conflict with authorized records.

Claims are never trusted directly; `grounding.verify` decides which become findings.
"""
from dataclasses import dataclass, field

from ..config import settings


@dataclass
class Claim:
    record_id: str
    decision_quote: str
    event_quote: str
    explanation: str


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
    if kind != "prompt" or mode == "rules":
        return RulesDetector()
    from .claude import ClaudeDetector

    detector = ClaudeDetector()
    if detector.available():
        return detector
    if mode == "claude":
        raise RuntimeError("ALIGN_DETECTOR=claude but the anthropic SDK is not installed")
    return RulesDetector()
