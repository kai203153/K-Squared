import json
import time
import urllib.error
import urllib.request

from align import engine
from align.config import ensure_token

from .helpers import SCENARIOS, AlignTestCase, serve


class ServerTests(AlignTestCase):
    def setUp(self):
        super().setUp()
        self.url = serve(self)
        self.token = ensure_token()

    def req(self, method, path, body=None, token=True, host=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Align-Token"] = self.token
        if host:
            headers["Host"] = host
        r = urllib.request.Request(self.url + path, method=method, headers=headers,
                                   data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(r, timeout=5) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def post_prompt(self, prompt, session="web"):
        return self.req("POST", "/api/events", {"task_id": "DE-104", "session": session, "kind": "prompt", "content": prompt})

    def wait_analyzed(self):
        for _ in range(50):
            if not engine.pending_event_ids():
                return
            time.sleep(0.05)
        self.fail("events stayed pending")

    def test_auth_and_host_checks(self):
        self.assertEqual(self.req("GET", "/health", token=False)[0], 200)
        self.assertEqual(self.req("GET", "/api/state", token=False)[0], 401)
        self.assertEqual(self.req("GET", "/api/state", host="evil.example:8765")[0], 403)

    def test_dashboard_has_token_injected(self):
        status, html = self.req("GET", "/", token=False)
        self.assertEqual(status, 200)
        self.assertIn(self.token, html)
        self.assertNotIn("__ALIGN_TOKEN__", html)

    def test_event_flow_and_state(self):
        status, body = self.post_prompt(SCENARIOS["positive"]["prompt"])
        self.assertEqual((status, json.loads(body)["status"]), (202, "pending"))
        self.wait_analyzed()
        state = json.loads(self.req("GET", "/api/state?task=DE-104")[1])
        self.assertEqual(len(state["findings"]), 1)
        self.assertGreater(state["knowledge"]["total"], 5)
        fid = state["findings"][0]["id"]
        status, body = self.req("POST", "/api/respond", {"finding_id": fid, "response": "use_existing"})
        self.assertEqual(json.loads(body)["status"], "resolved")

    def test_bad_requests(self):
        self.assertEqual(self.req("POST", "/api/events", {"task_id": "NOPE", "content": "x"})[0], 400)
        self.assertEqual(self.req("POST", "/api/respond", {"finding_id": "x"})[0], 400)
        self.assertEqual(self.req("GET", "/api/hook?task=DE-104&session=s")[0], 400)
        self.assertEqual(self.req("GET", "/api/hook?task=DE-104&session=s&hook=stop")[0], 400)

    def hook(self, session, hook, mode="once", event_id=""):
        q = f"/api/hook?task=DE-104&session={session}&hook={hook}&mode={mode}&event_id={event_id}"
        return json.loads(self.req("GET", q)[1])

    def test_gate_once_then_allow(self):
        self.post_prompt(SCENARIOS["positive"]["prompt"], session="g")
        first = self.hook("g", "pre_tool")  # Waits for the pending analysis itself.
        self.assertEqual(first["decision"], "ask")
        self.assertIn("ADR-12", first["reason"])
        self.assertEqual(self.hook("g", "pre_tool")["decision"], "allow")
        self.assertEqual(self.hook("g", "pre_tool", mode="strict")["decision"], "ask")
        self.assertEqual(self.hook("other", "pre_tool", mode="strict")["decision"], "allow")

    def test_prompt_hook_waits_for_its_event_and_delivers_once(self):
        event_id = json.loads(self.post_prompt(SCENARIOS["duplicate"]["prompt"], session="n")[1])["event_id"]
        out = self.hook("n", "prompt", event_id=event_id)
        self.assertIn("lib/events/schema.py", out["additional_context"])
        self.assertIn("already exists", out["system_message"])
        self.assertEqual(self.hook("n", "prompt"), {"decision": "allow"})

    def test_plan_hook_sends_plan_back_when_gated(self):
        r = self.req("POST", "/api/events", {"task_id": "DE-104", "session": "p", "kind": "plan",
                                             "content": SCENARIOS["positive"]["prompt"]})
        out = self.hook("p", "plan", event_id=json.loads(r[1])["event_id"])
        self.assertEqual(out["decision"], "deny")
        self.assertIn("Revise the plan", out["reason"])

    def test_plan_hook_with_gate_off_leaves_delivery_to_claude_later(self):
        r = self.req("POST", "/api/events", {"task_id": "DE-104", "session": "q", "kind": "plan",
                                             "content": SCENARIOS["positive"]["prompt"]})
        out = self.hook("q", "plan", mode="off", event_id=json.loads(r[1])["event_id"])
        self.assertEqual(out["decision"], "allow")
        self.assertIn("ADR-12", out["system_message"])
        self.assertIn("ADR-12", self.hook("q", "post_tool", mode="off")["additional_context"])

    def test_knowledge_and_findings_routes(self):
        facts = json.loads(self.req("GET", "/api/knowledge?q=retry%20backoff")[1])
        self.assertEqual(facts[0]["subject"], "with_backoff")
        self.post_prompt(SCENARIOS["positive"]["prompt"], session="f")
        self.wait_analyzed()
        self.assertEqual([f["fact_id"] for f in json.loads(self.req("GET", "/api/findings?task=DE-104")[1])], ["ADR-12"])
