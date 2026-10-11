"""Offline detector driven by the `forbid:` / `require:` patterns declared on each record.

- A non-negated match of a `forbid` pattern is a conflict ("create a new Lambda service").
- A negated match of a `require` pattern is a conflict ("do not reuse the Glue pipeline").
"""
import re

from . import Analysis, Claim
from .stage import classify, negated

SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")


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

    def analyze(self, content: str, kind: str, records: list[dict]) -> Analysis:
        text = normalize_tool_text(content) if kind == "pre_tool" else content
        stage = classify(text, kind)
        claims: list[Claim] = []
        for record in records:
            claim = self._match(record, text)
            if claim:
                # Quote the original content; for tool events the normalized text is not verbatim.
                if kind == "pre_tool":
                    claim.event_quote = content.strip()
                claims.append(claim)
        statement = claims[0].event_quote if claims else ""
        return Analysis(stage=stage, statement=statement, claims=claims, detector=self.name)

    def _match(self, record: dict, text: str) -> Claim | None:
        for pattern in record["forbid"]:
            for m in re.finditer(pattern, text, re.I):
                if not negated(text, m.start()):
                    return self._claim(record, text, m, f"Matches a pattern {record['id']} forbids.")
        for pattern in record["require"]:
            for m in re.finditer(pattern, text, re.I):
                if negated(text, m.start()):
                    return self._claim(record, text, m, f"Rejects something {record['id']} requires.")
        return None

    def _claim(self, record: dict, text: str, m: re.Match, why: str) -> Claim:
        return Claim(
            record_id=record["id"],
            decision_quote=best_quote(record["body"], m.group()),
            event_quote=sentence_at(text, m.start()),
            explanation=why,
        )
