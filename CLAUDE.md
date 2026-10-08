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
| Start a product package (Phase 1 discovery → `context-brief.md`) | the **`workflow-context-brief`** skill (`/context-brief`; Phase 1 of `/ship-it` and `/prep`) |

Skill and command instructions invoke Python as `python3` (on Windows the installer provides a `python3` shim).

## Workspace Layout

- `datasets/product/` — Roadmaps, backlog, PRDs, epics, customer briefs, agent-output
- `datasets/marketing/` — Content pipeline (briefs, outlines, drafts, verify, snippets)
- `datasets/research/` — External sources organized by strategic topic
- `datasets/strategy/` — Strategy sessions and formal memos
- `datasets/meetings/` — Synced meeting transcripts (`.txt`, `{domain}/YYYY-MM/`) with YAML frontmatter
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

Synced transcripts are **`.txt`** files. `granola_sync.py` / `otter_sync.py` write them (named `YYYY-MM-DD_HH-MM_{title}[_{id8}].txt`) into the profile's transcript target (default `datasets/meetings/`); `otter_classify.py` (via `transcript_post.py`) then prepends YAML frontmatter and moves the file to `{target}/{domain}/YYYY-MM/`. Frontmatter fields (empty ones omitted):

```yaml
---
title: "Meeting title"
date: "YYYY-MM-DD"
duration_minutes: 30
domain: "customer"            # classifier domain path, e.g. customer, strategy, product/<area>, general
participants:
  - "Person Name"
participant_emails:
  "Person Name": "person@example.com"
otter_id: "…"                 # source meeting id (Otter or Granola)
---
```

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
