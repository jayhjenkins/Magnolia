# PM-OS Roadmap

> Living document. Anchored to what's actually on disk (skills, the task/cron/dispatch engine, the eval substrate, MCP connectors). This describes where the **system** goes — not the product data, which is gitignored. Companion to [`UX_VISION.md`](./UX_VISION.md) (the design philosophy) and [`README.md`](./README.md) (how it works for a new user).

**Last updated:** 2026-06-16

**Status legend:** ✅ shipped · 🟡 partial · ⬜ planned

---

## Thesis

PM-OS already *runs* the work (dispatch → workers → skills) and *captures* feedback (judge scores + human reactions, all in card frontmatter). The spine that ties this together is a **judge** that scores every card and grows into a **quality manager** — it gets calibrated against human feedback, and eventually gates output and earns task-types the right to run autonomously.

The arc: **report → scored report → autonomous action → product-area squad**, one task-type at a time, always reversible.

**Where we are (2026-06-08):** most of the spine now exists. The judge scores every completed card in real time; the trust ladder (shadow / supervised / autonomous) is named, stored, and rendered; the Tier-2 external-write gate is enforced; the factory, the card-type registry, and all five board tabs are built. The remaining frontier is **closing the loop** — turning captured low scores into *proposed* prompt/skill changes, and graduating a task-type from shadow scoring into inline gating and then autonomous action.

