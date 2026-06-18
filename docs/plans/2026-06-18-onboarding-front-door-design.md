# Onboarding Front Door — design

> Status: approved 2026-06-18. Epic. Merge authority: PRs for Jay to merge.
> Scope decision: **front door only** — distribution stays **git-clone + launcher**
> (no engine/personal decoupling, no skill migration; both deferred to a later epic).

## Problem

Today a teammate installs Magnolia via a two-prompt, restart-in-the-middle dance in
Claude Code (Prompt 1 installs deps + clones; quit + reopen so new tools land on
`PATH`; Prompt 2 is `onboard me`). All onboarding *configuration* then happens
conversationally inside Claude Code via the `meta-onboard` skill — the board and its
cards collect nothing. The restart exists only because a running Claude Code session
can't see newly-installed CLIs on `PATH`.

We want: one `curl` command installs everything, the user types `magnolia`, the browser
opens, a guided in-UI onboarding runs, then the board appears — ready to work. The
intelligence that makes onboarding good (connector inheritance, voice discovery, doctor
remediation, adopting a legacy setup) is exactly the part that needs Claude, so we keep
Claude in the loop by running `meta-onboard` **headlessly behind the board** (the proven
Adapt / task-chat pattern), not by re-encoding it as a dumb wizard.

## The three-layer trust model (the crux)

"Connect my stuff" is really three independent gates. Only the first two are local /
pre-seedable; the third is web-side account OAuth and belongs in the guided UI.

| Layer | What | Where it lives | Pre-seedable by installer? |
|---|---|---|---|
| 1. CLI auth | `claude login` | `~/.claude.json` `oauthAccount` (machine) | No (interactive), but **detectable** → skip if present |
| 2. Folder trust + local MCP/edit | trust dialog, qmd enablement, external-includes | `~/.claude.json` `projects[<path>]` | **Yes — pure JSON patch** |
| 3. Connector authorization | Jira / M365 / Granola / Pendo / Databricks OAuth | claude.ai **account** (web) | **No** — per-connector, user consent, web-side |

Detection signals (read-only, confirmed present in `~/.claude.json`):
`oauthAccount` (has logged in), `claudeAiMcpEverConnected` (a **list** of connectors ever
authorized — lets onboarding branch new-vs-existing user and inherit existing connectors).

Open spike (Inc 1): a headless `claude -p` run in this repo works today despite
`hasTrustDialogAccepted: false`, which suggests the trust *dialog* gates only the
interactive TUI, not `-p`. If confirmed, Layer-2 seeding is cheap insurance for when the
user later opens interactive Claude in the folder — not a hard requirement for onboarding
to run. Seed it anyway; it's trivial. (Note: qmd enablement does NOT travel with a clone —
`settings.local.json` is gitignored — so the installer must seed it regardless.)

## Architecture — six surfaces (mostly assembly of existing parts)

