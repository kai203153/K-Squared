import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from align import cli, engine

from .helpers import SCENARIOS, AlignTestCase, serve

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "align_hook.py"


class HookTests(AlignTestCase):
    def setUp(self):
        super().setUp()
        self.url = serve(self)

    def hook(self, kind, envelope, gate="off", url=None):
        env = {**os.environ, "ALIGN_TASK_ID": "DE-104", "ALIGN_GATE": gate, "ALIGN_URL": url or self.url}
        p = subprocess.run([sys.executable, str(HOOK), kind], input=json.dumps(envelope),
                           capture_output=True, text=True, env=env, timeout=10)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout.strip()

    def events(self):
        for _ in range(50):
            if not engine.pending_event_ids():
                break
            time.sleep(0.05)
        return engine.snapshot("DE-104")["events"]

    def test_prompt_is_captured_and_redacted(self):
        out = self.hook("prompt", {"session_id": "h", "prompt": "Deploy it, api_key=sk-123 please"})
        self.assertEqual(out, "")
        self.assertEqual(self.events()[0]["content"], "Deploy it, api_key=[REDACTED] please")

    def test_tool_capture_is_metadata_only(self):
        self.hook("post_tool", {"session_id": "h", "tool_name": "Write",
                                "tool_input": {"file_path": "/secret/dir/app.py", "content": "print('code')"}})
        self.assertEqual(self.events()[0]["content"], "Write app.py")

    def test_gate_asks_then_allows(self):
        self.hook("prompt", {"session_id": "g", "prompt": SCENARIOS["positive"]["prompt"]})
        tool = {"session_id": "g", "tool_name": "Bash", "tool_input": {"command": "sam deploy"}}
        out = json.loads(self.hook("pre_tool", tool, gate="once"))["hookSpecificOutput"]
        self.assertEqual((out["hookEventName"], out["permissionDecision"]), ("PreToolUse", "ask"))
        self.assertIn("ADR-12", out["permissionDecisionReason"])
        self.assertEqual(self.hook("pre_tool", tool, gate="once"), "")
        self.assertNotIn("sam deploy", json.dumps(self.events()))

    def test_gate_off_never_prints(self):
        self.hook("prompt", {"session_id": "o", "prompt": SCENARIOS["positive"]["prompt"]})
        self.assertEqual(self.hook("pre_tool", {"session_id": "o", "tool_name": "Write", "tool_input": {}}), "")

    def test_fails_open_when_server_down(self):
        out = self.hook("pre_tool", {"session_id": "d", "tool_name": "Write", "tool_input": {}},
                        gate="strict", url="http://127.0.0.1:9")
        self.assertEqual(out, "")

    def test_garbage_stdin_is_ignored(self):
        p = subprocess.run([sys.executable, str(HOOK), "prompt"], input="not json", capture_output=True, text=True)
        self.assertEqual((p.returncode, p.stdout), (0, ""))


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
        self.assertEqual(len(config["hooks"]["UserPromptSubmit"]), 1)
