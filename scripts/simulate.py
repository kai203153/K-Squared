"""Send realistic Claude Code hook envelopes through the real hook script.

    python3 scripts/simulate.py positive              # UserPromptSubmit
    python3 scripts/simulate.py positive --tool lambda_handler.py --gate once   # then a PreToolUse

Requires `python3 -m align serve` to be running. Does not need Claude Code.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "align_hook.py"


def run_hook(kind: str, envelope: dict, env: dict) -> str:
    p = subprocess.run([sys.executable, str(HOOK), kind], input=json.dumps(envelope), text=True,
                       capture_output=True, env=env)
    return p.stdout.strip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("--example", default="de-104")
    ap.add_argument("--task", default="DE-104")
    ap.add_argument("--tool", help="also send a PreToolUse Write for this file name")
    ap.add_argument("--gate", default="off", choices=("off", "once", "strict"))
    args = ap.parse_args()

    scenarios = json.loads((ROOT / "examples" / args.example / "scenarios.json").read_text())
    if args.scenario not in scenarios:
        sys.exit("Options: " + ", ".join(scenarios))
    prompt = scenarios[args.scenario]["prompt"]
    session = "sim-" + args.scenario
    env = {**os.environ, "ALIGN_TASK_ID": args.task, "ALIGN_GATE": args.gate}

    run_hook("prompt", {"hook_event_name": "UserPromptSubmit", "session_id": session,
                        "cwd": os.getcwd(), "prompt": prompt}, env)
    print(f"Scenario: {args.scenario}\nPrompt:   {prompt}")
    if args.tool:
        out = run_hook("pre_tool", {"hook_event_name": "PreToolUse", "session_id": session,
                                    "tool_name": "Write", "tool_input": {"file_path": f"/repo/{args.tool}", "content": "..."}}, env)
        print(f"PreToolUse Write {args.tool} -> {out or '(no output: allowed)'}")
    print("Dashboard:", os.environ.get("ALIGN_URL", "http://127.0.0.1:8765"))


if __name__ == "__main__":
    main()
