"""Command line: python -m align <command>."""
import argparse
import json
import logging
import shlex
import sys
from pathlib import Path

from . import context, db
from .config import GATE_MODES, ROOT, ensure_token, settings

HOOK = ROOT / "hooks" / "align_hook.py"
HOOK_EVENTS = {
    "UserPromptSubmit": ("prompt", None, 3),
    "PreToolUse": ("pre_tool", "Write|Edit|MultiEdit|NotebookEdit|Bash", 5),
    "PostToolUse": ("post_tool", "Write|Edit|MultiEdit|NotebookEdit|Bash", 3),
}


def cmd_init(args) -> None:
    db.migrate()
    ensure_token()
    print(f"Initialized {settings().home}")
    if args.example:
        cmd_import(argparse.Namespace(path=ROOT / "examples" / args.example))


def cmd_import(args) -> None:
    db.migrate()
    result = context.import_dir(args.path)
    print(f"Imported tasks {result['tasks']} and records {result['records']}")


def cmd_serve(args) -> None:
    from .server import make_server

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    db.migrate()
    server = make_server(args.port)
    host, port = server.server_address[:2]
    print(f"Align running: http://{host}:{port}  (detector={settings().detector})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


def cmd_reset(args) -> None:
    db.migrate()
    db.reset_activity()
    print("Cleared events, commitments, and findings. Context kept.")


def hook_command(kind: str, task: str, gate: str) -> str:
    env = {"ALIGN_TASK_ID": task, "ALIGN_GATE": gate, "ALIGN_HOME": str(settings().home),
           "ALIGN_URL": settings().url}
    prefix = " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items())
    return f"{prefix} {shlex.quote(sys.executable)} {shlex.quote(str(HOOK))} {kind}"


def install_hooks(repo: Path, task: str, gate: str) -> Path:
    """Merge Align hooks into <repo>/.claude/settings.json, replacing earlier Align entries."""
    path = repo / ".claude" / "settings.json"
    config = json.loads(path.read_text()) if path.exists() else {}
    hooks = config.setdefault("hooks", {})
    for event, (kind, matcher, timeout) in HOOK_EVENTS.items():
        groups = [
            g for g in hooks.get(event, [])
            if not any("align_hook.py" in h.get("command", "") for h in g.get("hooks", []))
        ]
        group = {"hooks": [{"type": "command", "command": hook_command(kind, task, gate), "timeout": timeout}]}
        if matcher:
            group = {"matcher": matcher, **group}
        hooks[event] = groups + [group]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n")
    return path


def cmd_install_hooks(args) -> None:
    repo = Path(args.repo).resolve()
    if not repo.is_dir():
        sys.exit(f"not a directory: {repo}")
    db.migrate()
    if not context.task_exists(args.task):
        sys.exit(f"unknown task {args.task}; import its context first")
    ensure_token()
    path = install_hooks(repo, args.task, args.gate)
    print(f"Wrote {path} (task={args.task}, gate={args.gate}). Run /hooks in Claude Code to confirm.")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m align")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("init", help="create the database and token")
    sp.add_argument("--example", help="also import examples/<name>, e.g. de-104")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("import", help="import tasks (*.json) and records (*.md) from a directory")
    sp.add_argument("path")
    sp.set_defaults(func=cmd_import)

    sp = sub.add_parser("serve", help="run the local API and dashboard")
    sp.add_argument("--port", type=int)
    sp.set_defaults(func=cmd_serve)

    sp = sub.add_parser("reset", help="clear captured activity, keep context")
    sp.set_defaults(func=cmd_reset)

    sp = sub.add_parser("install-hooks", help="attach Align hooks to a repository")
    sp.add_argument("repo")
    sp.add_argument("--task", required=True)
    sp.add_argument("--gate", choices=GATE_MODES, default="off")
    sp.set_defaults(func=cmd_install_hooks)

    args = p.parse_args(argv)
    args.func(args)
