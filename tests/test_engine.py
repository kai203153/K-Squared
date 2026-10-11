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
                self.assertEqual(run(sc["prompt"], session=name)["result"] == "finding", sc["expect_finding"])

    def test_finding_fields_and_exposure(self):
        run(POSITIVE)
        f = engine.snapshot("DE-104")["findings"][0]
        self.assertEqual((f["fact_id"], f["severity"], f["status"], f["relation"]), ("ADR-12", "HIGH", "open", "contradicted"))
        self.assertIn("Lambda", f["event_quote"])
        self.assertEqual((f["impact_min"], f["impact_max"]), (2.26, 4.52))

    def test_existing_code_finding_is_low_with_no_exposure(self):
        run(SCENARIOS["duplicate"]["prompt"])
        f = engine.snapshot("DE-104")["findings"][0]
        self.assertEqual((f["severity"], f["relation"], f["fact_kind"]), ("LOW", "already_exists", "component"))
        self.assertEqual((f["impact_min"], f["impact_max"]), (0, 0))
        self.assertEqual(f["fact_source"], "lib/events/schema.py:13")
        self.assertIn("lib/events/schema.py", engine.respond(f["id"], "ask_manager")["draft"])

    def test_plan_events_are_committed(self):
        result = run("1. Add a new event schema validator module.\n2. Wire it into the job.", kind="plan")
        self.assertEqual((result["stage"], result["result"]), ("committed", "finding"))

    def test_new_findings_pop_up(self):
        with mock.patch("align.notify.notify_finding") as popup:
            run(POSITIVE)
            run(POSITIVE)  # Deduplicated: no second pop-up.
        self.assertEqual(popup.call_count, 1)
        self.assertEqual(popup.call_args[0][0]["severity"], "HIGH")

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

    def test_open_findings_sorted_by_severity(self):
        run(SCENARIOS["duplicate"]["prompt"])
        run(POSITIVE)
        self.assertEqual([f["severity"] for f in engine.open_findings("DE-104", "s1")], ["HIGH", "LOW"])


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
        fake = self.use(FakeClaude({"stage": "committed", "statement": "Build a Lambda service", "assumptions": [
            {"assumption": "A new Lambda service is allowed", "relation": "contradicted", "fact_id": "ADR-12",
             "fact_quote": "Do not introduce a new Lambda ingestion service without an approved exception.",
             "event_quote": "Create a new standalone AWS Lambda ingestion service for customer events.",
             "explanation": "New Lambda service instead of Glue."},
            {"assumption": "Events arrive as JSON", "relation": "unknown", "fact_id": "", "fact_quote": "",
             "event_quote": "customer events", "explanation": ""}]}))
        result = run(POSITIVE)
        self.assertEqual((result["result"], result["detector"]), ("finding", "claude"))
        self.assertEqual(result["rejected"], [])  # "unknown" assumptions are not claims at all.
        call = fake.calls[0]
        self.assertEqual(call["model"], "claude-opus-5-5")
        self.assertEqual(call["output_config"]["format"]["type"], "json_schema")
        self.assertIn("ADR-12", call["system"][1]["text"])
        self.assertIn(POSITIVE, call["messages"][0]["content"])
        f = engine.snapshot("DE-104")["findings"][0]
        self.assertEqual(f["assumption"], "A new Lambda service is allowed")

    def test_claude_sees_related_production_code(self):
        fake = self.use(FakeClaude({"stage": "committed", "statement": "x", "assumptions": []}))
        run(SCENARIOS["duplicate"]["prompt"])
        self.assertIn("def validate_event", fake.calls[0]["system"][1]["text"])

    def test_hallucinated_claude_claim_is_not_a_finding(self):
        self.use(FakeClaude({"stage": "committed", "statement": "x", "assumptions": [{
            "assumption": "x", "relation": "contradicted", "fact_id": "ADR-12",
            "fact_quote": "All new code must be written in Scala.",
            "event_quote": "Create a new standalone AWS Lambda", "explanation": "made up"}]}))
        result = run(POSITIVE)
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["rejected"], [{"fact_id": "ADR-12", "reason": "fact_quote_not_in_fact"}])

    def test_claude_failure_falls_back_to_rules(self):
        self.use(FakeClaude(error=TimeoutError("slow")))
        result = run(POSITIVE)
        self.assertEqual((result["result"], result["detector"]), ("finding", "rules"))
        self.assertEqual(result["notes"], ["claude_failed: TimeoutError"])

    def test_refusal_falls_back_to_rules(self):
        self.use(FakeClaude({}, stop_reason="refusal"))
        self.assertEqual(run(POSITIVE)["detector"], "rules")

    def test_tool_events_never_call_claude(self):
        fake = self.use(FakeClaude({"stage": "none", "statement": "", "assumptions": []}))
        run("Write lambda_handler.py", kind="pre_tool")
        self.assertEqual(fake.calls, [])


class AutoModeTests(AlignTestCase):
    env = {"ALIGN_DETECTOR": "auto"}

    def test_auto_without_sdk_uses_rules(self):
        with mock.patch.object(claude_mod.ClaudeDetector, "available", lambda self: False):
            self.assertEqual(run(POSITIVE)["detector"], "rules")


class TeamTests(AlignTestCase):
    def status(self, name):
        return next(e for e in engine.team("DE-104")["engineers"] if e["name"] == name)

    def send(self, content, engineer, kind="prompt"):
        eid = engine.ingest({"task_id": "DE-104", "session": engineer, "kind": kind,
                             "content": content, "engineer": engineer})
        return engine.analyze_event(eid)

    def test_status_follows_findings_and_responses(self):
        self.send(SCENARIOS["duplicate"]["prompt"], "Sam Okafor")
        sam = self.status("Sam Okafor")
        self.assertEqual(sam["status"], "flagged")
        engine.respond(sam["findings"][0]["id"], "use_existing")
        self.assertEqual(self.status("Sam Okafor")["status"], "aligned")

        self.send(SCENARIOS["retry"]["prompt"], "Priya Nair", kind="plan")
        engine.respond(self.status("Priya Nair")["findings"][0]["id"], "new_evidence", "jitter needed")
        self.assertEqual(self.status("Priya Nair")["status"], "needs_review")

    def test_direction_prefers_prompt_over_tool_call(self):
        self.send(POSITIVE, "Alex Rivera")
        self.send("Write lambda_handler.py", "Alex Rivera", kind="pre_tool")
        alex = self.status("Alex Rivera")
        self.assertIn("Lambda ingestion service", alex["direction"])
        self.assertEqual(len(alex["findings"]), 1)  # The tool call re-matches the same open finding.
        self.assertEqual(alex["exposure"], 4.52)

    def test_unassigned_engineer_still_appears(self):
        self.send(SCENARIOS["negative"]["prompt"], "Contractor")
        e = self.status("Contractor")
        self.assertEqual((e["assigned"], e["status"]), (False, "exploring"))
