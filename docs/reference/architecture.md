# Architecture — the engine map

The current-state map of the Magnolia engine: the spine, its subsystems, and the seams between them. This is a map, not a spec — the canonical truth for each subsystem lives in code or a skill, which every section links under **Canonical source:**. The laws that hold it together live in [`invariants.md`](./invariants.md) (linked, not restated). History and rationale live in [`docs/plans/`](../plans/). When a section and the code disagree, the code wins.

## 1. The spine — engine / profile / content

Three layers, one rule. The **engine** (skills, scripts, card registry, UI) is shared and de-personalized: you improve it, teammates pull it. **`profile/`** (gitignored, per-person) is the *only* place identity and integration choices live — who you are, which providers you use, your conventions. **`datasets/`** is per-person content (meetings, products, tasks, research). The rule: the engine reads identity only through `profile/`, never a literal (invariant #1).

**Canonical source:** `profile/README.md`; `docs/plans/2026-06-05-pm-os-portability-design.md` §1.

## 2. Skills + packs + auto-discovery

Skills auto-discover from `.claude/skills/<name>/SKILL.md` (flat — one level deep, no manifest). A **pack** (`.claude/packs.yaml`) is a named set of skill folders; `core` is always active; the active list is per-person in `profile/config.yaml` `active_skill_packs`. Packs gate the **background-worker dispatch catalog and the Profile UI only — NOT your interactive Claude Code session**, where native auto-discovery is unchanged. A skill in no pack stays always-available.

**Canonical source:** `.claude/CLAUDE.md`; `.claude/packs.yaml`.

## 3. Worker dispatch — workers scope, skills instruct

`scripts/task_dispatch.py` matches an agent-queue task to exactly one `scripts/workers/*.md` worker (`_default`, `researcher`, `product-analyst`, `scheduler`, `ticket-creator`, plus eval/grad/message workers). The worker declares tools, skills, tier, and timeout — that's *scope*; the skills it names supply the *how*. The model is resolved per task by `profile_lib.resolve_model(worker_tier, posture, task_override)` and passed as `--model` to `claude -p`.

**Canonical source:** `scripts/workers/_default.md`; `scripts/task_dispatch.py`.

## 4. The adapter seam + Tier-2 gate

External integrations are pluggable families behind structural Protocols in each family's `_contract.py`. Live families: `project_management` and `transcript`; future families (`calendar`, `doc_sync`) follow the same contract shape. The seam: the profile picks the provider, the loader (`adapters/__init__.py`) dynamic-imports it, a missing or `"none"` provider degrades gracefully, and the gated `publish(family, draft)` raises `NeedsConfirmation` on the first external write until the Tier-2 confirm is given (invariant #5).

**Canonical source:** `scripts/adapters/__init__.py`; `scripts/adapters/*/_contract.py`.

## 5. Profile + instruct-to-read-profile de-personalization

All identity and integration values flow through `scripts/profile_lib.py` — getters (`provider`, `jira_config`, `pendo_config`, `resolve_model`), writers (`set_integration_provider`, `set_integration_conventions`, `set_integration_confirmed`), and CLI flags (e.g. `--pendo-subid`). This API surface is what makes invariant #1 true: skills, workers, and adapters read from the profile here rather than embedding literals. The denylist test enforces it.

**Canonical source:** `profile/README.md`; `scripts/profile_lib.py`; `tests/test_engine_no_jay.py`.

## 6. The factory (self-extension)

The engine extends itself through a shared lifecycle in `meta-factory-core`: scaffold → capture-to-profile → gate-green → commit → Keep/Undo receipt. Four siblings specialize it — `meta-create-worker`, `meta-create-card-type`, `meta-create-adapter`, and `meta-create-program-type` (a new Cadence program type, §10) — each opens by reading `meta-factory-core` first. `scripts/factory_lib.py` supplies `commit-and-receipt` plus `validate-worker` / `validate-card-type` / `validate-adapter` / `validate-program-type`. Adapters are Tier-2 (they write externally); workers, card-types, and program-types are Tier-1. Git stays invisible — every change is presented as Keep/Undo.

**Canonical source:** `.claude/skills/meta-factory-core/SKILL.md` and the three `meta-create-*` skills.

## 7. Eval substrate

The default eval stack is native files + git + board: prompts live in files (git is their version history), traces in the Claude Code session JSONL, scores in task-markdown frontmatter, and the UI is the board's Quality tab. **LangFuse is a silent power-user opt-in** — set `LANGFUSE_SECRET_KEY` and the existing graceful-degradation wiring lights up — *not* the system of record.

**Canonical source:** `docs/plans/2026-06-06-phase-4-eval-substrate-design.md`.

### Trust ladder — the enforcement layer (no longer advisory)

Each task-type climbs a per-type trust tier in `scripts/ladder_lib.py` (`shadow → supervised → autonomous`), graduated by the twice-weekly assessor (`scripts/graduation_assess.py`). The tier now **enforces** (it was advisory through the 2026-06-09 passive-signal work): the judge is the enforcement seam — after it scores a completed task (`judge.py` `_finalize` → `enforce_lib.apply_post_judge`), `scripts/enforce_lib.py` runs a tier × score policy:

| Tier | Judge `< bar` (revisions remaining) | Judge `>= bar` |
|---|---|---|
| **shadow** | park (advisory — human reviews everything) | park |
| **supervised** | **revise** — reset + re-dispatch `--rerun` carrying `judge_why` (bounded by `max_revisions`) | park for human approval |
| **autonomous** *(action type + global flag ON)* | revise | **auto-ship** via the Tier-2-gated `shipper.autoship` + a never-deleted Keep/Undo receipt |

Three composing gates, none collapsible: **(1)** auto-ship is **judge-gated** — only a passing score ships; unscored/below-bar work never ships (fail-safe to *park*). **(2)** the **action/artifact split** is enforced in code — only `ACTION_TYPES` (`send-message`, `publish-ticket`) can auto-ship; artifacts (PRDs, research, memos) hard-stop at supervised regardless of tier. **(3)** Tier-2 composition — `shipper.autoship` calls the same `adapters.publish()`, so an unconfirmed integration still raises `NeedsConfirmation` → the one-time confirm card (autonomy never bypasses invariant #5's first-write confirm).

Auto-ship runs **only in trusted backend processes** (the judge), never the headless LLM agent session (which has no send tools — `chat_runner` boundary). It ships behind a global default-OFF posture flag `autonomy_enforcement` (`profile/config.yaml`, surfaced as the **Autonomous Mode** toggle in the top-bar settings cog). The brake is the Quality-tab **kill switch** (`POST /api/tasks/{type}/demote` → `ladder_lib.kill_to_supervised`), which instantly drops a type out of autonomous without waiting for the assessor. The supervised revision loop performs no external write and is opt-in via graduation, so it is *not* behind the global flag — the flag guards only the externally-risky auto-ship.

**Canonical source:** `scripts/enforce_lib.py`, `scripts/shipper.py`, `scripts/ladder_lib.py`; `docs/plans/2026-06-09-trust-ladder-enforcement-design.md`.

## 8. Cron

Recurring jobs live in `datasets/cron/jobs.json` with an atomic counter at `datasets/cron/_counter`. A daemon thread in `task_server.py` ticks them; created tasks flow through the normal dispatch pipeline. Title/description template vars (`{date}`, `{week}`, `{month}`, `{year}`) resolve at execution time.

**Canonical source:** `scripts/cron_lib.py`; `scripts/cron_scheduler.py`.

## 9. Task system (quick reference)

A unified task system with four queues. Route work by who acts and whether approval is needed: **human** (decisions, meetings, approvals), **agent** (autonomous research, drafting, analysis), **collab** (an agent acts on an external system but needs human approval first), **waiting** (owed by another person or team). CLI: `./scripts/task.sh add|list|show|update|done|inbox`. Agent-queue subcommands: `agent:start`, `agent:complete --output`, `agent:fail --error`, `agent:ask`. Web UI: `python3 scripts/task_server.py`.

**Canonical source:** `scripts/task.sh`.

## 10. Cadence — the second organ

If the task board is the **verbs of the operator's life**, Cadence is the **state of their programs**: standing loops that hold declared intent against observed reality on a schedule, and emit a verb onto the board only when something genuinely needs a human. Programs are markdown files (`datasets/programs/PROG-NNNN.md`) shaped by a declarative type registry (`cadence/programtypes/registry.json`, gated by `program_schema.py` — invariant #9). Each program runs a **cycle**: *observe* (read-only sentinels on the `claude -p` substrate return source-cited observations; the runner records them, the LLM never writes) → *reconcile* (`scripts/cadence/reconcile.py` computes a `holding/drifting/broken` drift verdict per one of four closed state models — pipeline/cycle/target/register) → *emit* (declarative emitters whose every action becomes a task: `escalate`, `draft-message`, `produce-artifact`, `propose-update`) → *log* (an append-only cycle entry). State mutates through exactly two doors — **facts** (adapter-grounded, applied mechanically with a cited observation) and **interpretations** (proposal cards a human approves, climbing the trust ladder). The full lifecycle (`candidate → active → paused → archived`) is self-hosting: an intake nursery births programs, the reconciler proposes archive, and a seeded `portfolio-health` janitor maintains the portfolio. A `CadenceScheduler` daemon (sibling to the cron scheduler, §8) ticks reconcile. **Cadence performs zero external writes itself** — outward actions ride the existing Tier-2 send path (§4) and the existing judge/ladder (§7); it is mostly Tier-1. The read-only **Cadence tab** (`GET /api/cadence`, `ui/task-board/js/cadence.js`) renders the portfolio from the registry + frontmatter. Extend it by dropping one program-type entry via `meta-create-program-type` (§6) — not by writing engine code.

**Canonical source:** [`docs/reference/cadence.md`](./cadence.md) (the map); `scripts/cadence/reconcile.py`, `scripts/program_lib.py`, `scripts/sentinel_runner.py`, `cadence/programtypes/registry.json` (the truth); `docs/plans/2026-06-12-cadence-design-brief.md` (the why + the 11-slice history).
