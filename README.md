# K-Squared: Align

Align catches a junior engineer's mistaken assumptions **before a commit or PR**, and nudges them in the right direction while they work with Claude Code.

Each prompt and each finished plan is checked automatically for the assumptions it rests on, and compared against a **ground-truth knowledge base**. That base contains only verified knowledge: decisions someone confirmed, and facts read from committed production code. The engineer is warned in three ways when they:
- contradict that knowledge,
- rebuild something that already exists, or
- simply act on a material assumption that nothing confirms. A new feature usually has no ground truth yet, and the point is that they *notice* the assumption.

The warnings appear:

- **Inside Claude Code.** Claude is told the finding as hidden context and raises it as a question, citing the source. The engineer sees a short `Align:` line. A finished plan can be sent back for revision.
- **As a desktop pop-up** (macOS, or Linux with `notify-send`).
- **At commit time**, through an optional git pre-commit check.

Findings are private to the engineer. Nothing is sent to anyone.

```
prompt / plan / tool call ──► hook ──► Align server ──► detector ──► grounding ──► finding
                                                           ▲             ▲             │
                                    knowledge base ────────┘─────────────┘             ▼
                     (confirmed decisions + production code)        Claude raises it · Align: line
                                                                    pop-up · gate · pre-commit
```

## What counts as ground truth

| Source | How it gets in | Verified by |
|---|---|---|
| Decision records (ADRs, requirements, conventions) | `python3 -m align import <dir>`. Only `status: confirmed` records; demoted ones are removed | the record's confirmed status |
| Production code: classes and functions (Python, JS/TS), infrastructure (Terraform, CloudFormation), dependencies | `python3 -m align knowledge scan <repo> --ref main` | the git commit it was read from |
| Facts a person checked by hand | `python3 -m align knowledge add ... --verified-by "<who>"` | the named person |

- **Code scanning reads only committed code at the ref**, never the working tree, so unmerged work can't become truth. Tests, vendored code and examples are skipped.
- **Re-scanning replaces a repo's code facts,** so deleted code stops counting.
- **Every code fact keeps its verbatim source line as evidence.**
- **The store refuses unverified facts.**

The knowledge base is a separate SQLite file (`.align/knowledge.db`, or `ALIGN_KNOWLEDGE`) behind the `KnowledgeStore` interface in `align/knowledge/`. That interface is the seam for moving it to a shared global service later.

## How assumptions are checked

1. **Find related facts.** For each prompt or plan, Align collects the task's decisions plus the production-code facts most related to the text (matched by shared word stems in names, paths and docstrings).
2. **Name the assumptions.** The first available detector is used:
   - **Claude API** (`anthropic` SDK plus `ANTHROPIC_API_KEY`).
   - **Headless Claude Code** (`claude -p` on your existing Claude Code login; no key needed). It runs with no tools, no settings or hooks, no MCP servers and no saved session, so it can't trigger Align recursively. It takes about 5–8 s with `ALIGN_CLI_MODEL=sonnet`.

   Either one names the assumptions behind the request, stated or not, as `contradicted`, `already_exists` or `unconfirmed` (at most two of those, and only material ones), quoting the engineer word for word.

   **Offline rules** are the fallback. They use each decision's `forbid:`/`require:` patterns, notice "create a new X" when production code already has an X, and flag work that depends on an external system ("the payments API") the knowledge base doesn't know.
3. **Grounding check.** An unconfirmed assumption needs an exact quote from the engineer and a commitment to act; there's no fact to cite. A conflict becomes a finding only if:
   - the fact exists, is verified and is current;
   - the fact quote appears word for word in the fact;
   - the engineer quote appears word for word in their message;
   - the engineer is committing rather than exploring.

   A model can't invent a finding past this check.
4. **Severity:**

   | Level | Meaning | Engineer sees |
   |---|---|---|
   | **HIGH** | Contradicts a confirmed decision | Claude raises it; alert dialog with *Explore reason* / *Ask manager* (placeholder); optional pause before file writes |
   | **MEDIUM** | Production code already has it, or works differently | Claude raises it; "check existing code first" banner |
   | **LOW** | A material assumption nothing verified confirms | Claude asks them to confirm it; "you might be operating on an assumption" banner |

   - A LOW finding counts as acknowledged when the engineer replies to Claude's question, or clicks *I acknowledge this assumption* on the dashboard.
   - A finding that was seen, acknowledged or dismissed isn't raised again in the same session. Only a resolved finding can recur.

## Quick start

Requires Python 3.11+. The core has no dependencies.

```bash
python3 -m align init --example de-104   # DB, token, decisions, and a scan of the demo "production" code
python3 -m align serve                    # http://127.0.0.1:8765
```

In another shell, replay scenarios through the real hook script and see what Claude and the engineer would be told:

```bash
python3 scripts/simulate.py duplicate                  # LOW: validate_event already exists
python3 scripts/simulate.py retry --plan --gate once   # plan sent back: with_backoff already exists
python3 scripts/simulate.py positive --tool lambda_handler.py --gate once   # HIGH: ADR-12, write paused
python3 scripts/simulate.py negative                   # silent: just exploring
```

## Use it with Claude Code

**Demo repo.** `examples/de-104/demo_repo/.claude/settings.json` is already wired up (`ALIGN_TASK_ID=DE-104`, `ALIGN_GATE=once`). Open that folder in VS Code, run Claude Code there, check `/hooks`, and try "Write a new event schema validator for order events."

**Your own repository:**

```bash
python3 -m align import path/to/decisions            # tasks (*.json) and records (*.md)
python3 -m align knowledge scan path/to/repo --ref main
python3 -m align install-hooks path/to/repo --task YOUR-TASK --gate once --git
```