### Already shipped since this doc began
- ✅ **Shadow judge** — scores every completed card (score + per-dimension + one-line why) into card frontmatter, event-driven (`scripts/judge.py`).
- ✅ **Trust ladder** — `shadow / supervised / autonomous` named in `ladder_lib.py`, stored in `datasets/evals/ladder.json` with per-transition thresholds, rendered on the Quality tab.
- ✅ **Calibration substrate** — `build_quality.py` aggregates human-reaction vs judge agreement per task-type; `graduation_assess.py` flags types ready to climb.
- ✅ **Tier-2 external-write gate** — `adapters.publish()` raises `NeedsConfirmation` on first external write; messaging send routes through it to a `confirm` card (invariant #5).
- ✅ **The factory** — `meta-create-worker` / `-card-type` / `-adapter` + `factory_lib.py` (scaffold → gate → commit → Keep/Undo).
- ✅ **Card-type registry** — declarative `ui/task-board/cardtypes/registry.json` (`task` / `recommendation` / `receipt` / `graduation` / `confirm`); `card_type` is a real frontmatter field.
- ✅ **Board tabs** — Now (Suggestions / Decide / Review / People / Agent), Activity, Quality, Engine, Schedules.
- ✅ **Message draft + send** — `message-writer` worker drafts in your voice, judge scores it, you review + send through the Tier-2 gate.
- ✅ **Cron substrate** — `datasets/cron/jobs.json` + daemon tick in `task_server.py`; three jobs seeded (doctor self-heal, weekly self-improvement, graduation ladder).
- ✅ **De-personalization + docs consolidation** — engine reads identity only through `profile/`; the `docs/reference/` layer is the canonical map.
- ✅ **Windows compatibility sweep** — `platform_lib` seam enforced across all dispatch paths; `doctor.py` detects Python reachability; macOS-only scripts labeled; `persist_lib` PowerShell quoting fixed; `task_cli.py --description` + `task_lib` body-replacement unblock ticket-creator agents on Windows.
- ✅ **Jira Severity field** — `JIRA_SEVERITY` drafts and publishes to `customfield_10269`; required for Bug/Regression Defect/Work Item Defect.
- ✅ **Board cancel action + In Review column** — `POST /api/tasks/{id}/cancel`, confirm-dialog textarea, `in-review` status group in agent/collab lanes.

---

## 1. Self-improvement loop — 🟡 partial

Today: judge scores and human reactions land in card frontmatter, but skills are read from disk and never change. **Capture ≠ learning.**

**Built so far:** `eval_digest.py` reads low-scored tasks, clusters them by deliverable kind + worker, and writes a digest to `datasets/evals/feedback-loop/{date}/`. The weekly cron that runs it is seeded.

**Still to build:** the **improvement agent** that reads the digest and proposes a concrete change as a `collab` card with a diff attached. You accept or reject. **Nothing auto-applies.**

**Fixes are not restricted to prompts.** The improvement agent picks the *altitude* of the fix:
- a skill edit (`.claude/skills/<name>/SKILL.md`)
- a new shared `voice.md` / `house-style.md` appended to relevant workers
- a worker scoping change (`scripts/workers/*.md`)
- a new quality-gate skill
- a golden example added to the eval set
- a rubric change to the judge itself

A recurring tone complaint becomes one `voice.md`, not six scattered skill edits. This is `meta-refine-workflow` pointed inward.

**Golden dataset:** the accumulated human-annotated cards. Seed pattern already exists in `eval_meeting_classifier.py` / `eval_task_classifier.py`; extend it to output-quality evals. The judge *proposes* candidates (high-score, high-agreement outputs); **you confirm inclusion.** The judge never adds to its own ground truth.

**Done when:** a down-vote reliably produces a proposed change (at the right altitude) that you can accept as a diff.

---

## 2. Shadow judge → quality manager — ✅ shadow shipped · ⬜ gating planned

An LLM-as-judge, versioned like every other prompt. It has two roles and graduates from one to the other **per task-type**.

### Scoring is real-time; calibration is batch
- **Scoring (✅ shipped):** event-driven. A task completes → judge scores it → score + per-dimension breakdown + a one-line "why" land on the card **immediately** (`judge.py`, called by `task_cli.cmd_agent_complete`). You see it right away.
- **Calibration (✅ substrate shipped):** `build_quality.py` compares judge score vs. your reactions on the same cards → agreement %. Divergence is the signal: either the rubric is wrong (propose a judge-prompt change) or output genuinely varies. The judge improves through the loop, not through training.

### Phase A — Shadow (advisory) — ✅ shipped
Judge scores every output and writes score + notes to the card. **Takes no action.** The read-only manager looking over the shoulder. You read the scores; calibration accrues. (`judge.py` is explicitly observe-only.)

### Phase B — Manager (gating) — ⬜ planned
Once agreement on a task-type crosses threshold, the judge graduates **for that type**: if score < threshold, it bounces the output back for revision **before it lands**.

**How revision works (this is the key mechanic):**
- The revision is **inline, inside the same task execution**: `generate → critique → revise`, bounded to 1–2 passes.
- The worker keeps full context naturally — the critic is a **sub-step of the run**, not a separate later agent. No cross-invocation memory problem.
- The trace records draft-v1, the critique, draft-v2.

The judge does **not** silently fix-and-merge (destroys the signal) and does **not** annotate-and-walk-away once it's a manager. The portfolio judge (scoring landed cards for calibration + the self-improvement loop) is the stateless, after-the-fact measurement layer using the same rubric.

**Done when:** every card carries a quality score on completion *(done)*, and at least one task-type has graduated to inline gating with measured human agreement *(remaining)*.

---

## 3. Capability tiers + autonomy promotion — 🟡 partial

Two distinct axes live here; keep them separate (the [`UX_VISION.md`](./UX_VISION.md) and [`README.md`](./README.md) draw the same line):

- **Blast-radius tiers** — *how far a mistake could reach.* A safety gate.
- **The trust ladder** — *how much the judge inserts itself.* A trust level (shadow / supervised / autonomous), climbed per task-type.

### Blast-radius tiers — ✅ enforced for external writes
- **Tier 0 — read-only:** research, Pendo / Databricks queries
- **Tier 1 — writes local:** reports, drafts, files in the repo
- **Tier 2 — writes external:** Jira publish, send doc, calendar, email

**Rule:** Tier 2 **always** routes through a confirm before its first external action, regardless of how autonomous the task-type is. This is **enforced**: `adapters.publish()` raises `NeedsConfirmation` and a `confirm` card lands in collab (proven end-to-end by the messaging send path). *Remaining:* dispatch-time tier checks are still advisory for non-adapter actions; the gate is real wherever work flows through an adapter.

### Trust ladder + promotion — 🟡 scaffolded
**report → scored report → autonomous action**, one task-type at a time:
1. Reports prove the worker (safe — a report can't break anything). ✅
2. The judge proves the quality. ✅ (scoring live)
3. Calibration proves the judge (high judge-vs-human agreement on that type). ✅ (substrate live; `graduation_assess.py`)
4. *Then* the action graduates. ⬜ (the flip to inline gating / autonomous execution is not yet wired)

**Promote** a task-type when, over a rolling window (~last 10 runs): your approval rate of its collab proposals is high (>~90% accepted unmodified) **and** judge-vs-human agreement is high. (Thresholds live in `ladder.json`.)

**Reversible:** the judge keeps scoring autonomous output. If scores drop, or a spot-check disagrees with the judge → **automatic demote** back to collab. A trust budget per task-type: up with agreement, down with surprises.

**Done when:** Tier 2 actions are blocked from autonomous execution by the system *(done at the adapter seam)*, and at least one task-type has graduated through the full ladder to autonomous *(remaining)*.

---

## 4. Rocks as the reference workflow — ⬜ planned

`metric-quarterly-rocks` + `update_rocks_xlsx.py` would be the most concrete recurring agent job (Q2 2026: Home WAU and Board Member Weekly Login Rate). Harden it as the canonical pattern: byte-reproducible, scored by the judge, cron-driven, exception-review only. *(Not yet on disk — the skill and script don't exist.)*

**Done when:** the weekly Rocks run requires zero manual touch and you review only the delta.

---

## 5. Consolidate, don't expand — 🟡 ongoing discipline

~60 skills is past the point where coverage is the problem — overlap and drift are. `meta-refine-workflow` + `quality-documentation-sync` exist for exactly this. The `docs/reference/` consolidation (the canonical engine map) is ✅ done.

**Rule:** prune / merge before adding. Every skill earns its slot.

**Done when:** the 6-file documentation-sync gate passes clean and no two skills cover the same trigger.

---

## 6. Product-area squads — ⬜ planned

Once 1–3 hold, organize workers into **mini squads by product area** (e.g., Home, Board Member portal). Not a scrum team — a small cross-functional crew whose job is **monitoring and addressing the customer / user experience** for its area.

**Squad roles (agents):**
- **Product** — watches the area's UX signals, triages, frames problems, drafts PRDs/proposals
- **Support** — surfaces and synthesizes customer pain (Zendesk, Gong, Pendo Listen)
- **QA** — validates behavior, catches regressions, checks against definition-of-done
- **Dev (optional)** — squashes bugs / drafts fixes for the small stuff

**Mostly it's about watching the experience:** Pendo usage + session replays, support tickets, sales-call signal — the squad triages what's degrading for users in its area, drafts the response (ticket, fix, PRD update), and escalates to you what needs a decision.

**Shared squad context:** its own backlog slice, `voice.md`, roadmap context, and definition-of-done. A pod-lead orchestrator routes work across the squad. The self-improvement loop (#1) runs scoped to the squad as its "retro."

**Done when:** one product area runs as a squad that detects a real UX issue, drafts the response, and escalates the decision — end to end.

---

## The crons

The cron substrate is ✅ built (`datasets/cron/jobs.json` + daemon tick + atomic counter). **Three jobs are seeded today:** doctor self-heal, weekly self-improvement (`eval_digest`), and the graduation-ladder assessment. The load-bearing recurring jobs below grow that set toward ~20 over time.

1. ⬜ **Rocks metrics refresh** — weekly; Home WAU + Board Member login rate (reference workflow, see §4)
2. ⬜ **Human-queue audit** — weekly; finds human tasks an agent could own, messages an agent could draft, and stale tasks to kill → proposes each as a one-tap recommendation. The self-improvement loop pointed at task *routing*; keeps the human pile from forming. See `UX_VISION.md`. **Highest QoL priority.**
3. ⬜ **Meetings-to-backlog** — nightly; new transcripts → signals / PRD proposals *(runs today via `run-meetings-to-backlog.sh`; not yet a seeded cron)*
4. ⬜ **Shadow judge pass** — scores cards completed since last run *(per-card scoring is event-driven and live; this would be the backfill/sweep)*
5. 🟡 **Feedback-loop improvement pass** — weekly; low scores → collab proposals *(digest seeded; the proposal agent is the missing half, see §1)*
6. ⬜ **Judge calibration** — weekly; judge-vs-human agreement report, flags drift *(aggregation exists in `build_quality.py`; not yet a scheduled cron)*
7. ⬜ **qmd index refresh** — nightly (`qmd-nightly-update.sh`)
8. ⬜ **Task hygiene sweep** — stale `waiting` / `in-progress` → inbox
9. ⬜ **Customer signal digest** — weekly synthesis by customer, QBR-ready
10. ⬜ **Support / sales signal digest** — weekly; Zendesk + Gong via Databricks
11. ⬜ **Doc-sync reconciliation** — agent-output → Word / SharePoint, detect drift

*Later (12–20): research expiry sweep, recruiting pipeline status, north-star weekly, cron self-audit, Jira draft reconciliation, competitive watch.*

---

## Open questions

- **Gating threshold** — one global bar, or per-tier? Leaning per-tier, with Tier 2 stricter than Tier 1. *(Still open — gating, §2 Phase B, is unbuilt.)*
- **Promotion event** — auto or human sign-off? Leaning: criteria can be met automatically, but the *flip to autonomous* is always one `collab` card you approve.
- **Judge rubric** — one shared rubric, or per-task-type? Leaning shared base + per-type overlay. *(Today the judge uses kind-specific dimension sets — document / message / meeting.)*

*Resolved:* **Where People drafts route** → reuse the collab queue via the Tier-2 confirm card (proven by the messaging send path), not a new card state.

---

## Explicitly out of scope

- **Dispatch outside headless / cmux bridge** — different use cases, different feedback loops. cmux is active work; pm-os is automation. They stay separate.
