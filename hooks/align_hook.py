#!/usr/bin/env python3
"""Claude Code hook for Align. Standalone: stdlib only, no imports from the align package.

Usage (from .claude/settings.json): align_hook.py prompt|pre_tool|post_tool

Captures the prompt text (redacted) and, for tools, only the tool name and file basename:
never code bodies, commands, or transcripts. Fails open: any error exits 0 silently.
Writes to stdout only for the opt-in PreToolUse gate (ALIGN_GATE=once|strict).
"""
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

URL = os.environ.get("ALIGN_URL", "http://127.0.0.1:8765")
HOME = Path(os.environ.get("ALIGN_HOME", Path(__file__).resolve().parents[1] / ".align"))
SECRET = re.compile(r"(?i)(api[_-]?key|password|passwd|secret|token)\s*[:=]\s*\S+")


def token() -> str:
    return os.environ.get("ALIGN_TOKEN") or (HOME / "token").read_text().strip()


def content_for(kind: str, data: dict) -> str:
    if kind == "prompt":
        text = str(data.get("prompt", ""))
    else:
        inp = data.get("tool_input") or {}
        path = str(inp.get("file_path") or inp.get("notebook_path") or inp.get("path") or "")
        text = f"{data.get('tool_name', '')} {os.path.basename(path)}".strip()
    return SECRET.sub(r"\1=[REDACTED]", text)


def call(method: str, path: str, body: dict | None = None, timeout: float = 1.0) -> dict:
    req = urllib.request.Request(
        URL + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Content-Type": "application/json", "X-Align-Token": token()},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def main() -> None:
    kind = sys.argv[1] if len(sys.argv) > 1 else "prompt"
    data = json.load(sys.stdin)
    task = os.environ.get("ALIGN_TASK_ID", "")
    session = str(data.get("session_id", "unknown"))
    content = content_for(kind, data)
    if not task or not content:
        return
    try:
        call("POST", "/api/events", {"task_id": task, "session": session, "kind": kind, "content": content})
    except Exception:
        pass  # Observation is best-effort; the gate below may still answer.

    mode = os.environ.get("ALIGN_GATE", "off")
    if kind != "pre_tool" or mode == "off":
        return
    query = urllib.parse.urlencode({"task": task, "session": session, "mode": mode})
    result = call("GET", f"/api/gate?{query}", timeout=3.5)
    if result.get("decision") == "ask":
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": result["reason"],
            }
        }))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # Fail open: never block Claude because Align is unavailable.
    sys.exit(0)
