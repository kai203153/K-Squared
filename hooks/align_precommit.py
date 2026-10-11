#!/usr/bin/env python3
"""git pre-commit hook for Align: the last check before work leaves the engineer's machine.

Lists open findings for ALIGN_TASK_ID. Warns only, unless ALIGN_GATE=strict and a HIGH
finding is open. Fails open if Align is not running.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

URL = os.environ.get("ALIGN_URL", "http://127.0.0.1:8765")
HOME = Path(os.environ.get("ALIGN_HOME", Path(__file__).resolve().parents[1] / ".align"))


def main() -> int:
    task = os.environ.get("ALIGN_TASK_ID", "")
    if not task:
        return 0
    token = os.environ.get("ALIGN_TOKEN") or (HOME / "token").read_text().strip()
    req = urllib.request.Request(f"{URL}/api/findings?{urllib.parse.urlencode({'task': task})}",
                                 headers={"X-Align-Token": token})
    with urllib.request.urlopen(req, timeout=2) as r:
        findings = json.load(r)
    if not findings:
        return 0
    print(f"Align: {len(findings)} open assumption finding(s) on {task}:", file=sys.stderr)
    for f in findings:
        print(f"  [{f['severity']}] {f['fact_id']} ({f['fact_source']}): {f['explanation']}", file=sys.stderr)
    print(f"  Review them at {URL} before opening a PR.", file=sys.stderr)
    strict = os.environ.get("ALIGN_GATE") == "strict"
    return 1 if strict and any(f["severity"] == "HIGH" for f in findings) else 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception:
        code = 0  # Never block a commit because Align is unavailable.
    sys.exit(code)
