"""Play a team working on DE-104 through the real API, so the manager view fills in live.

    python3 scripts/team_demo.py              # one step every 3 s; open http://127.0.0.1:8765/manager
    python3 scripts/team_demo.py --pace 0     # all at once

Requires `python3 -m align serve` (scripts/demo.sh resets activity first). Each prompt is
followed by the same hook call Claude Code makes, so findings show as raised in Claude.
"""
import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from align.config import ROOT, ensure_token, settings  # noqa: E402

SCENARIOS = json.loads((ROOT / "examples" / "de-104" / "scenarios.json").read_text())
TASK = "DE-104"

# (engineer, action, payload). Actions: prompt/plan/pre_tool send an event; a response answers
# that engineer's latest open finding the way they would on their own dashboard.
STORY = [
    ("Jordan Lee", "prompt", SCENARIOS["legitimate"]["prompt"]),
    ("Alex Rivera", "prompt", SCENARIOS["negative"]["prompt"]),
    ("Sam Okafor", "prompt", SCENARIOS["duplicate"]["prompt"]),
    ("Priya Nair", "plan", SCENARIOS["retry"]["prompt"]),
    ("Alex Rivera", "prompt", SCENARIOS["positive"]["prompt"]),
    ("Sam Okafor", "use_existing", "Reusing validate_event from lib/events/schema.py."),
    ("Alex Rivera", "pre_tool", "Write lambda_handler.py"),
    ("Jordan Lee", "prompt", SCENARIOS["corrective"]["prompt"]),
    ("Priya Nair", "new_evidence", "Order writer needs jittered backoff; with_backoff is fixed-interval."),
]


def call(method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        settings().url + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", "X-Align-Token": ensure_token()})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def session(engineer: str) -> str:
    return "demo-" + engineer.split()[0].lower()


def step(engineer: str, action: str, payload: str) -> str:
    s = session(engineer)
    if action in ("prompt", "plan", "pre_tool"):
        event = call("POST", "/api/events", {"task_id": TASK, "session": s, "kind": action,
                                             "content": payload, "engineer": engineer})
        hook = urllib.parse.urlencode({"task": TASK, "session": s, "hook": action, "mode": "once",
                                       "event_id": event["event_id"]})
        out = call("GET", f"/api/hook?{hook}")
        return out.get("system_message") or out.get("reason") or "(nothing raised)"
    findings = call("GET", f"/api/findings?task={TASK}&session={s}")
    if not findings:
        return "(no open finding to answer)"
    call("POST", "/api/respond", {"finding_id": findings[0]["id"], "response": action, "note": payload})
    return f"answered {findings[0]['fact_id']}: {action}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pace", type=float, default=3.0, help="seconds between steps")
    args = ap.parse_args()
    try:
        call("GET", f"/api/team?task={TASK}")
    except OSError:
        sys.exit("Align is not running. Start it with scripts/demo.sh or python3 -m align serve.")
    print(f"Manager view: {settings().url}/manager")
    for i, (engineer, action, payload) in enumerate(STORY):
        if i and args.pace:
            time.sleep(args.pace)
        print(f"{engineer:<12} {action:<12} {step(engineer, action, payload)[:110]}")


if __name__ == "__main__":
    main()
