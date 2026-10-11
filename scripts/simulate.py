"""Send realistic Claude Code hook envelopes through the real hook script.

    python3 scripts/simulate.py duplicate             # UserPromptSubmit: shows what Claude is told
    python3 scripts/simulate.py positive --tool lambda_handler.py --gate once   # then a PreToolUse
    python3 scripts/simulate.py retry --plan --gate once   # send the scenario as a finished plan

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


def show(label: str, out: str) -> None:
    if not out:
        print(f"{label} -> (no output: nothing to raise)")
        return
    data = json.loads(out)
    specific = data.get("hookSpecificOutput", {})
    print(f"{label} ->")
    if data.get("systemMessage"):
        print(f"  engineer sees: {data['systemMessage']}")
    if specific.get("permissionDecision"):
        print(f"  decision: {specific['permissionDecision']}: {specific['permissionDecisionReason'][:300]}")
    if specific.get("additionalContext"):
        print("  Claude is told:\n    " + specific["additionalContext"].replace("\n", "\n    "))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("--example", default="de-104")
    ap.add_argument("--task", default="DE-104")
    ap.add_argument("--tool", help="also send a PreToolUse Write for this file name")
    ap.add_argument("--gate", default="off", choices=("off", "once", "strict"))
    ap.add_argument("--plan", action="store_true", help="send the text as an ExitPlanMode plan instead of a prompt")
    args = ap.parse_args()

    scenarios = json.loads((ROOT / "examples" / args.example / "scenarios.json").read_text())
    if args.scenario not in scenarios:
        sys.exit("Options: " + ", ".join(scenarios))
    prompt = scenarios[args.scenario]["prompt"]
    session = "sim-" + args.scenario
    env = {**os.environ, "ALIGN_TASK_ID": args.task, "ALIGN_GATE": args.gate}

    print(f"Scenario: {args.scenario}\n{'Plan' if args.plan else 'Prompt'}:   {prompt}")
    if args.plan:
        out = run_hook("pre_tool", {"hook_event_name": "PreToolUse", "session_id": session,
                                    "tool_name": "ExitPlanMode", "tool_input": {"plan": prompt}}, env)
        show("PreToolUse ExitPlanMode", out)
    else:
        out = run_hook("prompt", {"hook_event_name": "UserPromptSubmit", "session_id": session,
                                  "cwd": os.getcwd(), "prompt": prompt}, env)
        show("UserPromptSubmit", out)
    if args.tool:
        out = run_hook("pre_tool", {"hook_event_name": "PreToolUse", "session_id": session,
                                    "tool_name": "Write", "tool_input": {"file_path": f"/repo/{args.tool}", "content": "..."}}, env)
        show(f"PreToolUse Write {args.tool}", out)
    print("Dashboard:", os.environ.get("ALIGN_URL", "http://127.0.0.1:8765"))


if __name__ == "__main__":
    main()
