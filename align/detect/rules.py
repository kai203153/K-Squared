"""Offline detector. Two checks, both grounded in verified facts:

1. Decisions: a non-negated match of a fact's `forbid` pattern, or a negated match of a
   `require` pattern, means the engineer assumes the decision doesn't apply.
2. Existing code: "create a new X" where production code already has an X means the
   engineer assumes X doesn't exist yet.
"""
import re

from ..knowledge import CODE_KINDS, DECISION_KINDS, Fact, terms
from . import ALREADY_EXISTS, CONTRADICTED, Analysis, Claim
from .stage import classify, negated

SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")
CREATE = re.compile(
    r"\b(?:create|add|build|write|implement|make|introduce|set up|scaffold)\s+"
    r"(?:a |an |the |our |my )?(?:(?:new|separate|standalone|custom|own|another)\s+)*"
    r"(?P<obj>\w[\w\s/-]{2,60}?)(?=\s+(?:for|to|that|which|in|on|with|from|so|and|now|using)\b|[.,;:!?\n]|$)",
    re.I,
)
MIN_OVERLAP = 2


def sentence_at(text: str, pos: int) -> str:
    for m in SENTENCE.finditer(text):
        if m.start() <= pos < m.end():
            return m.group().strip()
    return text.strip()


def best_quote(body: str, hint: str) -> str:
    """The body sentence sharing the most words with `hint`, so the quote is always verbatim."""
    sentences = [m.group().strip() for m in SENTENCE.finditer(body) if m.group().strip()]
    words = set(re.findall(r"\w+", hint.lower()))
    return max(sentences, key=lambda s: len(words & set(re.findall(r"\w+", s.lower()))), default=body)


def normalize_tool_text(text: str) -> str:
    # "Write lambda_handler.py" -> "Write lambda handler py" so word boundaries work on filenames.
    return re.sub(r"[_\-./]+", " ", text)


class RulesDetector:
    name = "rules"

    def analyze(self, content: str, kind: str, facts: list[Fact]) -> Analysis:
        text = normalize_tool_text(content) if kind == "pre_tool" else content
        claims = [c for f in facts if f.kind in DECISION_KINDS and (c := self._decision(f, text))]
        if kind != "pre_tool":
            claims += self._existing(text, [f for f in facts if f.kind in CODE_KINDS])
        if kind == "pre_tool":
            # Quote the original content; the normalized text is not verbatim.
            for c in claims:
                c.event_quote = content.strip()
        statement = claims[0].event_quote if claims else ""
        return Analysis(stage=classify(text, kind), statement=statement, claims=claims, detector=self.name)

    def _decision(self, fact: Fact, text: str) -> Claim | None:
        for pattern in fact.forbid:
            for m in re.finditer(pattern, text, re.I):
                if not negated(text, m.start()):
                    return self._claim(fact, text, m, f"{fact.id} rules this out.",
                                       f"{fact.id} does not apply to \"{m.group()}\"")
        for pattern in fact.require:
            for m in re.finditer(pattern, text, re.I):
                if negated(text, m.start()):
                    return self._claim(fact, text, m, f"{fact.id} requires this.",
                                       f"Skipping \"{m.group()}\" is acceptable")
        return None

    def _claim(self, fact: Fact, text: str, m: re.Match, why: str, assumption: str) -> Claim:
        return Claim(fact_id=fact.id, fact_quote=best_quote(fact.statement, m.group()),
                     event_quote=sentence_at(text, m.start()), explanation=why,
                     relation=CONTRADICTED, assumption=assumption)

    def _existing(self, text: str, facts: list[Fact]) -> list[Claim]:
        claims, seen = [], set()
        for m in CREATE.finditer(text):
            if negated(text, m.start()):
                continue
            wanted = terms(m.group("obj"))
            if len(wanted) < MIN_OVERLAP:
                continue
            scored = [(len(wanted & f.index_terms()), f) for f in facts]
            scored = [(s, f) for s, f in scored if s >= MIN_OVERLAP and s * 2 >= len(wanted)]
            if not scored:
                continue
            # Prefer the closest match; components before infra/dependencies on ties.
            _, fact = max(scored, key=lambda x: (x[0], x[1].kind == "component", -len(x[1].subject)))
            if fact.id in seen:
                continue
            seen.add(fact.id)
            obj = m.group("obj").strip()
            claims.append(Claim(
                fact_id=fact.id, fact_quote=fact.evidence or fact.statement,
                event_quote=sentence_at(text, m.start()),
                explanation=f"{fact.subject} already exists in production code ({fact.source}).",
                relation=ALREADY_EXISTS, assumption=f"There is no existing {obj}",
            ))
        return claims
