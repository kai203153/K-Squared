from align import context
from align.detect import Analysis, Claim
from align.detect.grounding import verify
from align.detect.rules import RulesDetector
from align.detect.stage import classify

from .helpers import SCENARIOS, AlignTestCase

DECISION = "Do not introduce a new Lambda ingestion service without an approved exception."
EVENT = "Create a new standalone AWS Lambda ingestion service now."


class StageTests(AlignTestCase):
    def test_demo_scenarios(self):
        stages = {k: classify(v["prompt"]) for k, v in SCENARIOS.items()}
        self.assertEqual(stages, {"negative": "exploring", "positive": "committed",
                                  "corrective": "exploring", "legitimate": "exploring"})

    def test_negated_execute_is_not_commitment(self):
        self.assertNotEqual(classify("Do not create anything yet."), "committed")
        self.assertEqual(classify("Compare both, then go ahead and implement the cache."), "committed")
        self.assertEqual(classify("Write config.yml", "pre_tool"), "committed")


class RulesTests(AlignTestCase):
    def setUp(self):
        super().setUp()
        self.records = context.records_for("DE-104", authoritative_only=True)

    def claims(self, text, kind="prompt"):
        return RulesDetector().analyze(text, kind, self.records).claims

    def test_forbid_and_negated_require(self):
        self.assertEqual([c.record_id for c in self.claims(SCENARIOS["positive"]["prompt"])], ["ADR-12"])
        self.assertEqual(len(self.claims("Ship it. Do not reuse the Glue pipeline.")), 1)

    def test_negated_forbid_is_not_a_claim(self):
        self.assertEqual(self.claims("Extend Glue. Do not create a new Lambda service."), [])

    def test_tool_filenames(self):
        claims = self.claims("Write lambda_handler.py", "pre_tool")
        self.assertEqual(claims[0].event_quote, "Write lambda_handler.py")
        self.assertEqual(self.claims("Edit glue_job.py", "pre_tool"), [])


class GroundingTests(AlignTestCase):
    def setUp(self):
        super().setUp()
        self.records = context.records_for("DE-104", authoritative_only=True)

    def reasons(self, claim, stage="committed", content=EVENT):
        grounded, rejected = verify(Analysis(stage, "x", [claim]), content, self.records)
        return [r for _, r in rejected] or ["ok"] * len(grounded)

    def test_grounded_claim_passes(self):
        self.assertEqual(self.reasons(Claim("ADR-12", DECISION, EVENT, "")), ["ok"])

    def test_quotes_are_normalized(self):
        claim = Claim("ADR-12", "  do not introduce a NEW lambda ingestion service ", "create a new standalone aws lambda", "")
        self.assertEqual(self.reasons(claim), ["ok"])

    def test_rejections(self):
        self.assertEqual(self.reasons(Claim("ADR-99", DECISION, EVENT, "")), ["unknown_record"])
        self.assertEqual(self.reasons(Claim("ADR-12", "Lambda is banned forever", EVENT, "")),
                         ["decision_quote_not_in_record"])
        self.assertEqual(self.reasons(Claim("ADR-12", DECISION, "rewrite everything in Rust", "")),
                         ["event_quote_not_in_event"])
        self.assertEqual(self.reasons(Claim("ADR-12", DECISION, EVENT, ""), stage="exploring"), ["not_committed"])

    def test_non_authoritative_record_rejected(self):
        proposed = [{**self.records[0], "status": "proposed"}]
        _, rejected = verify(Analysis("committed", "x", [Claim("ADR-12", DECISION, EVENT, "")]), EVENT, proposed)
        self.assertEqual(rejected[0][1], "record_not_authoritative")
