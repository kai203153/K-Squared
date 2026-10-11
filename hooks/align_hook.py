#!/usr/bin/env python3
"""Claude Code hook for Align. Standalone: stdlib only, no imports from the align package.

Usage (from .claude/settings.json): align_hook.py prompt|pre_tool|post_tool

Captures the prompt text and finished plans (redacted) and, for other tools, only the tool
name and file basename: never code bodies or transcripts. Shell commands are captured only
with ALIGN_CAPTURE_COMMANDS=1. Then asks Align whether there are assumptions to raise and
prints the matching hook JSON. Fails open: any error exits 0 silently.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

URL = os.environ.get("ALIGN_URL", "http://127.0.0.1:8765")
HOME = Path(os.environ.get("ALIGN_HOME", Path(__file__).resolve().parents[1] / ".align"))
# Who is working, for the manager view. Read at run time so a shared settings.json works for everyone.
ENGINEER = os.environ.get("ALIGN_ENGINEER") or os.environ.get("USER") or "unknown"
NUDGE_WAIT = float(os.environ.get("ALIGN_NUDGE_WAIT", "10"))
SECRET = re.compile(r"(?i)(api[_-]?key|password|passwd|secret|token)\s*[:=]\s*\S+")
EVENT_NAMES = {"prompt": "UserPromptSubmit", "plan": "PreToolUse", "pre_tool": "PreToolUse",
               "post_tool": "PostToolUse"}


def token() -> str:
    return os.environ.get("ALIGN_TOKEN") or (HOME / "token").read_text().strip()


def classify(hook: str, data: dict) -> tuple[str, str]:
    """Return (event kind, content to capture). An empty content means capture nothing."""
    if hook == "prompt":
        return "prompt", str(data.get("prompt", ""))
    tool = str(data.get("tool_name", ""))
    inp = data.get("tool_input") or {}
    if tool == "ExitPlanMode":
        # The plan is captured once, before approval; afterwards only deliver findings.
        return ("plan", str(inp.get("plan", ""))) if hook == "pre_tool" else ("post_tool", "")
    if tool == "Bash" and os.environ.get("ALIGN_CAPTURE_COMMANDS") == "1":
        return hook, f"Bash {str(inp.get('command', ''))[:300]}"
    path = str(inp.get("file_path") or inp.get("notebook_path") or inp.get("path") or "")
    return hook, f"{tool} {os.path.basename(path)}".strip()


def call(method: str, path: str, body: dict | None = None, timeout: float = 1.0) -> dict:
    req = urllib.request.Request(
        URL + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Content-Type": "application/json", "X-Align-Token": token()},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def output(kind: str, result: dict) -> dict | None:
    out: dict = {}
    if result.get("system_message"):
        out["systemMessage"] = result["system_message"]
    specific: dict = {"hookEventName": EVENT_NAMES[kind]}
    if result.get("decision") in ("ask", "deny"):
        specific.update(permissionDecision=result["decision"], permissionDecisionReason=result["reason"])
    if result.get("additional_context"):
        specific["additionalContext"] = result["additional_context"]
    if len(specific) > 1:
        out["hookSpecificOutput"] = specific
    return out or None


def main() -> None:
    hook = sys.argv[1] if len(sys.argv) > 1 else "prompt"
    data = json.load(sys.stdin)
    task = os.environ.get("ALIGN_TASK_ID", "")
    if not task:
        return
    session = str(data.get("session_id", "unknown"))
    kind, content = classify(hook, data)
    content = SECRET.sub(r"\1=[REDACTED]", content)

    event_id = ""
    if content:
        try:
            event_id = call("POST", "/api/events", {"task_id": task, "session": session, "kind": kind,
                                                    "content": content, "engineer": ENGINEER}).get("event_id", "")
        except Exception:
            pass  # Observation is best-effort; earlier findings can still be delivered.

    slow = kind in ("prompt", "plan")
    query = urllib.parse.urlencode({"task": task, "session": session, "hook": kind,
                                    "mode": os.environ.get("ALIGN_GATE", "off"), "event_id": event_id})
    result = call("GET", f"/api/hook?{query}", timeout=NUDGE_WAIT + 2 if slow else 3.5)
    out = output(kind, result)
    if out:
        print(json.dumps(out))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # Fail open: never block Claude because Align is unavailable.
    sys.exit(0)
