"""Command line: python -m align <command>."""
import argparse
import json
import logging
import shlex
import subprocess
import sys
from pathlib import Path

from . import context, db
from .config import GATE_MODES, ROOT, ensure_token, settings
from .knowledge import KINDS, Fact, store
from .knowledge.scan import scan

HOOK = ROOT / "hooks" / "align_hook.py"
PRECOMMIT = ROOT / "hooks" / "align_precommit.py"
PRECOMMIT_MARKER = "# align-precommit"
TOOLS = "Write|Edit|MultiEdit|NotebookEdit|Bash|ExitPlanMode"
# Prompt and plan hooks wait for analysis (ALIGN_NUDGE_WAIT, default 10 s), so allow more time.
HOOK_EVENTS = {
    "UserPromptSubmit": ("prompt", None, 15),
    "PreToolUse": ("pre_tool", TOOLS, 15),
    "PostToolUse": ("post_tool", TOOLS, 5),
}


def cmd_init(args) -> None:
    db.migrate()
    ensure_token()
    print(f"Initialized {settings().home}")
    if args.example:
        example = ROOT / "examples" / args.example
        cmd_import(argparse.Namespace(path=example))
        code = example / "demo_repo"
        if code.is_dir():
            # The example's "production" code lives in this repo's working tree.
            cmd_knowledge_scan(argparse.Namespace(repo=code, ref=None, working_tree=True, name=None))


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


def cmd_knowledge_scan(args) -> None:
    name, facts = scan(args.repo, args.ref, args.working_tree, args.name)
    store().replace_code(name, facts)
    by_kind: dict[str, int] = {}
    for f in facts:
        by_kind[f.kind] = by_kind.get(f.kind, 0) + 1
    where = "working tree" if args.working_tree else (facts[0].commit[:10] if facts else args.ref or "HEAD")
    print(f"Verified {len(facts)} production-code facts from {name} @ {where}: {by_kind}")


def cmd_knowledge_add(args) -> None:
    fact = Fact(id=args.id, kind=args.kind, subject=args.subject, statement=args.statement,
                source=args.source, source_type="manual", scope=[s.strip() for s in args.scope.split(",")],
                verified_by=args.verified_by)
    store().upsert([fact])
    print(f"Stored verified fact {fact.id} (verified by {fact.verified_by})")


def cmd_knowledge_list(args) -> None:
    kb = store()
    hits = kb.search(args.query, args.task) if args.query else [(0, f) for f in kb.facts(args.task)]
    for score, f in hits[: args.limit]:
        print(f"{f.id:<20} {f.kind:<11} {f.source:<40} {f.statement[:80]}" + (f"  (score {score})" if score else ""))
    print(f"{len(hits)} fact(s); store: {kb.stats()['by_kind']}")


def env_prefix(task: str, gate: str) -> str:
    env = {"ALIGN_TASK_ID": task, "ALIGN_GATE": gate, "ALIGN_HOME": str(settings().home),
           "ALIGN_URL": settings().url}
    return " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items())


def hook_command(kind: str, task: str, gate: str) -> str:
    return f"{env_prefix(task, gate)} {shlex.quote(sys.executable)} {shlex.quote(str(HOOK))} {kind}"


def install_precommit(repo: Path, task: str, gate: str, force: bool = False) -> Path:
    p = subprocess.run(["git", "rev-parse", "--git-path", "hooks"], cwd=repo, capture_output=True, text=True)
    if p.returncode != 0:
        raise ValueError(f"{repo} is not a git repository")
    path = (repo / p.stdout.strip()).resolve() / "pre-commit"
    if path.exists() and PRECOMMIT_MARKER not in path.read_text() and not force:
        raise ValueError(f"{path} exists and is not Align's; use --force to replace it")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\n{PRECOMMIT_MARKER}\n"
                    f"{env_prefix(task, gate)} exec {shlex.quote(sys.executable)} {shlex.quote(str(PRECOMMIT))}\n")
    path.chmod(0o755)
    return path


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
    if args.git:
        try:
            print(f"Wrote {install_precommit(repo, args.task, args.gate, args.force)}")
        except ValueError as e:
            sys.exit(str(e))


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
    sp.add_argument("--git", action="store_true", help="also install a git pre-commit check")
    sp.add_argument("--force", action="store_true", help="replace an existing non-Align pre-commit hook")
    sp.set_defaults(func=cmd_install_hooks)

    kp = sub.add_parser("knowledge", help="manage the verified ground-truth knowledge base")
    ksub = kp.add_subparsers(dest="knowledge_command", required=True)
    sp = ksub.add_parser("scan", help="read verified facts from committed production code")
    sp.add_argument("repo")
    sp.add_argument("--ref", help="git ref of production code (default HEAD)")
    sp.add_argument("--working-tree", action="store_true", help="read the working tree (no git)")
    sp.add_argument("--name", help="repo name to record (default: directory name)")
    sp.set_defaults(func=cmd_knowledge_scan)
    sp = ksub.add_parser("add", help="add a fact someone verified by hand")
    sp.add_argument("--id", required=True)
    sp.add_argument("--kind", choices=KINDS, required=True)
    sp.add_argument("--subject", required=True)
    sp.add_argument("--statement", required=True)
    sp.add_argument("--source", required=True)
    sp.add_argument("--verified-by", required=True, help="who verified it, e.g. a reviewer's name")
    sp.add_argument("--scope", default="*", help="comma-separated task ids, or *")
    sp.set_defaults(func=cmd_knowledge_add)
    sp = ksub.add_parser("list", help="list or search verified facts")
    sp.add_argument("query", nargs="?", default="")
    sp.add_argument("--task")
    sp.add_argument("--limit", type=int, default=50)
    sp.set_defaults(func=cmd_knowledge_list)

    args = p.parse_args(argv)
    args.func(args)
