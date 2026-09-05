# Frontier-model refactor audit (Astra / Fable class)

**Date:** 2026-09-05 · **Status:** audit, no code changed · **Scope:** the whole engine as it guides an agent

The prompt for this audit was the "coding agents have changed, clean house" advice: shorter skill descriptions, progressive disclosure, fewer recipes, a lighter AGENTS.md / CLAUDE.md, boundaries that trust the model's judgment, and completion defined up front rather than "stop and check in". This document measures Magnolia against that advice, ranks the refactors, and then lists the bets that would 10x what the app can do once the model underneath it is a frontier-class model.

Everything below was measured on `main` at `db66a82`. Numbers are from the repo, not estimates.

---

## 0. Two things to fix before anything else

These are not about model class. They are red gates on `main`.

| Finding | Evidence | Fix |
|---|---|---|
| **Invariant #1 is broken on `main`.** `tests/test_engine_no_jay.py` fails: `workflow-schedule-meeting/SKILL.md:104` contains the operator's real email address. | Introduced by commit `20cbaa6` "apply recommendation TASK-1740". `task_server.apply_recommendation()` runs `git apply` + `git commit` with a patch-applies pre-check only. It never runs the five green gates. | Move the literal to `profile/` (a `calendar.organizer_email` getter in `profile_lib`), then make `apply_recommendation` run the gates before committing and park the recommendation with the gate output if they fail. The self-improvement loop must be held to invariant #2 like every other committer. |
| **A fresh clone cannot run the gates.** `pytest` errors at collection because `croniter` is only in `requirements-langfuse.txt`; after installing it, 13 tests still fail on Linux. | 5 × `test_transcript_to_dispatch` fail on the gitignored `datasets/tasks/_counter`; 5 × `test_cron_scheduler` fail on a due-job tick; `test_respawn_posix_path_prepended` asserts `/opt/homebrew/bin` on any POSIX host. | Add `croniter` to `requirements-dev.txt`; make the dispatch tests create their own counter fixture; make the respawn test assert the seam, not a macOS path. Portability (invariant #8) should include "the gates are green on a machine that isn't the author's". |

Also six empty files (`Generative-AI`, `How`, `Internal`, `Leading`, `Measure`, `Organizations`) and a 222-byte `Untitled.md` sit at the repo root. They look like an accidental paste of a document title split on spaces. Deleting is a confirm-first action, so they are listed here rather than removed.

---

## 1. What the model sees before it does anything

The fixed context that loads into every interactive session, before the operator's first message:

| Source | Size | Loads when |
|---|---|---|
| 78 skill descriptions (`.claude/skills/*/SKILL.md` frontmatter) | 16,212 chars (~4k tokens) | every session |
| 47 slash commands (`.claude/commands/`) | 109 KB on disk; names + first lines in the listing | every session |
| `CLAUDE.md` + `.claude/CLAUDE.md` | 7.4 KB + 4.0 KB | every session |
| `ui/task-board/CLAUDE.md` | 3.2 KB | inside the UI dir |
| SessionStart hook injecting `meta-using-skills` in `<EXTREMELY_IMPORTANT>` | 3.1 KB | startup, resume, clear **and every compact** |
| `AGENTS.md` | "read `CLAUDE.md` in full before any action" | every non-Claude agent |

`scripts/compaction.py` already records the consequence: the Adapt runner's compact threshold had to be raised from 50% to 60% because "the large FIXED prompt baseline alone" tripped it. That baseline is the thing to shrink.

The headless side is heavier per call. `task_dispatch.build_prompt()` (and every `scripts/workers/*.md` body) injects the full pack-gated skill catalog (7,169 chars) into each dispatch, then tells the model to "Read `CLAUDE.md` in the project root" as step 0 even though `claude -p` already loads it, then lists nine numbered steps plus a rules block. A researcher dispatch therefore reads `CLAUDE.md` twice and a catalog of 34 skills before it reads the task.

---

## 2. Skills: the audit against the four failure modes

### 2.1 Descriptions are too long and too eager

`meta-create-skill` sets a 200-character ceiling on descriptions. **38 of 78 skills exceed it**; the longest (`workflow-agentic-api-designer`) is 552 characters and spends half of them on what the skill does *not* do. Several descriptions are pitches rather than triggers: `quality-pendo-tag-audit` (408 chars) enumerates symptoms; `workflow-launch-monitor` (397) narrates its whole pipeline. Two descriptions also point at each other's territory: `workflow-metric-diagnosis` and `metric-root-cause-diagnosis` both trigger on "metric changed unexpectedly", and `workflow-tradeoff-decision` and `metric-tradeoff-evaluation` both trigger on "mixed results / conflicting metrics".

**Refactor:** cut every description to one clause: *when* to use it, nothing about how. Target ≤ 120 characters. Add a test in `tests/test_skill_frontmatter.py` that enforces the ceiling so the factory cannot regress it. Then run a trigger-accuracy eval (see §6.3) before and after.

### 2.2 No progressive disclosure

74 of 78 skills are a single `SKILL.md`. Only `metric-scorecard-fetch` (10 files), `workflow-velocity-estimate` (5), `quality-documentation-sync` and `workflow-bug-severity-assessment` (2) split anything out. Total skill body is **114,208 words**; `meta-create-skill`'s own ceiling is 500 words and **70 of 78 skills exceed it**. The largest 15 are 17–26 KB each and get read in full whenever they trigger.

What is in those bodies, by kind:

- **Textbook PM material a frontier model already knows.** `metric-root-cause-diagnosis` (598 lines) teaches 4-dimension segmentation with a Snapchat worked example; `workflow-metric-diagnosis` (680 lines) teaches the same thing with a Microsoft Teams example and adds minute-by-minute phase timings ("Phase 1: 15–20 minutes"). `metric-funnel-metric-mapping`, `metric-north-star-alignment`, `metric-proxy-metric-selection`, `workflow-goal-setting`, `workflow-dashboard-design` are the same shape.
- **Repo-specific facts the model cannot know.** Pendo app IDs and tool names (`context-pendo-analytics`), Databricks SQL templates and catalog names (`context-databricks-analytics`), the Jira field map (`workflow-jira-home`), output paths and templates, the EOS rubric. This is the valuable part and it is buried inside the lectures.
- **Steering scaffolding written for weaker models.** 19 skills contain an "Iron Law"; there are 214 `MUST`s across skills and commands; 48 mentions of "rationalization" (anti-rationalization tables, "Red Flags" lists, "Common Mistakes" sections). `meta-create-skill` *requires* a "Common Mistakes / anti-rationalization" section in every skill, which is why every skill has one.

**Refactor:** for each of the 15 largest skills, make `SKILL.md` a router of under 150 lines: the trigger, the inputs, the outputs and where they go, the repo-specific facts, and links to `reference/*.md` (rubrics, SQL, templates, worked examples) that the model opens only when it needs them. Delete the textbook sections outright rather than moving them; a frontier model does not need to be taught what a hypothesis table is. Keep the iron laws that encode a real invariant (citation-per-sentence in content mode, Tier-2 confirm); drop the ones that only exist to stop a model from skipping steps.

### 2.3 Duplicates

ROADMAP §5 already says "prune / merge before adding". Concrete merges:

| Keep | Fold in | Why |
|---|---|---|
| `workflow-metric-diagnosis` | `metric-root-cause-diagnosis` | Same method, same MCP enrichment section, same trigger. |
| `workflow-tradeoff-decision` | `metric-tradeoff-evaluation` | Same five mitigation strategies, same report template. |
| `workflow-metrics-definition` | `metric-funnel-metric-mapping`, `metric-north-star-alignment`, `metric-proxy-metric-selection` | The workflow exists only to sequence these three. One skill with a `reference/` folder. |
| `workflow-magnolia-build` | `.claude/commands/build.md`, `prep.md`, `ship-it.md` pipeline prose | See §3. |

That takes 78 skills to roughly 70 and removes the two trigger collisions.

### 2.4 The factory generates the bloat

`meta-create-skill` is the `$skill-creator` analog and it is the root cause of most of §2. It mandates "NO SKILL WITHOUT A FAILING TEST FIRST" (there is no skill test harness in the repo, so this is ceremony), six required sections including Common Mistakes and Success Criteria, and a documentation-sync gate (`quality-documentation-sync`) that still describes keeping "Cursor rules" in sync with Claude Code across "6 system files"; there is no Cursor configuration in the repo. Fix the generator first, or every new skill re-introduces the pattern:

- Required sections become: trigger (frontmatter), inputs/outputs, repo-specific facts, pointers. Everything else optional.
- Replace the description guidance with the 120-char ceiling and a "no pitch, no negatives" rule.
- Replace "failing test first" with the trigger-accuracy eval in §6.3.
- Retire or rewrite `quality-documentation-sync` to name the files that actually exist (`CLAUDE.md`, `.claude/CLAUDE.md`, `docs/reference/architecture.md`, `packs.yaml`).

### 2.5 `meta-using-skills` is the wrong bootstrap for this model class

It is injected on every startup, resume, clear and compact, wrapped in `<EXTREMELY_IMPORTANT>`, and says: invoke a skill at "a 1% chance" of relevance, "skills control HOW, the user controls WHAT", "announce which skill you are using", "every checklist item becomes a TodoWrite todo", plus a red-flags table for thoughts to suppress. On a model that already picks skills well, this over-triggers (loading a 20 KB skill for a one-line question), adds a narration step to every task, and encourages the model to follow a skill "exactly" where the skill is out of date.

**Refactor:** replace with ~8 lines: skills are auto-discovered; use one when its trigger matches; read the current version rather than recalling it; quality gates named in a skill are mandatory; the operator owns the goal, you own the method. Move the operator-name line from the hook into `CLAUDE.md` via a profile read. Use the hook's `additionalContext` for *state* instead (see §6.4).

---

## 3. Commands: 47 wrappers and three 400-line itineraries

Every skill is already invocable by name, so 44 of the 47 command files are pure indirection ("MANDATORY: use skill X, announce it, follow it exactly"). The other three (`ship-it.md` 18 KB, `build.md` 11 KB, `prep.md` 9.5 KB) are hand-written orchestrations: seven sub-agent dispatch briefs in prose, gates between phases, PM decision points, and a hard rule that every sub-agent "MUST be dispatched using `model: "opus"`". They duplicate the skills they dispatch and they pin a model alias that will be wrong for the next model.

**Refactor:**

- Delete the thin wrappers; keep the command namespace only where the name is materially better than the skill name (`/search`, `/status`).
- Turn `ship-it` into one skill whose `SKILL.md` is the package layout plus the phase order plus the two PM decision points, and let the model choose sub-agents, parallelism and models. The state-transfer design (artifacts on disk under `datasets/product/packages/…`) is right and should stay; the choreography prose should go.
- Remove every `model:` pin from commands and skills. Model choice belongs in `profile_lib.resolve_model` (§4).

---

## 4. The headless harness: assumptions baked for a weaker model

| Where | Today | Why it hurts on a frontier model | Change |
|---|---|---|---|
| `profile_lib.TIER_MODELS` | `light/standard/deep → haiku/sonnet/opus` aliases; `profile.example` pins `judge: claude-opus-4-8`, `parser: claude-haiku-4-5` | The "deep" ceiling is the previous generation; the judge is graded by a weaker model than the worker it grades once the worker moves up. | Map tiers to family aliases resolved from the harness, and add a fourth `frontier` tier; judge follows `deep` by default. Keep the posture shift. |
| `scripts/workers/*.md` | `max_turns: 30`, `timeout: 600` for a `deep` researcher; sentinels `max_turns: 12` | A thorough model uses turns to verify; capping at 30 turns / 10 min truncates exactly the behaviour you want. | Budget by tier (`light` 20 / `standard` 60 / `deep` 150 turns; wall-clock 30 min for deep) and let the worker file override. |
| `build_prompt()` / worker bodies | Step 0 "Read `CLAUDE.md`"; full skills catalog; "follow the skill exactly"; "if you ask a question STOP immediately" | Double-loads `CLAUDE.md`; the catalog is a pack-wide list when the worker already declares its `skills:`; the stop rule plus "prefer completing over asking" pulls in both directions. | Drop step 0 and the catalog (send only the worker's `skills:` list). Replace steps 1–8 with: the task, the acceptance criteria, **the rubric the judge will score with**, and the two exits (`agent:complete`, `agent:ask`). |
| Completion | "Produce the requested output as a file on disk" | The judge's rubric (`judge.py` per-kind rubrics) is the real definition of done, but the worker never sees it. | Inline the kind-specific rubric in the dispatch prompt and ask for a self-check before `agent:complete`. The supervised-tier revision loop then happens inside one session instead of a re-dispatch. |
| `compaction.context_window()` | substring map, default 200k, "1m" → 1M | Wrong window for the new family under-reports usage and delays compaction. | Read the window from the model's `result.usage` where the CLI reports it; keep the map only as fallback. |
| `docs/reference/conventions.md` §1 | Hard-codes the `Co-Authored-By: Claude Opus 4.8 (1M context)` trailer | Stale on the first model change; also a per-model literal in a team-portable doc. | "End commits with the trailer the harness provides." |
| `conventions.md` §7, `profile.example`, `workflow-magnolia-build` | Ports `8744`, `8742`, `8743` respectively | Three docs, three ports. | `server_lib.port()` is the truth; docs say "the configured port". |

---

## 5. Decision boundaries and persistence

The repo has one boundary that must stay hard: **anything that writes to the outside world is Tier-2 with one plain-language confirm** (invariant #5), enforced in code by `adapters.publish()`. That is exactly the kind of boundary the article says to keep.

Around it, a lot of softer "ask first" language has accumulated that a frontier model will honour by stopping:

- `CLAUDE.md`: "For batch operations, show a plan and await approval", "Never overwrite large files without confirmation", "Ask first: file deletion".
- `workflow-magnolia-build`: "Brainstorm before building. No code until a design is approved — even when it looks simple", ask merge authority at kickoff, "Never silently fall back to ad-hoc building" if the superpowers plugin is missing, and a mandated five-skill loop (`brainstorming → scope-extension → writing-plans → subagent-driven-development → finishing-a-development-branch`) for every build "assumed epic-level by default".
- Worker prompts: "If you ask a question, STOP immediately."

**Refactor:** state the safe envelope explicitly, once, in `CLAUDE.md`, in the article's form:

> The five gates, the board on its configured port, writes under `datasets/`, `profile/`, `logs/`, and anything on a feature branch are local and disposable. Run them, fix what breaks, and re-run without asking. The only actions that need a confirm are external writes (Tier-2) and deleting files the operator wrote.

Then define completion for the build loop instead of gating its start: "done" = gates green, e2e observed on the running board, branch pushed, PR opened. Keep brainstorming for multi-surface work; let the model decide for a one-file fix. Drop the hard dependency on the `superpowers` plugin as a precondition; keep it as the recommended loop.

Where the operator *does* want a stop (the two PM decision points in ship-it, the birth-proposal accept in Cadence, the autonomous flip on the ladder), those are real decisions and are already modelled as cards. That is the pattern to keep: a stop is a card, not a sentence in a prompt.

---

## 6. Ten-x bets

The engine's thesis (README, UX_VISION) is a chief of staff that earns autonomy through a judge and a trust ladder. Everything below compounds that thesis; none of it is a new product surface.

### 6.1 Close the self-improvement loop, with the gates in it
ROADMAP §1 is "partial": the digest exists, the improvement agent does not. §0 shows why the gates matter here: the recommendation path already commits to the engine without running them. Build the improvement agent as a `deep`-tier worker that reads `datasets/evals/feedback-loop/`, picks the altitude (skill edit, voice file, worker scope, rubric), writes a patch, **runs the five gates**, and lands a `collab` card with the diff and the gate output. With a frontier model the patch quality is good enough that this becomes the main way skills change.

### 6.2 Make skill hygiene a Cadence program
The "audit your instructions" pass this document did by hand should be a `register`-type program: one item per skill with owner, description length, body words, last-triggered date, trigger collisions. A `skill-hygiene` sentinel reads `.claude/skills/` and the session JSONL; the reconciler flags anything over the ceilings or unused for 90 days; the emitter proposes a merge or a trim as a recommendation card. This is self-hosting in the sense Cadence already uses for `portfolio-health`.

### 6.3 Instrument skill usage and eval trigger accuracy
Nothing today records which skills fire. Add a PostToolUse hook on the `Skill` tool that appends `(timestamp, skill, session, source: interactive|worker)` to `datasets/evals/skill-usage.jsonl`, and build `eval_skill_router.py` on the `eval_task_classifier.py` pattern: a fixture of ~60 real task titles → expected skill, scored after every description edit. Shrinking 78 descriptions without this eval is guesswork; with it, it is a 30-minute job.

### 6.4 Spend the session-start budget on state, not protocol
The hook currently injects 3 KB of protocol. Inject 300 bytes of *state* instead: operator name, active packs, board up/down, counts on Decide / Review / People, the top drifting Cadence program, and the most recent judge disagreement. A chief-of-staff model that opens with "two decisions are waiting and PROG-0007 slipped" is worth more than one that has been reminded to announce skills.

### 6.5 Route work with a model, not regexes
`scripts/workers/*.md` match tasks by `title_patterns` regexes ("(?i)research", "(?i)zendesk|gong"). A `light`-tier one-shot classifier (worker + tier + the two or three skills to load, as JSON) is now cheap, more accurate, and removes the "misroute check" paragraphs every worker carries.

### 6.6 Let the judge be a reviewer, not just a scorer
`judge.py` returns a score and a rationale after the fact. With a frontier model as judge, return a *revision request* (specific edits, with quotes) and feed it into the same session via `--resume` for the supervised tier, instead of re-dispatching from scratch with `judge_why` in the prompt. Raise `max_revisions` from 1 to 3 and lower `revision_bar` friction. This shortens the ladder's climb from shadow to autonomous because approval rates rise.

### 6.7 Gate the interactive session by pack, too
Packs gate workers and the Profile UI but the interactive session sees all 78 skills. Move pack folders under `.claude/skill-packs/<pack>/` and have `packs_lib` materialize the active set into `.claude/skills/` (gitignored symlinks, regenerated by the doctor). An `exec`-profile operator then loads 30 descriptions, not 78, and the skills listing stops being truncated.

### 6.8 Give sentinels more eyes
Cadence is the differentiated part of the product and it currently reads transcripts, the tracker adapter and one sheet. The same read-only contract extends to email and Teams (the M365 MCP is already wired for scheduling), Jira comments, and Pendo usage for `target`-type programs. Each is one `scripts/sentinels/*.md` file plus a source line in the registry; a frontier model attributes cross-source signals to programs far better than the previous generation did, which is what made `movement-watch` conservative by design.

### 6.9 Orchestrate pipelines in code, not prose
`ship-it` is a 400-line prompt because, when it was written, only a prompt could sequence sub-agents. The engine already has the right primitive: a task per phase, artifacts on disk, the judge between phases. Express `ship-it` as a seeded chain of agent-queue tasks (each phase completes → creates the next) so it is resumable, visible on the board, judged per artifact, and no longer needs to live in the orchestrator's context at all.

### 6.10 Make the engine model-agnostic where it is documented, and model-aware where it runs
The harness already abstracts `claude` vs `codex`. Finish the job: no model aliases in any `.md` under `.claude/`, `docs/`, or `scripts/workers/`; one place (`profile_lib`) that maps tier → model per harness, with a per-family override so a teammate on a smaller model can raise `max_turns` guidance and keep the checklists that a frontier model no longer needs. The article's point that "repository skills also guide other contributors' agents" is exactly this repo's team-portability invariant.

---

## 7. Suggested order

1. **Day 1 (gates):** fix the invariant #1 literal; add gates to `apply_recommendation`; add `croniter` to dev requirements; make the three environment-bound tests portable.
2. **Day 1 (generator):** rewrite `meta-create-skill` and `meta-using-skills`; replace `AGENTS.md`'s "read in full" line; add the description-length test; remove `model:` pins and the model-specific commit trailer.
3. **Week 1:** trim all 78 descriptions with the trigger eval (§6.3) in place; delete the 44 thin commands; merge the four duplicate pairs; split the 15 largest skills into router + `reference/`.
4. **Week 2:** worker prompt rewrite with rubric-in-prompt and tier budgets (§4); the safe-envelope paragraph and completion definition in `CLAUDE.md` and `workflow-magnolia-build` (§5).
5. **Then:** the improvement agent (§6.1), skill-hygiene program (§6.2), state-injecting hook (§6.4), model-based routing (§6.5).

Each step keeps invariant #6: nothing generated is deleted, and merged skills leave a one-line `SKILL.md` that points at the survivor for one release.
