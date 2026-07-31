# CLAUDE.md — repo router

This is the team-portable **PM-OS engine**: a calm chief-of-staff system built from markdown + git + simple Python + a headless-Claude harness. Workflows are decomposed into auto-discovered skills under `.claude/skills/`. This file is a router — it leads with the invariants and points you at the canonical reference docs. Read the relevant doc before acting.

## ⚠️ Invariants (read first)

1. The engine never hardcodes person/team identity — it reads from `profile/` via `scripts/profile_lib.py`.
2. Gates stay green before any commit (`pytest`, `card_schema.py`, `test_engine_no_jay.py`).
3. Card definitions reference theme tokens ONLY — never a hardcoded color, radius, or transition.
4. Capture team/person nuance to the PROFILE, never into a generated artifact.
5. Anything that writes to the outside world is Tier-2: exactly one plain-language confirm before its first external action.
6. Never delete generated artifacts — append a version suffix (`v1`, `v2`).
7. `~/pm-os` (port `8742`) is retired and no longer in use — this repo, on `localhost:8744`, is the only install.

Full laws + enforcing commands: [`docs/reference/invariants.md`](docs/reference/invariants.md) — read before acting on the engine.

## Where to look (question → source)

| If you need to… | Read |
|---|---|
| Build a new feature or epic | run **`/magnolia-build`** — loads the operating context and runs the brainstorm→plan→build→ship loop |
| Know the rules that must never break | [`docs/reference/invariants.md`](docs/reference/invariants.md) |
| Understand how the system fits together | [`docs/reference/architecture.md`](docs/reference/architecture.md) |
| Understand or extend **Cadence** (the standing-loop "second organ": programs, sentinels, the reconciler, lifecycle, the tab) | [`docs/reference/cadence.md`](docs/reference/cadence.md) |
| Work the right way (the loop, git mechanics, timing) | [`docs/reference/conventions.md`](docs/reference/conventions.md) |
| Add or change a card type / theme (the rules) | [`docs/reference/design-system.md`](docs/reference/design-system.md) |
| `.claude/` config (skills, packs, commands, hooks) | [`.claude/CLAUDE.md`](.claude/CLAUDE.md) |
| Board UI internals (server, routes, JS, Moods) | [`ui/task-board/CLAUDE.md`](ui/task-board/CLAUDE.md) |
| Profile schema & API | [`profile/README.md`](profile/README.md) |
| Project history / past design decisions | [`docs/plans/`](docs/plans/) (archive) |

## Workspace Layout

- `datasets/product/` — Roadmaps, backlog, PRDs, epics, customer briefs, agent-output
- `datasets/marketing/` — Content pipeline (briefs, outlines, drafts, verify, snippets)
- `datasets/research/` — External sources organized by strategic topic
- `datasets/strategy/` — Strategy sessions and formal memos
- `datasets/meetings/` — Meeting transcripts (Customers/, Internal/) with YAML frontmatter
- `datasets/tasks/` — Unified task queues (human, agent, collab, waiting)
- `datasets/programs/` — Cadence program instances (`PROG-NNNN.md`); `archive/` holds retired programs (version-suffixed)
- `datasets/cron/` — Recurring job definitions
- `cadence/programtypes/registry.json` — Cadence program-type registry (gated by `program_schema.py`); `cadence/starter-sets.yaml` — onboarding bundles
- `scripts/workers/` — Worker definitions for agent task dispatch
- `scripts/sentinels/` — Cadence sentinel definitions (read-only observers)
- `logs/` — Automation execution logs

## Search Tool Selection

| Need | Tool | Why |
|---|---|---|
| Conceptual/topic search | `context-search` skill (qmd) | Semantic search; transcripts use conversational language that doesn't match grep |
| Exact string lookup | Grep | Customer names, task IDs, error messages |
| File structure | Glob | Find files by path pattern |
| YAML frontmatter | Grep | Structured field matching |

**Default for transcript research: qmd first.** People say "it's really slow" not "performance issue."

## MCP Data Sources

Two MCP servers supplement local datasets. Steps that use them are optional and degrade gracefully when unavailable.

- **Pendo** (`mcp__claude_ai_Pendo__*`) — product analytics, feature usage, segments, Pendo Listen feedback, session replays, AI agent analytics. Vantaca subId: `4818486697721856`. App IDs and tool reference live in the `context-pendo-analytics` skill.
- **VantacaDatabricks** (`mcp__claude_ai_VantacaDatabricks__execute_sql_read_only`) — Gong sales calls, Zendesk tickets, Azure DevOps work items. Catalog `is_prod`. SQL templates and schema reference live in the `context-databricks-analytics` skill.

## Meeting File Schema

Every meeting markdown begins with:

```yaml
---
date: "YYYY-MM-DD"
type: "sales | product | customersuccess | onboarding | strategy | ops | marketing | general"
customer: "Company Name"
companies: ["Company A","Company B"]
participants: ["Person Name"]
granola_folder: "Sales"
granola_url: "https://…"
meeting_note_id: "uuid"
tags: ["2026Q2","keyword"]
---
```

**Naming**: `YYYY-MM-DD_{type}_{titleSlug}_{companyOrFunctionSlug}_{participantsSlug}.md`

**Sections**: `## ⬇️ AI Summary` · `## ⬇️ Action Items` · `## ⬇️ Full Transcript` · `## ⬇️ Links`

## Headless Automation

Run meeting-to-backlog automation directly:

```bash
./run-meetings-to-backlog.sh
```

Headless env vars: `CLAUDE_CODE_AUTO_APPROVE_FILE_READS=true`, `CLAUDE_CODE_AUTO_APPROVE_FILE_WRITES=true`, `CLAUDE_CODE_HEADLESS=true`. Logs land in `logs/meetings-to-backlog-YYYYMMDD_HHMMSS.log`.

## Task system, cron, observability

- **Task system, queues, and the agent-dispatch pipeline:** see [`docs/reference/architecture.md`](docs/reference/architecture.md) §9 + §3.
- **Cron (recurring agent tasks):** see [`docs/reference/architecture.md`](docs/reference/architecture.md) §8.
- **LangFuse:** a power-user opt-in for prompt versioning/tracing, not the system of record — see [`docs/reference/architecture.md`](docs/reference/architecture.md) §7.

> In interactive sessions the task system exists but do NOT auto-pick tasks — the human is here to do something specific. Reference and update tasks during work if relevant.

## Output Conventions

- Never delete generated artifacts — append version suffixes (`v1`, `v2`) (invariant #6).
- Maintain `status.json` for processing state, `progress.md` for human notes.
- Historical snapshots for roadmaps and decisions.
- Default to markdown with clear headings; `*-draft.md` if unsure.

## Tools & Permissions

- Allowed: file read/write, dir listing, diffs, local shell ops in safe paths.
- Ask first: file deletion, and anything that writes to the outside world — that's Tier-2 (invariant #5).
- Prefer create/append over overwrite.

## Safety Rails

- Operate within the engine repo unless explicitly instructed otherwise (invariant #7 — `~/pm-os` is retired and no longer in use, so there's no separate install to avoid).
- Never overwrite large files without confirmation.
- When unsure of a path, list directories first.
- For batch operations, show a plan and await approval.
- **Magnolia runs natively on macOS and Windows** — never recommend WSL, Ubuntu, or a Linux VM to make it work. A script that errors on Windows is a portability bug to fix natively (the OS seam is `scripts/platform_lib.py`), not a reason to install a Unix layer.
