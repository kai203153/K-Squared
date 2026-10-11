import os
from unittest import mock

from align import engine
from align.detect import claude as claude_mod

from .helpers import SCENARIOS, AlignTestCase, FakeClaude

POSITIVE = SCENARIOS["positive"]["prompt"]


def run(content, session="s1", kind="prompt"):
    eid = engine.ingest({"task_id": "DE-104", "session": session, "kind": kind, "content": content})
    return engine.analyze_event(eid)


class EngineTests(AlignTestCase):
    def test_scenarios_match_expectations(self):
        for name, sc in SCENARIOS.items():
            with self.subTest(name):
                self.assertEqual(run(sc["prompt"], session=name)["result"] == "conflict", sc["expect_finding"])

    def test_finding_fields_and_exposure(self):
        run(POSITIVE)
        f = engine.snapshot("DE-104")["findings"][0]
        self.assertEqual((f["record_id"], f["severity"], f["status"]), ("ADR-12", "HIGH", "open"))
        self.assertIn("Lambda", f["event_quote"])
        self.assertEqual((f["impact_min"], f["impact_max"]), (2.26, 4.52))

    def test_open_findings_deduplicated_per_record_and_session(self):
        first = run(POSITIVE)["findings"]
        self.assertEqual(run("Write lambda_handler.py", kind="pre_tool")["findings"], first)
        self.assertNotEqual(run(POSITIVE, session="other")["findings"], first)

    def test_ingest_validation(self):
        for bad in ({"task_id": "NOPE", "content": "x"}, {"task_id": "DE-104", "content": " "},
                    {"task_id": "DE-104", "kind": "stop", "content": "x"}):
            with self.assertRaises(ValueError):
                engine.ingest(bad)

    def test_post_tool_ignored(self):
        self.assertEqual(run("Write lambda_handler.py", kind="post_tool")["result"], "ignored_non_decision_event")

    def test_responses(self):
        fid = run(POSITIVE)["findings"][0]
        out = engine.respond(fid, "ask_manager", "need latency numbers")
        self.assertEqual(out["status"], "needs_review")
        self.assertIn("ADR-12", out["draft"])
        self.assertEqual(engine.respond(fid, "use_existing")["status"], "resolved")
        with self.assertRaises(ValueError):
            engine.respond(fid, "escalate")
        with self.assertRaises(ValueError):
            engine.respond("missing", "dismiss")
        # A resolved finding no longer deduplicates new conflicts.
        self.assertNotEqual(run(POSITIVE)["findings"], [fid])

    def test_dismissals_counted_per_record(self):
        engine.respond(run(POSITIVE)["findings"][0], "dismiss")
        adr = next(r for r in engine.snapshot("DE-104")["records"] if r["id"] == "ADR-12")
        self.assertEqual(adr["dismissals"], 1)


class ClaudeDetectorTests(AlignTestCase):
    env = {"ALIGN_DETECTOR": "claude"}

    def use(self, fake):
        p = mock.patch.object(claude_mod.ClaudeDetector, "client", lambda self: fake)
        p.start()
        self.addCleanup(p.stop)
        a = mock.patch.object(claude_mod.ClaudeDetector, "available", lambda self: True)
        a.start()
        self.addCleanup(a.stop)
        return fake

    def test_grounded_claude_claim_becomes_finding(self):
        fake = self.use(FakeClaude({"stage": "committed", "statement": "Build a Lambda service", "conflicts": [{
            "record_id": "ADR-12",
            "decision_quote": "Do not introduce a new Lambda ingestion service without an approved exception.",
            "event_quote": "Create a new standalone AWS Lambda ingestion service for customer events.",
            "explanation": "New Lambda service instead of Glue."}]}))
        result = run(POSITIVE)
        self.assertEqual((result["result"], result["detector"]), ("conflict", "claude"))
        call = fake.calls[0]
        self.assertEqual(call["model"], "claude-opus-5-5")
        self.assertEqual(call["output_config"]["format"]["type"], "json_schema")
        self.assertIn("ADR-12", call["system"][1]["text"])
        self.assertIn(POSITIVE, call["messages"][0]["content"])

    def test_hallucinated_claude_claim_is_not_a_finding(self):
        self.use(FakeClaude({"stage": "committed", "statement": "x", "conflicts": [{
            "record_id": "ADR-12", "decision_quote": "All new code must be written in Scala.",
            "event_quote": "Create a new standalone AWS Lambda", "explanation": "made up"}]}))
        result = run(POSITIVE)
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["rejected"], [{"record_id": "ADR-12", "reason": "decision_quote_not_in_record"}])

    def test_claude_failure_falls_back_to_rules(self):
        self.use(FakeClaude(error=TimeoutError("slow")))
        result = run(POSITIVE)
        self.assertEqual((result["result"], result["detector"]), ("conflict", "rules"))
        self.assertEqual(result["notes"], ["claude_failed: TimeoutError"])

    def test_refusal_falls_back_to_rules(self):
        self.use(FakeClaude({}, stop_reason="refusal"))
        self.assertEqual(run(POSITIVE)["detector"], "rules")

    def test_tool_events_never_call_claude(self):
        fake = self.use(FakeClaude({"stage": "none", "statement": "", "conflicts": []}))
        run("Write lambda_handler.py", kind="pre_tool")
        self.assertEqual(fake.calls, [])


class AutoModeTests(AlignTestCase):
    env = {"ALIGN_DETECTOR": "auto"}

    def test_auto_without_sdk_uses_rules(self):
        with mock.patch.object(claude_mod.ClaudeDetector, "available", lambda self: False):
            self.assertEqual(run(POSITIVE)["detector"], "rules")
