#!/bin/sh
# Start Claude Code in the demo repo with Align's hooks loaded explicitly, so they run even if
# Claude Code treats the enclosing K-Squared repo as the project.
# Usage: scripts/claude-demo.sh   (Align must be running: ALIGN_DETECTOR=rules scripts/demo.sh)
cd "$(dirname "$0")/../examples/de-104/demo_repo" || exit 1
exec claude --settings "$PWD/.claude/settings.json" "$@"
