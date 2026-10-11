"""Classify whether a prompt commits to action or only explores options."""
import re

EXECUTE = re.compile(
    r"\b(implement|build|create|deploy|write|go ahead|proceed|start coding|generate|scaffold|"
    r"provision|set up|add|migrate)\b",
    re.I,
)
EXPLORE = re.compile(
    r"\b(compare|consider|could|should we|what if|pros and cons|explore|explain|propose|"
    r"inspect|investigate|evaluate|tradeoffs?|options|evidence|would it)\b",
    re.I,
)
# Explicit go-signals win over exploratory wording ("compare A and B, then implement B").
STRONG_EXECUTE = re.compile(r"\b(go ahead|implement|deploy|create|build now|start coding)\b", re.I)
NEGATION = re.compile(
    r"\b(do not|don't|dont|never|avoid|without|instead of|rather than|not)\b", re.I
)


def negated(text: str, start: int, window: int = 30) -> bool:
    """True when the phrase at `start` is preceded, within its sentence, by a negation."""
    prefix = text[max(0, start - window):start]
    prefix = re.split(r"[.!?;\n]", prefix)[-1]
    return bool(NEGATION.search(prefix))


def _positive_matches(pattern: re.Pattern, text: str) -> bool:
    return any(not negated(text, m.start()) for m in pattern.finditer(text))


def classify(text: str, kind: str = "prompt") -> str:
    if kind == "pre_tool":
        return "committed"  # A tool call is an action, not a discussion.
    execute = _positive_matches(EXECUTE, text)
    strong = _positive_matches(STRONG_EXECUTE, text)
    explore = bool(EXPLORE.search(text))
    if execute and (strong or not explore):
        return "committed"
    if explore:
        return "exploring"
    return "none"