| # | Surface | New/extend | Role |
|---|---|---|---|
| A | `install.sh` / `install.ps1` (curl target) | new | Per-OS bootstrap: install deps (scripts today's `INSTALL-*.md`), `claude login` only if `oauthAccount` absent, clone to known path, seed trust, put `magnolia` on PATH, print banner. |
| B | `magnolia` launcher | new (thin) | Wraps `server_lib.start` + `platform_lib.open_url` + `persist_lib.install`. Subcommands: `magnolia` (boot+open), `magnolia update` (git pull engine), `magnolia doctor` (doctor.py). |
| C | `trust_seed.py` | new (pure lib) | Read `oauthAccount` / `claudeAiMcpEverConnected`; patch `projects[<path>]` → `hasTrustDialogAccepted`, `enabledMcpjsonServers:["qmd"]`, external-includes approved. Unit-testable. |
| D | First-run gate in `task_server.py` | extend | Unpopulated `profile/` → serve onboarding room at `/` instead of board; on completion → serve board. No first-run logic exists today. |
| E | Onboarding UI room in `ui/task-board/` | new (reuse chat pattern) | Welcome + "Onboard me" + chat panel (same frontend as task-chat/Adapt) streaming the agent; on completion closes and reveals the board. |
| F | `onboard_runner.py` | new (mirror `chat_runner`/`adapt_runner`) | Headless `claude -p` (resumed, multi-turn) pointed at `meta-onboard`, **broad** onboarding allowlist (`Bash` + `mcp__claude_ai_*` + Read/Write/Edit) bounded by an Adapt-style `--settings` fairway hook. Reuses `build_chat_cmd`. |

`meta-onboard` gets **light** extension only: emit a completion sentinel the runner
detects; be aware it's running headless-in-UI (narrate "a browser opened — go sign in");
read `claudeAiMcpEverConnected` to branch messaging and surface "Authorize on claude.ai"
links per missing connector. The seven steps otherwise stay as-is.

## Data flow

`curl` → installer (deps + login? + clone + trust seed + `magnolia` on PATH) → user types
`magnolia` → launcher starts server → server sees empty profile → serves onboarding room →
user clicks "Onboard me" → server spawns `onboard_runner` (headless `meta-onboard`) → SSE
stream to chat → agent writes `profile/*`, triggers browser auths, surfaces connector
links → agent emits completion sentinel → first-run gate flips → board served.

## Error handling / graceful degradation (existing patterns)

Missing optional tool → degrade with reason (doctor). Connector not authorized → continue,
feature disabled. M365 admin-consent fails → continue, messaging off. Installer idempotent
+ resumable; `meta-onboard` already resumes. `~/.claude.json` absent (login skipped) → skip
trust seed gracefully.

## The onboarding agent is high-privilege (security note)

`CHAT_ALLOWED_TOOLS` (the task-chat panel) is deliberately locked down — no `mcp__*`, no
broad `Bash` — because that panel must never write externally. The onboarding agent is the
**opposite**: it genuinely needs broad `Bash` (cp, otter_auth.py, mgc login, npm, server
start, task.sh) and `mcp__claude_ai_*`. This is no *more* dangerous than `onboard me` is
today (same power, just UI-driven), but it is a different posture than the chat panel. Bind
it with the Adapt `--settings` fairway hook, not the chat allowlist. Permission prompts
vanish under `bypassPermissions`, so the chat narrates installs rather than prompting.

## Testing + the honest verification limit

- Unit/mockable: `trust_seed.py` (JSON patch), launcher logic, first-run gate decision,
  `onboard_runner` argv builder + sentinel detection — mock the `_spawn` seam like
  `chat_runner` tests do.
- All five gates green (pytest, card_schema, test_engine_no_jay, portability_gate,
  program_schema). Onboarding prompt/skill text stays denylist-clean. OS specifics go
  through `platform_lib` (portability gate).
- **Honest limit:** the *true* end-to-end (curl on a fresh machine → login → browser OAuth
  → board) cannot be fully automated on this dev box. e2e-verify the in-UI onboarding over
  the headless agent on `:8743` with a throwaway profile (first-run gate → wizard → agent
  stream → board reveal). Ship the installer/login/trust path with unit coverage + a
  **manual clean-machine smoke checklist**, stated as such — not claimed as automated e2e.

## Increments (each a PR off `main`, in the `feat/onboarding-front-door` worktree)

1. **Inc 1 — `trust_seed.py` + detection** + the headless-needs-trust spike. Lowest risk,
   foundational, pure-testable.
2. **Inc 2 — `magnolia` launcher** (`magnolia` / `update` / `doctor`) over existing libs.
   Verifiable here (boots the dev server).
3. **Inc 3 — first-run gate + onboarding UI room + `onboard_runner`** + light
   `meta-onboard` extensions. The meat; e2e-verifiable on `:8743`.
4. **Inc 4 — `install.sh` / `install.ps1`** (curl bootstrap) wiring deps + login + clone +
   trust seed + PATH + banner. Manual clean-machine smoke.

## Out of scope (deferred to a later epic)

Engine/personal decoupling (separating the customization surfaces — skills, workers,
adapters, `packs.yaml` — from the tracked engine tree so updates don't collide), and
migration/import of teammates' existing skills. The front door proves the headless-
onboarding pattern that the migration story will later reuse.
