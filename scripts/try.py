"""Type prompts as if you were the engineer, and watch Align react: no Claude Code needed.

    python3 scripts/try.py              # requires `scripts/demo.sh` running in another terminal

Each line goes through the real hook script. You'll see what the engineer and Claude would be
told, and the desktop pop-up fires by severity (LOW banner, HIGH alert with actions).
"""
import os
import sys
import uuid

from simulate import run_hook, show

EXAMPLES = [
    "Compare extending the existing Glue pipeline versus a Lambda service. Explain tradeoffs, don't modify files.",
    "Write a new event schema validator for the order events and wire it into ingestion.",
    "Implement a custom retry helper with backoff for the order event writer.",
    "Create a new standalone AWS Lambda ingestion service for customer events. Implement the handler now.",
]


def main() -> None:
    session = "try-" + uuid.uuid4().hex[:6]
    env = {**os.environ, "ALIGN_TASK_ID": os.environ.get("ALIGN_TASK_ID", "DE-104")}
    print(f"Align playground (session {session}). Type a prompt, a number for an example, or q to quit.")
    for i, text in enumerate(EXAMPLES, 1):
        print(f"  {i}. {text}")
    while True:
        try:
            line = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if line.lower() in ("q", "quit", "exit"):
            return
        if line.isdigit() and 1 <= int(line) <= len(EXAMPLES):
            line = EXAMPLES[int(line) - 1]
            print(f"you> {line}")
        if line:
            show("Align", run_hook("prompt", {"session_id": session, "prompt": line}, env))


if __name__ == "__main__":
    sys.exit(main())
