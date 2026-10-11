import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from align import cli, engine

from .helpers import SCENARIOS, AlignTestCase, serve

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
HOOK = HOOKS / "align_hook.py"
PRECOMMIT = HOOKS / "align_precommit.py"


class HookTestCase(AlignTestCase):
    def setUp(self):
        super().setUp()
        self.url = serve(self)

    def run_script(self, script, args, stdin="", gate="off", url=None):
        env = {**os.environ, "ALIGN_TASK_ID": "DE-104", "ALIGN_GATE": gate, "ALIGN_URL": url or self.url}
        return subprocess.run([sys.executable, str(script), *args], input=stdin, capture_output=True,
                              text=True, env=env, timeout=20)

    def hook(self, kind, envelope, gate="off", url=None):
        p = self.run_script(HOOK, [kind], json.dumps(envelope), gate, url)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout) if p.stdout.strip() else None

    def events(self):
        for _ in range(50):
            if not engine.pending_event_ids():
                break
            time.sleep(0.05)
        return engine.snapshot("DE-104")["events"]


class HookTests(HookTestCase):
    def test_prompt_is_redacted_and_quiet_without_findings(self):
        self.assertIsNone(self.hook("prompt", {"session_id": "h", "prompt": "Deploy it, api_key=sk-123 please"}))
        self.assertEqual(self.events()[0]["content"], "Deploy it, api_key=[REDACTED] please")

    def test_prompt_with_assumption_tells_claude_and_engineer(self):
        out = self.hook("prompt", {"session_id": "a", "prompt": SCENARIOS["duplicate"]["prompt"]})
        self.assertIn("already exists", out["systemMessage"])
        specific = out["hookSpecificOutput"]
        self.assertEqual(specific["hookEventName"], "UserPromptSubmit")
        self.assertIn("validate_event", specific["additionalContext"])
        self.assertNotIn("permissionDecision", specific)

    def test_tool_capture_is_metadata_only(self):
        self.hook("post_tool", {"session_id": "h", "tool_name": "Write",
                                "tool_input": {"file_path": "/secret/dir/app.py", "content": "print('code')"}})
        self.assertEqual(self.events()[0]["content"], "Write app.py")

    def test_gate_asks_then_allows(self):
        self.hook("prompt", {"session_id": "g", "prompt": SCENARIOS["positive"]["prompt"]})
        read = {"session_id": "g", "tool_name": "Bash", "tool_input": {"command": "cat docs/ADR-12.md"}}
        self.assertIsNone(self.hook("pre_tool", read, gate="once"))  # Reading the evidence is never paused.
        tool = {"session_id": "g", "tool_name": "Write", "tool_input": {"file_path": "handler.py"}}
        out = self.hook("pre_tool", tool, gate="once")["hookSpecificOutput"]
        self.assertEqual((out["hookEventName"], out["permissionDecision"]), ("PreToolUse", "ask"))
        self.assertIn("ADR-12", out["permissionDecisionReason"])
        self.assertIsNone(self.hook("pre_tool", tool, gate="once"))
        self.assertNotIn("cat docs", json.dumps(self.events()))

    def test_plan_is_captured_and_sent_back(self):
        plan = {"session_id": "p", "tool_name": "ExitPlanMode", "tool_input": {"plan": SCENARIOS["retry"]["prompt"]}}
        out = self.hook("pre_tool", plan, gate="once")
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("with_backoff", out["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertEqual(self.events()[0]["kind"], "plan")
        # The revised plan is not blocked again for the same finding.
        self.assertIsNone(self.hook("pre_tool", plan, gate="once"))

    def test_findings_from_tool_events_reach_claude_after_the_tool(self):
        self.hook("pre_tool", {"session_id": "t", "tool_name": "Write", "tool_input": {"file_path": "lambda_handler.py"}})
        out = self.hook("post_tool", {"session_id": "t", "tool_name": "Write", "tool_input": {"file_path": "lambda_handler.py"}})
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "PostToolUse")
        self.assertIn("ADR-12", out["hookSpecificOutput"]["additionalContext"])

    def test_gate_off_never_asks(self):
        self.hook("prompt", {"session_id": "o", "prompt": SCENARIOS["positive"]["prompt"]})
        self.assertIsNone(self.hook("pre_tool", {"session_id": "o", "tool_name": "Write", "tool_input": {}}))

    def test_fails_open_when_server_down(self):
        out = self.hook("prompt", {"session_id": "d", "prompt": SCENARIOS["positive"]["prompt"]},
                        gate="strict", url="http://127.0.0.1:9")
        self.assertIsNone(out)

    def test_garbage_stdin_is_ignored(self):
        p = subprocess.run([sys.executable, str(HOOK), "prompt"], input="not json", capture_output=True, text=True)
        self.assertEqual((p.returncode, p.stdout), (0, ""))


class PrecommitTests(HookTestCase):
    def test_warns_and_blocks_only_in_strict(self):
        self.assertEqual(self.run_script(PRECOMMIT, []).returncode, 0)
        self.hook("prompt", {"session_id": "c", "prompt": SCENARIOS["positive"]["prompt"]})
        p = self.run_script(PRECOMMIT, [])
        self.assertEqual(p.returncode, 0)
        self.assertIn("[HIGH] ADR-12", p.stderr)
        self.assertEqual(self.run_script(PRECOMMIT, [], gate="strict").returncode, 1)
        self.assertEqual(self.run_script(PRECOMMIT, [], gate="strict", url="http://127.0.0.1:9").returncode, 0)


class InstallHooksTests(AlignTestCase):
    def test_merge_preserves_other_hooks_and_replaces_align(self):
        repo = Path(tempfile.mkdtemp(dir=self.home))
        settings = repo / ".claude" / "settings.json"
        settings.parent.mkdir()
        other = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo mine"}]}
        settings.write_text(json.dumps({"model": "x", "hooks": {"PreToolUse": [other]}}))
        cli.install_hooks(repo, "DE-104", "off")
        cli.install_hooks(repo, "DE-104", "once")
        config = json.loads(settings.read_text())
        self.assertEqual(config["model"], "x")
        pre = config["hooks"]["PreToolUse"]
        self.assertEqual(pre[0], other)
        self.assertEqual(len(pre), 2)
        self.assertIn("ALIGN_GATE=once", pre[1]["hooks"][0]["command"])
        self.assertIn("ExitPlanMode", pre[1]["matcher"])
        self.assertEqual(len(config["hooks"]["UserPromptSubmit"]), 1)

    def test_precommit_install_respects_existing_hooks(self):
        repo = Path(tempfile.mkdtemp(dir=self.home))
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        path = cli.install_precommit(repo, "DE-104", "strict")
        self.assertIn("ALIGN_GATE=strict", path.read_text())
        cli.install_precommit(repo, "DE-104", "off")  # Re-install over our own hook is fine.
        path.write_text("#!/bin/sh\necho theirs\n")
        with self.assertRaises(ValueError):
            cli.install_precommit(repo, "DE-104", "off")
        cli.install_precommit(repo, "DE-104", "off", force=True)
