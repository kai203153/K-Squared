# K-Squared: Align

Align watches opted-in Claude Code sessions and flags when a developer commits to a direction that contradicts a **confirmed** project decision (an ADR, a requirement), before a PR exists. Findings are private to the developer. Nothing is sent to anyone.

- **Any task, any decision set.** Context lives in files (`*.json` tasks, `*.md` records) that you import.
- **Claude-powered detection with grounding.** Claude proposes conflicts, but a finding is raised only when the claim quotes a confirmed, currently valid record **verbatim**, quotes the developer's message verbatim, and the developer has committed rather than explored. With no SDK or credentials, an offline rule detector driven by patterns in each record takes over.
- **Optional pause.** An opt-in `PreToolUse` gate makes Claude Code ask for confirmation before acting against an open finding.
- **Privacy by default.** Captures prompt text (with a redaction pass) and tool name plus file basename only. No code bodies, commands, or transcripts. Binds to `127.0.0.1` with a per-install token.

## Quick start

Requires Python 3.10+. The core has no dependencies.

```bash
python3 -m align init --example de-104   # DB + token in .align/, imports examples/de-104
python3 -m align serve                    # http://127.0.0.1:8765
```

In another shell, replay the demo scenarios through the real hook script:

```bash
python3 scripts/simulate.py negative                                     # silent: exploring
python3 scripts/simulate.py positive --tool lambda_handler.py --gate once  # HIGH finding + gate asks
python3 scripts/simulate.py corrective                                   # silent
python3 scripts/simulate.py legitimate                                   # silent: gathering evidence
```

## Use it with Claude Code

**Demo repo.** `examples/de-104/demo_repo/.claude/settings.json` already points at the hook with `ALIGN_TASK_ID=DE-104 ALIGN_GATE=once`. Open that folder in VS Code, run Claude Code there, check `/hooks`, and try:

> Create a new standalone AWS Lambda ingestion service for customer events. Implement the handler, infrastructure configuration, and integration tests now. Do not reuse the Glue pipeline.

The finding appears on the dashboard. Before Claude's first Write/Edit/Bash, Claude Code asks you to confirm, citing ADR-12.

**Any repository:**

```bash
python3 -m align import path/to/context-dir
python3 -m align install-hooks path/to/repo --task YOUR-TASK --gate once
```

`install-hooks` merges entries into `<repo>/.claude/settings.json` and keeps your other hooks. Re-running it replaces the earlier Align entries.

## Context format

A task is a JSON file:

```json
{"id": "DE-104", "title": "Customer events ingestion", "goal": "...",
 "work": [{"id": "W1", "description": "Implement ingestion handler", "hours": 2, "dependency": 1, "invalidation": 1}]}
```

A record is Markdown with `key: value` frontmatter. The body is the authoritative text that findings quote.

```markdown
---
id: ADR-12
title: Reuse the Glue ingestion pipeline
kind: adr
status: confirmed            # confirmed | proposed | superseded. Only confirmed records are enforced
applies_to: DE-104           # comma-separated task ids, or *
valid_until: 2027-12-31      # optional; valid_from too
source: docs/ADR-12.md
forbid: \blambda\b[\w\s-]{0,30}\b(service|function|handler)\b   # rule detector: conflict if matched
require: \b(reuse|extend)\b[\w\s-]{0,20}\bglue\b                # rule detector: conflict if negated
---
Extend the existing AWS Glue ingestion pipeline. Do not introduce a new Lambda ingestion service without an approved exception.
```

`forbid` and `require` are optional and used only by the offline rule detector. Negated mentions ("do not create a new Lambda service") don't count as `forbid` matches.

## Claude detection

```bash
pip install -r requirements-llm.txt          # the anthropic SDK
export ANTHROPIC_API_KEY=...                 # or an `ant auth login` profile
ALIGN_DETECTOR=claude python3 -m align serve # default "auto" uses Claude when the SDK is installed
```

- Prompts are analyzed in a background worker, so hooks return immediately.
- Detection uses `claude-opus-5-5` (override with `ALIGN_MODEL`) at low effort, with JSON-schema structured output and the server-side refusal fallback enabled.
- Tool events always use rules, because they carry only metadata.
- Any API error, timeout, or refusal falls back to rules for that event. The event shows a `claude_failed` note.
- Don't send employer content to an external API without permission.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `ALIGN_HOME` | `./.align` | DB and token location |
| `ALIGN_PORT` | `8765` | Server port |
| `ALIGN_DETECTOR` | `auto` | `auto` \| `claude` \| `rules` |
| `ALIGN_MODEL` | `claude-opus-5-5` | Claude model for detection |
| `ALIGN_LLM_TIMEOUT` | `20` | Seconds per Claude call |
| `ALIGN_GATE` | `off` | Hook-side: `off`, `once` (ask once per finding), `strict` (ask until responded) |
| `ALIGN_TASK_ID` | none | Hook-side: which task this repo works on |

## API

All `/api/*` routes require `X-Align-Token` (the contents of `.align/token`). Requests whose `Host` isn't `127.0.0.1` or `localhost` are rejected.

- `GET /api/state?task=`: tasks, records, events, commitments, findings, work, exposure.
- `GET /api/gate?task=&session=&mode=`: `{"decision": "allow" | "ask", "reason"}`. Waits up to 2 s for pending analysis.
- `POST /api/events` `{task_id, session, kind: prompt|pre_tool|post_tool, content}` → `202`.
- `POST /api/respond` `{finding_id, response: use_existing|new_evidence|ask_manager|dismiss, note?}`. `ask_manager` returns a clarification **draft** for you to send yourself.
- `POST /api/import` `{path}`.

## Development

```bash
python3 -m unittest discover -s tests -t .   # no network or SDK needed
python3 -m align reset                       # clear activity, keep imported context
```

## Limitations

- Findings are scoped to a Claude Code session. A new session is re-checked from its own prompts.
- The exposure range is a dependency-weighted estimate of future work at risk, not observed time saved.
- Rule patterns are only as good as the ones written into each record. Claude detection is broader, but it is still bounded by the grounding check, by design.
- Redaction is a regex pass. Get your organization's approval before using Align on real repositories.
