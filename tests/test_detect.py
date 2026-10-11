from dataclasses import replace

from align.detect import ALREADY_EXISTS, Analysis, Claim
from align.detect.grounding import verify
from align.engine import candidate_facts
from align.knowledge import store
from align.detect.rules import RulesDetector
from align.detect.stage import classify

from .helpers import SCENARIOS, AlignTestCase

DECISION = "Do not introduce a new Lambda ingestion service without an approved exception."
EVENT = "Create a new standalone AWS Lambda ingestion service now."


class StageTests(AlignTestCase):
    def test_demo_scenarios(self):
        stages = {k: classify(v["prompt"]) for k, v in SCENARIOS.items()}
        self.assertEqual(stages, {"negative": "exploring", "positive": "committed",
                                  "corrective": "exploring", "legitimate": "exploring",
                                  "duplicate": "committed", "retry": "committed",
                                  "unconfirmed": "committed"})

    def test_negated_execute_is_not_commitment(self):
        self.assertNotEqual(classify("Do not create anything yet."), "committed")
        self.assertEqual(classify("Compare both, then go ahead and implement the cache."), "committed")
        self.assertEqual(classify("Write config.yml", "pre_tool"), "committed")


class RulesTests(AlignTestCase):
    def claims(self, text, kind="prompt"):
        return RulesDetector().analyze(text, kind, candidate_facts("DE-104", text)).claims

    def test_forbid_and_negated_require(self):
        self.assertEqual([c.fact_id for c in self.claims(SCENARIOS["positive"]["prompt"])], ["ADR-12"])
        self.assertEqual(len(self.claims("Ship it. Do not reuse the Glue pipeline.")), 1)

    def test_negated_forbid_is_not_a_claim(self):
        self.assertEqual(self.claims("Extend Glue. Do not create a new Lambda service."), [])

    def test_tool_filenames(self):
        claims = self.claims("Write lambda_handler.py", "pre_tool")
        self.assertEqual(claims[0].event_quote, "Write lambda_handler.py")
        self.assertEqual(self.claims("Edit glue_job.py", "pre_tool"), [])

    def test_existing_code_is_found(self):
        claims = self.claims(SCENARIOS["duplicate"]["prompt"])
        self.assertEqual([c.relation for c in claims], [ALREADY_EXISTS])
        self.assertEqual(store().get(claims[0].fact_id).subject, "validate_event")
        self.assertEqual(claims[0].fact_quote, "def validate_event(event: dict, schema: EventSchema) -> None:")
        # The docstring is searchable, so a differently named helper is still found.
        self.assertEqual(store().get(self.claims(SCENARIOS["retry"]["prompt"])[0].fact_id).subject, "with_backoff")

    def test_unknown_external_dependency_is_an_unconfirmed_assumption(self):
        claims = self.claims(SCENARIOS["unconfirmed"]["prompt"])
        self.assertEqual([c.relation for c in claims], ["unconfirmed"])
        self.assertEqual(claims[0].assumption, "The payments API exists and provides what this work needs")
        self.assertEqual(claims[0].fact_quote, "")
        # Known systems and negated mentions are not flagged.
        self.assertEqual(self.claims("Upload the daily report to the analytics lake bucket now."), [])
        self.assertEqual(self.claims("Do not call the payments API from the job."), [])

    def test_unrelated_or_negated_creation_is_not_flagged(self):
        self.assertEqual(self.claims("Create a new dashboard for marketing campaign metrics."), [])
        self.assertEqual(self.claims("Do not write a new event schema validator."), [])


class GroundingTests(AlignTestCase):
    def setUp(self):
        super().setUp()
        self.facts = candidate_facts("DE-104", "event schema validator")

    def reasons(self, claim, stage="committed", content=EVENT):
        grounded, rejected = verify(Analysis(stage, "x", [claim]), content, self.facts)
        return [r for _, r in rejected] or ["ok"] * len(grounded)

    def test_grounded_claim_passes(self):
        self.assertEqual(self.reasons(Claim("ADR-12", DECISION, EVENT, "")), ["ok"])

    def test_quotes_are_normalized(self):
        claim = Claim("ADR-12", "  do not introduce a NEW lambda ingestion service ", "create a new standalone aws lambda", "")
        self.assertEqual(self.reasons(claim), ["ok"])

    def test_rejections(self):
        self.assertEqual(self.reasons(Claim("ADR-99", DECISION, EVENT, "")), ["unknown_fact"])
        self.assertEqual(self.reasons(Claim("ADR-12", "Lambda is banned forever", EVENT, "")),
                         ["fact_quote_not_in_fact"])
        self.assertEqual(self.reasons(Claim("ADR-12", DECISION, EVENT, "", relation="dislikes")),
                         ["invalid_relation"])
        self.assertEqual(self.reasons(Claim("ADR-12", DECISION, "rewrite everything in Rust", "")),
                         ["event_quote_not_in_event"])
        self.assertEqual(self.reasons(Claim("ADR-12", DECISION, EVENT, ""), stage="exploring"), ["not_committed"])

    def test_unconfirmed_needs_quote_assumption_and_commitment(self):
        good = Claim("assumption:x", "", "Create a new standalone AWS Lambda", "", "unconfirmed", "Lambda is allowed")
        self.assertEqual(self.reasons(good), ["ok"])
        self.assertEqual(self.reasons(Claim("assumption:x", "", "made up words", "", "unconfirmed", "x")),
                         ["event_quote_not_in_event"])
        self.assertEqual(self.reasons(Claim("assumption:x", "", EVENT, "", "unconfirmed", " ")), ["missing_assumption"])
        self.assertEqual(self.reasons(good, stage="exploring"), ["not_committed"])

    def test_code_fact_quote_must_match_code_line(self):
        fact = next(f for f in self.facts if f.subject == "validate_event")
        ok = Claim(fact.id, "def validate_event(event: dict, schema: EventSchema)", EVENT, "", ALREADY_EXISTS)
        bad = Claim(fact.id, "def validate_event(event, strict=True)", EVENT, "", ALREADY_EXISTS)
        self.assertEqual(self.reasons(ok), ["ok"])
        self.assertEqual(self.reasons(bad), ["fact_quote_not_in_fact"])

    def test_expired_fact_rejected(self):
        self.facts = [replace(f, valid_until="2000-01-01") if f.id == "ADR-12" else f for f in self.facts]
        self.assertEqual(self.reasons(Claim("ADR-12", DECISION, EVENT, "")), ["fact_not_current"])