- `install-hooks` merges into `<repo>/.claude/settings.json` and keeps your other hooks.
- `--git` also installs the pre-commit check. It refuses to overwrite a pre-commit hook that isn't Align's unless you pass `--force`.
- `--git` installs into the git repository that contains `<repo>`. For a subfolder of a larger repo, that's the larger repo.

## How the engineer is nudged

| Hook | What happens |
|---|---|
| `UserPromptSubmit` | Waits up to `ALIGN_NUDGE_WAIT` seconds (default 15) for analysis. New findings go to Claude as `additionalContext` and to the engineer as a `systemMessage`. |
| `PreToolUse` on `ExitPlanMode` | The plan is analyzed. With the gate on, a plan with findings is denied, with the reason given to Claude so it revises the plan or asks the engineer. |
| `PreToolUse` on Write/Edit/Bash | With `ALIGN_GATE=once\|strict`, an open HIGH finding makes Claude Code ask before the tool runs. |
| `PostToolUse` | Findings that arrived after the prompt (slow analysis, tool-event findings) go to Claude here. |
| git `pre-commit` | Lists open findings. It blocks only with `ALIGN_GATE=strict` and a HIGH finding open. |

Each finding is delivered once. Everything fails open: if Align is down or slow, Claude and git carry on.

## Context format

A task is a JSON file: `{"id", "title", "goal", "work": [{"id", "description", "hours", "dependency", "invalidation"}]}`. The work items feed the exposure estimate for HIGH findings.

A record is Markdown with `key: value` frontmatter:

```markdown
---
id: ADR-12
title: Reuse the Glue ingestion pipeline
kind: adr                    # adr | decision | requirement | convention
status: confirmed            # only confirmed records become ground truth
applies_to: DE-104           # comma-separated task ids, or *
valid_until: 2027-12-31      # optional; valid_from too
source: docs/ADR-12.md
forbid: \blambda\b[\w\s-]{0,30}\b(service|function|handler)\b   # rule detector: conflict if matched
require: \b(reuse|extend)\b[\w\s-]{0,20}\bglue\b                # rule detector: conflict if negated
---
Extend the existing AWS Glue ingestion pipeline. Do not introduce a new Lambda ingestion service without an approved exception.
```

## Claude detection

```bash
pip install -r requirements-llm.txt          # the anthropic SDK
export ANTHROPIC_API_KEY=...
python3 -m align serve                       # ALIGN_DETECTOR=auto uses Claude when the SDK is installed
```

- Uses `claude-opus-5-5` (`ALIGN_MODEL`) at low effort, with JSON-schema output and the server-side refusal fallback.
- Prompts and plans use Claude. Tool events always use rules.
- Any API error, timeout or refusal falls back to rules for that event.
- Don't send employer content to an external API without permission.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `ALIGN_HOME` | `./.align` | Activity DB and token |
| `ALIGN_KNOWLEDGE` | `$ALIGN_HOME/knowledge.db` | Ground-truth store; point several installs at one file to share it |
| `ALIGN_PORT` | `8765` | Server port |
| `ALIGN_DETECTOR` | `auto` | `auto` \| `claude` \| `claude-code` \| `rules` |
| `ALIGN_CLI_MODEL` | `sonnet` | Model for the headless Claude Code detector |
| `ALIGN_MODEL` | `claude-opus-5-5` | Claude model for detection |
| `ALIGN_NOTIFY` | `auto` | Desktop pop-ups: `auto` \| `on` \| `off` |
| `ALIGN_NUDGE_WAIT` | `15` | Seconds the prompt/plan hook waits for analysis |
| `ALIGN_GATE` | `off` | Hook-side: `off`, `once` (pause once per HIGH finding), `strict` (until responded; also blocks commits) |
| `ALIGN_TASK_ID` | none | Hook-side: which task this repo works on |
| `ALIGN_CAPTURE_COMMANDS` | `0` | Hook-side: `1` captures Bash commands (redacted) as well as tool names |

## API

All `/api/*` routes require `X-Align-Token` (the contents of `.align/token`). Requests whose `Host` isn't `127.0.0.1` or `localhost` are rejected.

- `GET /api/state?task=`: tasks, records, knowledge stats, events, commitments, findings, work, exposure.
- `GET /api/hook?task=&session=&hook=prompt|plan|pre_tool|post_tool&mode=&event_id=`: what that hook should say: `decision`, `reason`, `system_message`, `additional_context`.
- `GET /api/findings?task=&session=`: open findings, most severe first.
- `GET /api/knowledge?q=&task=`: search verified facts.
- `POST /api/events` `{task_id, session, kind: prompt|plan|pre_tool|post_tool, content}` → `202`.
- `POST /api/respond` `{finding_id, response: use_existing|new_evidence|ask_manager|dismiss, note?}`. `ask_manager` returns a question **draft** for the engineer to send themselves.
- `POST /api/import` `{path}`.

## Development

```bash
python3 -m unittest discover -s tests -t .   # 76 tests; no network, SDK or Claude login needed
python3 -m align reset                       # clear activity, keep context and knowledge
python3 -m align knowledge list [query]      # inspect the ground truth
```

## Limitations

- Findings are scoped to a Claude Code session; a new session is re-checked from its own prompts and plans.
- The rule detector's "already exists" check is word-stem matching on names, paths and docstrings. The Claude detector is much broader, but it's still bounded by the grounding check, by design.
- Code facts cover top-level Python and JS/TS definitions, Terraform/CloudFormation resources, and Python/npm dependencies.
- The exposure range is an illustrative estimate of rework at risk, not observed time saved.
- Redaction is a regex pass. Get your organization's approval before using Align on real repositories.
