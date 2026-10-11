"""Assumption detection through headless Claude Code (`claude -p`).

Uses the engineer's existing Claude Code login, so no API key is needed. The call is locked
down: no tools, no project/user settings or hooks from the working directory, no MCP servers,
no saved session, and output validated against the shared JSON schema.
"""
import json
import shutil
import subprocess

from ..config import settings
from ..knowledge import Fact
from . import Analysis
from .claude import INSTRUCTIONS, SCHEMA, facts_block, to_analysis, user_message


class ClaudeCodeDetector:
    name = "claude-code"

    def __init__(self, binary: str | None = None):
        self.binary = binary or shutil.which("claude")

    def available(self) -> bool:
        return self.binary is not None

    def command(self, content: str, kind: str, facts: list[Fact]) -> list[str]:
        return [
            self.binary, "-p", user_message(content, kind),
            "--system-prompt", f"{INSTRUCTIONS}\n\n{facts_block(facts)}",
            "--model", settings().cli_model,
            "--tools", "",
            "--setting-sources", "project",
            "--strict-mcp-config",
            "--no-session-persistence",
            "--output-format", "json",
            "--json-schema", json.dumps(SCHEMA),
        ]

    def analyze(self, content: str, kind: str, facts: list[Fact]) -> Analysis:
        s = settings()
        # Run from Align's own directory, which has no .claude settings, so no hooks fire
        # and this analysis can never trigger Align recursively.
        p = subprocess.run(self.command(content, kind, facts), cwd=s.home, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=s.llm_timeout)
        if p.returncode != 0:
            raise RuntimeError(f"claude -p exited {p.returncode}: {p.stderr.strip()[:200]}")
        out = json.loads(p.stdout)
        if out.get("is_error") or not isinstance(out.get("structured_output"), dict):
            raise RuntimeError(f"claude -p returned no structured output: {str(out.get('result'))[:200]}")
        return to_analysis(out["structured_output"], self.name)
