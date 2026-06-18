# Cadence — design brief + draft spec for the standing-loop subsystem

> 2026-06-12. This document is two things: **Part A** is a brief you can hand to a design/build agent
> (via `/magnolia-build`) to produce the detailed design — data model, UI, and scaffolding. **Part B**
> is the draft spec of the primitives, far enough along to iterate on and constrain the design agent.
> Companion thinking: `2026-06-12-loop-abstractions-brainstorm.md` (Cadence programs are the "standing
> objective" from that doc, made concrete).

---

# Part A — The brief

## Mission

Build the second organ of Magnolia. The task board is the **verbs of the operator's life** — discrete
items routed through their attention. Cadence is the **state of their program** — standing loops that
hold declared intent against observed reality on a schedule, and emit a verb onto the task board only
when something genuinely needs a human.

Cadence is an agentic TPM attached to the chief of staff: it tracks, reconciles, nudges, prepares, and
verifies — the convexity work where the last 5% of completeness carries most of the value (the one
dropped commitment is always the one that detonates).

The four seed loops it must support on day one (detailed in the Appendix):

1. **Roadmap & pipeline health** — initiatives move discovery → planning → execution on time, in the
   declared order, with enough refined work always in front of the team.
2. **Weekly prioritization & delivery** — a Monday priorities digest the system drafts and the operator
   tweaks; incoming priorities captured all week; every declared item gated by *"is it actually done?"*
   checks against ground truth (e.g. the PM tracker via the adapter seam).
3. **Did it work?** — every shipped feature's predicted metrics tracked weekly against actuals; closure
   fan-out (who asked for this → notify → hand off to marketing).
4. **EOS facilitation** — scorecard, rocks, issues, to-dos, L10 prep. Strictly a **facilitator, not a
   scribe**: the team updates their EOS sheet manually *on purpose*; this loop reads, prepares, and
   nudges — it never writes to manual-on-purpose sources.

## What the design agent must deliver

1. **Data model design** — finalize the schemas in Part B (program type registry, program instance
   file, **bindings + emission policy**, observation ledger, cycle log, emitter playbooks) with worked
   examples for all four seed program types.
2. **Schema gate** — `scripts/program_schema.py`, the sibling of `card_schema.py`: validates the
   program-type registry (closed state-model set, theme-token-only presentation, instrumented
   checkpoints) and becomes part of invariant #2's green gates.
3. **UI design for the Cadence tab** — see the UI contract (Part B §8): jobs-to-be-done + data
   inventory only. Rendered entirely from the registry (no per-type hardcoded UI), theme tokens only;
   layout and visual treatment are the operator's designer's domain.
4. **Sentinel + reconciler + emitter pipeline design** — how the observe → reconcile → emit cycle runs
   on the existing cron + dispatch substrate, and how emissions enter the existing task system.
   Includes the full **program lifecycle** (Part B §6): intake classification, the intake register,
   birth proposal cards with bootstrap emissions, completion/archive proposals, and the
   portfolio-janitor program.
5. **Factory extension** — `meta-create-program-type`, the fourth sibling under `meta-factory-core`
   (scaffold → capture-to-profile → gate-green → commit → Keep/Undo receipt).
6. **Build slices** — refine the slice plan in Part B §10 into a sequenced plan with verification
   steps per slice (live board e2e, per conventions).

## Non-negotiable philosophy constraints

These extend the existing invariants; the design must not bend them:

- **Files-based, local-first.** Programs are markdown files with YAML frontmatter under `datasets/`,
  exactly like tasks. Git is the history. No database.
- **One external-write seam.** Cadence performs **zero external writes** itself. Every outward action
  (nudge, digest send, escalation) is emitted as a task into the existing queues, governed by the
  existing judge, trust ladder, and Tier-2 confirm. No second shipper, no second ladder.
- **Declarative types, gated.** All flexibility lives in the program-type registry; instances are
  rigid. The registry is validated by a schema gate that runs with the green gates (invariant #2).
  Presentation references theme tokens only (invariant #3 extended).
- **Append-only evidence.** Observation ledgers and cycle logs are never deleted or rewritten
  (invariant #6 extended). Every observation cites its source (file + location).
- **Profile-driven identity** (invariant #1). No person, team, company, distro, or channel literal in
  the engine. The Cadence view is shaped by the operator's *portfolio* (their program instances), not
  by persona flags; family labels and section order are profile display preferences. Routing targets
  (distro lists, channels) live in `profile/` via `profile_lib`.
- **Manual-on-purpose sources are read-only forever.** A source declared `mode: read` in a program
  type can never gain write access by configuration. The EOS sheet is the canonical example.
- **Fact vs. interpretation.** Program state mutates through exactly two doors: (a) **uncontradicted**
  adapter-grounded readings (e.g. tracker status changed, nothing on the ledger disputes it) applied
  mechanically with an observation citing the evidence; (b) interpretations — off-track judgments,
  date-change proposals, and *contradicted* readings (stale tracker vs. fresh transcripts) — emitted
  as proposal cards a human approves, climbing the existing trust ladder like any other action type.
- **No uninstrumented checkpoint.** Every date, target, or invariant a program declares must name how
  it is measured (an adapter query, a metric source, a deterministic check, or explicitly
  `human-attested`). A checkpoint without an instrument fails the schema gate. (This is the LFD
  instruments rule from the companion doc, made an invariant.)

## Why this shape (context for the agent)

The task-board side is easy to extend because its primitives are small, single-purpose, and gated:
workers scope, skills instruct, cards declare presentation, cron schedules, the judge scores, the
ladder governs, adapters write. Cadence must replicate that Lego quality with the same discipline:
**sentinels observe, the reconciler judges drift, emitters propose, programs remember.** No primitive
does two jobs. A future agent extending the system should be able to add a persona's whole world by
dropping one program-type entry — and should find it *structurally impossible*
to create an ungated write path or an unmeasurable promise.

---

# Part B — Draft spec (iterate on this)

## 1. The primitive vocabulary

| Primitive | Job | Analog on the task side |
|---|---|---|
| **Program** | A unit of custody: declared intent + dates + state, held over time | Task (but persistent) |
| **Binding** | A program's pinned source: role (truth/signal/artifact) + provider anchor or local path + traversal scope | Adapter seam, made instance-level (§7) |
| **Program type** | Declarative shape: state model, phases/fields, cadence, sentinels, emitters, sources | Card type in `registry.json` |
| **State model** | One of a **closed set of four** behaviors a type builds on | (new — the fuck-up fence) |
| **Sentinel** | Reads sources and emits observations. Two contracts: **movement** (did anything move an existing program?) and **intake** (is anything here cadence-level — new program candidate, or capture for a cycle program?) | Worker (reuses dispatch substrate, different contract) |
| **Observation** | Append-only, source-cited evidence entry on a program | Activity-log entry |
| **Reconciler** | Per cycle: declared vs. observed → drift verdict + state updates (facts) + proposals (interpretations) | Judge + enforce_lib |
| **Emitter** | Declarative `on: <condition> → action` playbook; all actions exit as tasks | shipper / card actions |
| **Cycle** | One heartbeat of a program: observe → reconcile → emit → log | Cron tick + receipt |

## 2. The four state models (closed set)

Types choose exactly one. New state models require a design doc and a gate change — deliberately hard.

| Model | Shape | Drift means | Seed examples |
|---|---|---|---|
| **pipeline** | Ordered phases, each with an entry/exit window | Checkpoint slip, phase overage, starved supply | Roadmap initiatives; launch checklists |
| **cycle** | Recurring steady-state with a cadence artifact; no phases | Artifact late/missing; captured items not reflected; declared items failing the is-it-done gate | Weekly priorities; L10 prep; scorecard digest |
| **target** | Metric(s) vs. expected trajectory over a window | Actual diverging from predicted beyond tolerance | Did-it-work scoreboard; EA ramp; forecast honesty |
| **register** | A set of items, each with an owner and a closure condition | Item aging past policy; orphaned items; closures unverified or uncommunicated | Customer-promise ledger; decision log; risk register; bug-queue aging |

Drift verdicts are uniform across models — `holding` / `drifting` / `broken` — so the UI, escalation
policy, and portfolio rollups never special-case a type. What *computes* the verdict is model-specific.

### 2.1 Phases are reported, never enforced (pipeline)

The pipeline model is **not a workflow engine**. The system never moves work through phases, never
gates a transition, and never blocks anything for being "out of order." `phase` is a **projection** —
"where does the evidence say this is?" — computed each cycle, known through three doors in order of
preference:

1. **Derived** (the workhorse): the type declares `phase_rules` — a small mapping from truth-binding
   state to phase ("epic has children in an active sprint → execution"; "epic status Done →
   shipped"). Phase becomes a projection of tracker truth; zero bookkeeping. `phase_entered` = the
   first cycle the projection changed.
2. **Observed**: a sentinel hears "design review passed, build starts Monday" → interpretation door,
   `propose-update` card.
3. **Attested**: the operator says so (`human-attested`).

Consequences, stated so nobody rebuilds the tar pit later:

- **Backward moves are history, not errors.** Real work bounces planning↔execution; the projection
  follows the evidence and the phase history records the bounce. There is no sequence enforcement
  anywhere.
- **Phases stay coarse on purpose.** They answer "where is the center of gravity," nothing finer.
  Incremental delivery (early-adopter cohort live, partial ship, ramp) is *not* modeled in phases —
  it lives in **checkpoints** and in the tracker itself, one click away through the binding. A shop
  that wants EA/ramp/GA as phases declares them in its type; the mechanism doesn't change.
- **Checkpoints carry the drift.** A checkpoint is a *dated, instrumented promise* — "X will be true
  by DATE, measured by INSTRUMENT" ("EA cohort live by Jul 15 — Pendo segment exists"). Most pipeline
  drift is date math against checkpoints: deterministic, cheap, and the thing that actually matters.
  Phase overage (time-in-phase vs. the type's window) is a softer, secondary signal.
- **Minimum viable pipeline program = a title + a truth binding + two or three dated checkpoints.**
  Phase is derived sugar on top; if a type declares no `phase_rules`, phase is attested-or-absent and
  the program still works. This is the floor slice 3 builds.

## 3. Program type — registry entry (draft schema)

`cadence/programtypes/registry.json`, validated by `scripts/program_schema.py`:

```json
{
  "id": "roadmap-initiative",
  "label": "Roadmap initiative",
  "family": "roadmap",
  "state_model": "pipeline",
  "phases": [
    { "id": "discovery",  "label": "Discovery",  "max_age_days": 21 },
    { "id": "planning",   "label": "Planning",   "max_age_days": 14 },
    { "id": "execution",  "label": "Execution" },
    { "id": "shipped",    "label": "Shipped" },
    { "id": "verified",   "label": "Verified", "terminal": true }
  ],
  "cadence": "weekly",
  "phase_rules": {
    "from": "binding:tracker",
    "map": [
      { "when": "epic.children_in_active_sprint > 0", "phase": "execution" },
      { "when": "epic.status == 'Done'",              "phase": "shipped" }
    ],
    "fallback": "human-attested"
  },
  "sources": [
    { "kind": "transcripts", "mode": "read" },
    { "kind": "project_management", "mode": "read", "via": "adapter" },
    { "kind": "team_threads", "mode": "read" }
  ],
  "sentinels": ["movement-watch", "tracker-truth"],
  "checkpoint_instruments": ["adapter:project_management", "human-attested"],
  "emitters": [
    { "on": "checkpoint_overdue", "action": "draft-message", "template": "nudge-owner" },
    { "on": "phase_overage",      "action": "propose-update", "template": "phase-stall" },
    { "on": "drift:broken",       "action": "escalate" }
  ],
  "intake": {
    "route": "candidate",
    "signals": ["kickoff/initiative language", "new epic appears in tracker", "recurring theme with an owner and an outcome"],
    "birth_threshold": { "min_independent_sources": 2, "or_explicit_declaration": true },
    "bootstrap_emissions": [
      { "action": "draft-ticket",   "template": "create-tracker-initiative" },
      { "action": "propose-update", "template": "add-roadmap-entry" }
    ]
  },
  "presentation": { "chip_tokens": { "discovery": "--phase-early", "execution": "--phase-active" } }
}
```

Gate checks (minimum): `state_model` ∈ closed set; phases only on `pipeline`; every emitter `action`
∈ closed action set; every source has an explicit `mode`; `mode: read` sources have no write-capable
emitter targeting them; every checkpoint instrument resolvable; presentation uses theme tokens only;
no identity literals (denylist scan extends to `cadence/**`); `intake.route` ∈ closed routing set
(§6.1); `route: candidate` requires a `birth_threshold`; bootstrap emissions ∈ closed action set.

`family` is **presentation-only**: a default shelf label for grouping on the Cadence tab. It does no
computational work (drift, state models, emitters, and scheduling are all family-independent), the
engine never enumerates valid family values, and the profile may rename/regroup/reorder families per
person (§8). Many types per family is the intended shape — e.g. `gtm-initiative` and
`internal-training` (both `pipeline`, different phases) can shelve under the same family as
`roadmap-initiative`. The gate requires only that a default family exists.

## 4. Program instance — file format (draft)

`datasets/programs/PROG-0007.md` — same frontmatter-plus-ledger pattern as tasks:

```markdown
---
program_id: PROG-0007
type: roadmap-initiative
status: active                 # candidate | active | paused | archived (§6); drift computed only for active
title: "Payments reconciliation revamp"
aliases: ["payments revamp", "recon project"]   # how people actually refer to it; grows from observations; feeds the portfolio digest (§5.1)
owner_role: product            # role reference, never a name (invariant #1)
created: 2026-06-12T09:00:00Z
phase: discovery
phase_entered: 2026-06-12
checkpoints:
  - { id: discovery-exit, due: 2026-07-03, instrument: "human-attested", status: pending }
  - { id: ship, due: 2026-09-15, instrument: "adapter:project_management", status: pending }
bindings:                       # where truth lives for THIS program — see §7
  - { id: tracker, role: truth,     kind: project_management, anchor: "epic:ABC-123",
      traverse: ["feature", "unit"], mode: read }
  - { id: prd,     role: reference, kind: local,              anchor: "datasets/product/…/prd-v2.md" }
emission_policy:                # instance overlay on the type's emitter defaults — see §7.4
  muted: [phase_movement]       # covered in daily standups; observe silently, don't card anyone
  overrides:
    - { emitter: nudge-owner, audience: "contact:eng-lead", max_per_week: 1 }
drift: holding                 # cached verdict from last cycle, for the UI
last_cycle: 2026-W24
---

## Intent
One paragraph: what this program holds and why it matters.

## Observations
### 2026-06-12T14:02Z — sentinel:movement-watch [status-signal] (confidence 0.8)
source: datasets/meetings/2026-06-11_product_…md (§Action Items)
claim: Discovery spike reported complete; design review is the gate to planning.

## Cycles
### 2026-W24 — holding
sentinels: tracker-truth ✓ · movement-watch ✓ (3 observations) · emitted: none ·
next checkpoint: discovery-exit in 21d
```

Observation `kind` is a closed enum: `status-signal | date-change | completion | commitment | risk |
metric | capture | blocker`. Observations are append-only and always cite a source. (`capture` is how
"things that come into me" land on a cycle program's inbox between digests.)

Open question for the design agent: single file per program vs. directory split
(`program.md` + `ledger.md`) once observation volume hurts — pick a threshold and a migration that
respects invariant #6.

## 5. The cycle — observe → reconcile → emit → log

Every program runs the same four-step cycle. Who gathers the evidence is §5.1; when everything runs
is §5.2.

1. **Observe.** Gather the witnesses: local bindings are read directly (free); truth sentinels
   deposit one **snapshot observation** per external binding; exhaust-scan observations have been
   accumulating on the ledger since the last cycle. A snapshot is *testimony, not a verdict* —
   "tracker says epic ABC-123 In Progress, last updated 31d ago" — staleness included.
2. **Reconcile** (`scripts/cadence/reconcile.py`, per program). Health-check the bindings first
   (§7.3), then weigh declared state (checkpoints, phase) against the witnesses:
   - **Uncontradicted** structured evidence applies through the **fact door**: a mechanical update
     citing the snapshot. *Uncontradicted* is the load-bearing word — a truth binding's reading
     auto-applies only when nothing else on the ledger disputes it.
   - **Contradicted** evidence is `sources-disagree`, and resolving it is the system's signature
     move, not an error path: a 31-day-stale tracker field vs. three fresh independent transcript
     witnesses → reality moved and the tracker didn't → propose the program update *with the
     citations* and emit a corrective fix-the-tracker draft. **Reality wins; the bound source gets
     driven back into agreement with it** — visibly, never silently in either direction. Witness
     weight accounts for staleness (`last_updated` rides on every snapshot) and independence (three
     sources beat one). Judged interpretation reuses `judge.py` machinery with cadence rubrics and
     is spent only where witnesses disagree or arithmetic can't reach.
   - Outputs: drift verdict and phase projection, **cached into program frontmatter** — which is all
     the UI ever renders (the Cadence tab reads program files only, never a live source; the board
     is the data model as-of-last-cycle).
3. **Emit.** Match the emitter playbook (type defaults overlaid by instance policy, §7.4). **Every
   action becomes a task** in the existing queues — Cadence never acts externally itself:
   - `draft-message` → collab-queue task (judge-scored, ladder-governed, Tier-2-gated like any
     `send-message`).
   - `produce-artifact` → versioned digest/document (never overwritten) + a task carrying it.
     *Attachment slice:* `attachments: [paths]` on task frontmatter; the messaging adapter bundles,
     degrading to inline links.
   - `escalate` → human-queue card: program context + the ≤2-sentence "what I need from you."
   - `propose-update` → recommendation card whose accept applies the program mutation; climbs the
     trust ladder as its own action type (shadow → supervised → autonomous + Keep/Undo receipt).
4. **Log.** Append the cycle entry — checked, heard, concluded, emitted — the program's audit trail.

### 5.1 Sentinels are workers

A sentinel is a **worker** — same definition format (`scripts/workers/*.md` frontmatter: `tools`,
skills, tier, timeout), same dispatch substrate, same `profile_lib.resolve_model` resolution. What
makes it a sentinel is the contract: **its only output is observations deposited on program ledgers
via CLI** — never artifacts, never tasks, never external writes. Two disciplines enforce that:

- **Courier, not author.** Snapshot fields are recorded *verbatim from tool results* ("status field
  reads 'In Progress', updated 2026-05-12"), never paraphrased. Determinism lives in the ledger
  schema, the verbatim rule, and the reconciler's date math — not in the transport.
- **Read-only tool lists, gated.** A sentinel's `tools:` may contain only query/read MCP tools and
  read skills (`context-pendo-analytics`, `context-databricks-analytics`, tracker query skills —
  reuse, don't rewrite). The schema gate rejects a sentinel declaring a write-capable tool; this is
  what keeps "Cadence performs zero external writes" true with agents in the fetch path.

Most external sources in this system are **MCP servers reached from Claude sessions** (tracker,
Pendo, Gong/Zendesk via Databricks) — so most truth sync is a dispatched sentinel run, not a Python
poll. Three mechanisms, by cost:

1. **Local bindings — programmatic, free.** Files in `datasets/` (digest lineages, the EOS sheet
   export, PRDs) are read directly by the reconciler. No agent.
2. **Provider-batched truth sentinels — one run per provider per sweep.** A single tracker-sentinel
   dispatch queries *all* tracker-bound programs' anchors in one session and deposits one snapshot
   observation each. Cost scales with **providers used, not programs held**. Tree traversal is the
   sentinel walking parent/child links via the provider's query tools to the binding's declared
   `traverse` depth — no per-program code.
3. **The exhaust scan — one run per new document, for the whole portfolio.** When a transcript,
   thread, or email lands, one run reads it once and routes everything it finds in a single pass
   using §6.1's closed verb set — evidence for existing programs, captures for cycle inboxes,
   candidates for the intake register. O(documents), not O(programs × documents); same shape as the
   existing meetings-to-backlog harness; shares scan scheduling with task extraction, shares **no**
   output path.

**The portfolio digest** is what makes mechanisms 2–3 work: a compact index regenerated by
`program_lib` from program files (id, title, `aliases`, type, state, bindings per program; intake
signals of live types). It is how a run matches "the payments thing" to `PROG-0007` and knows which
anchors to visit. Digest size at large portfolios is an open question (§11).

### 5.2 Operational cadence — three clocks

1. **The nightly quiet sweep.** Once a day, off-hours: local bindings read free, **one batched
   truth-sentinel run per provider** (a few small-tier agent runs — real but bounded cost), then the
   reconciler recomputes verdicts and refreshes board caches. Local-first semantics: it runs on the
   operator's daemon, so the contract is *run on schedule or on wake* — a missed 3am tick fires at
   next server start (croniter catch-up). The guarantee is "fresh as of last night by the time you
   look," not a fixed hour. **Bindings declare a freshness tier** (`sync_cadence`, §7.2): `local`
   syncs every sweep; tracker-MCP syncs in the nightly provider batch; research-grade sources (a
   Zendesk mention-sweep, a Gong commitment-hunt) are real research-agent runs and sync weekly or on
   the program's own cycle — never nightly by default.
2. **Exhaust scans are event-driven, not scheduled.** The per-document scan (§5.1) runs when a
   document *arrives* — a transcript synced after a 2pm meeting deposits observations that
   afternoon. Observations accumulate intra-day; verdicts recompute at the next sweep or program
   cycle, whichever comes first.
3. **Program cycles fire on human anchors.** Emissions are scheduled by the type/instance cadence on
   the existing cron substrate (never keyed off `family`), anchored to the operator's rhythms, not
   to the sweep: the weekly-priorities digest drafts Sunday night to be waiting Monday morning; L10
   prep fires the day before the meeting; a did-it-work scoreboard refreshes weekly.

Plus one manual override: **refresh-now** per program (re-run truth sync + reconcile on demand) for
the walking-into-the-meeting case — in the actions inventory, §8.3.

## 6. Program lifecycle — discovery, birth, completion, retirement

§5 assumes programs exist. This section is where they come from and where they go. Lifecycle:
`candidate → active → paused → archived`. Drift is computed only for `active`; `paused` keeps
observing but mutes emitters; nothing is ever deleted.

### 6.1 Intake: the classification pass

Intake is the second question asked by the exhaust scan (§5.1) — same pass, same document — scoped
to the **active program-type registry**, which doubles as the classification taxonomy. For each new
item, the scan routes with a closed verb set:

- **`observe`** — it's evidence about an *existing* program → observation on that program (handed to
  the movement path).
- **`capture`** — it's an item for a `cycle` program's inbox (a mid-week priority, an L10 issue) →
  `capture` observation; the next digest reconciles it. New things for cycle programs are captures,
  never births.
- **`candidate`** — it looks program-worthy for an active type → evidence added to the intake
  register (§6.2). Intake never creates programs directly.
- **`ignore`** — not cadence-level. The existing task-extraction pipeline runs independently either
  way; one item can legitimately be both a task and program evidence.

Each type declares its own discovery rules in the registry `intake` block (§3): the signals to watch
for, the routing verb, the birth threshold, and `bootstrap_emissions`. Adding a program type
automatically teaches intake what to look for — no classifier changes.

### 6.2 The intake register (self-hosting nursery)

Candidates are **not** program files — `datasets/programs/` means "under custody." They are items in
a seeded `register`-model program, `program-intake`. Each candidate accumulates source-cited evidence
across scans. The birth proposal fires only when the type's `birth_threshold` is crossed — e.g.
**2+ independent sources, or one explicit declaration** ("we're kicking off X"). This is the
anti-noise fence: a single offhand mention must never become a proposal card. Declined candidates
stay in the register append-only, closed-with-reason — the memory that prevents re-proposing the
same thing. Only material new evidence reopens one.

### 6.3 Birth

The proposal is an existing **`recommendation` card** (no new card type for v1), prefilled by the
grounding pass (§7.5): the program file (type, title, checkpoints inferred from evidence, the
citations that earned the proposal) plus its proposed bindings and emission policy, rendered for
verification. **Accept** → program file created with `status: active` + the type's
`bootstrap_emissions` enqueued as ordinary tasks (e.g. tracker-initiative draft via the
ticket-creator worker → collab queue → Tier-2; roadmap-table entry as a propose-update). **Reject**
→ candidate closed-with-reason in the register.

### 6.4 Completion and retirement

Completion detection belongs to the **reconciler**, not a sentinel, via the two existing doors:

- **Fact**: terminal phase reached, tracker epic closed, `did-it-work` checkpoint verified → archive
  proposal with the evidence attached.
- **Interpretation**: no observations and no emissions for N cycles → "this program seems
  complete/dormant — archive it?" (distinguishing *dormant program* from *blind sentinel* is the
  portfolio janitor's job, §6.5).

Both emit `propose-update: archive`. Accept → `status: archived`, file moves to
`datasets/programs/archive/` (version-suffixed, invariant #6). The manual archive button on the
Cadence row (§8) remains; the system proposing it first is the goal state.

### 6.5 The portfolio janitor

Portfolio maintenance is itself a seeded program — `portfolio-health`, `register` model, whose
**source is the program store and the intake register**: stale actives (no observations in N cycles —
and whether that's a dead program or a blind sentinel), candidates aging unresolved, overlapping or
duplicate programs, supply ("enough refined work in front of the team" lives here, not in any single
initiative), and archive sweeps. Self-hosting means the janitor gets a standard Cadence row, cycle
log, emitters, and kill switch — the maintainer is governed by the same machinery it maintains, and
extending it is editing a program type, not writing new engine code.

## 7. Grounding — bindings and emission policy

A program is only as good as its grounding: where truth lives, and what (if anything) leaves the
loop. Both are **instance-level**, agent-proposed, human-verified.

### 7.1 Three-level source resolution (portability preserved)

- The **type** declares source *kinds* (§3 `sources`): "a pipeline initiative reads a
  project-management truth source plus transcript signals."
- The **profile** resolves each kind to a *provider* — one operator's project management is one
  tracker, another's is another; the engine never knows which (invariant #1). This is the existing
  integration seam (profile-configured providers, reached via MCP tools or adapters); nothing new at
  this level.
- The **program instance** pins *bindings*: concrete anchors inside the provider — this board, this
  epic key, this spreadsheet tab, this local path. Bindings are personal content in `datasets/`,
  so provider literals belong there (never in the engine).

### 7.2 Binding schema

Each binding: `{ id, role, kind, anchor, traverse?, history?, mode, sync_cadence? }`, with `role`
from a closed set (`sync_cadence` is the freshness tier — see §5.2):

- **`truth`** — the structured witness. Only truth bindings can feed the fact door, and only when
  uncontradicted (§5) — "truth" names the binding's *role in reconciliation*, not a promise that the
  source is right. `traverse` declares the child structure to walk under the anchor (initiative →
  features → unit cards), so the loop tracks the tree, not just the root.
- **`signal`** — evidence streams (transcripts, threads, email). Observations only, never facts.
- **`artifact`** — the program's own output lineage. For `cycle` programs this is the central case:
  the weekly-priorities digest file *is* the source of truth —
  `{ role: artifact, kind: local, anchor: "<digest dir>", history: 4 }` means "anchor to last
  week's, read the last four back," then the cycle recommends amendments against it.
- **`reference`** — context the loop may read for understanding (a PRD) but never reconciles against.

Local-first: local-path bindings are the always-available degenerate case; external bindings degrade
gracefully when an adapter is unconfigured (existing pattern). **Multi-source is the default shape**,
not an edge case — a program lists several bindings with different roles. Two `truth` bindings that
disagree is its own drift kind (`sources-disagree`) and escalates rather than silently picking a
winner.

### 7.3 Binding health and re-grounding

Bindings rot — boards move, epics get renamed, files get reorganized. The reconciler health-checks
bindings **first** each cycle; an unreachable or ambiguous binding makes the program **blind**, a
state surfaced distinctly from drift. (This formalizes §6.5's dead-program-vs-blind-sentinel
distinction: binding health is how you tell.) Re-anchoring arrives as a `propose-update` card, and so
do mid-life additions — a sentinel that finds a newly created epic matching an existing program
proposes *adding* a binding through the same door.

### 7.4 Emission policy — per-program overlay on type defaults

The type's emitters (§3) are defaults; each instance carries an `emission_policy`: mutes, audiences,
thresholds, rate caps. This is where "same type, opposite verbosity" lives: a roadmap initiative
covered in daily standups runs effectively silent (`muted:` kills the cards, **not** the watching —
the loop still observes and keeps the shelf current), while the L10-prep program enables the
day-before group nudge with the audience pinned to a leadership channel. Audiences are
contact/channel references resolved per person; the policy is plain data on the instance, editable
any time.

Learning the policy has the usual two doors: **explicit** — every emitted card carries a *mute-this*
quick action that writes back to the instance policy; **implicit** — repeated declines of an
emitter's cards are judge-visible, and the reconciler proposes the mute ("you've declined 4
phase-movement nudges on this program — silence them?").

The Goodhart fence lives here too. A nudge loop's loss function is "commitments converge at minimal
social cost," and the cheap path — nudge harder — is fenced in the policy schema, not left to good
intentions: per-person **rate caps** (`max_nudges_per_person_per_week`) and **send windows** (even
autonomous-tier sends go out in business hours — a nudge is a social act), enforced by the
reconciler, visible on the program. A recipient who stops responding is the loop failing, not the
recipient.

### 7.5 Grounding at birth — the verification card

Grounding is agent-first: when a candidate crosses its threshold (§6.2), a **grounding pass** runs
*before* the proposal card is emitted — a worker explores the profile's providers with the same
read-only tools sentinels use (search the tracker for the initiative, walk its children, locate the
sheet or file) and prefills bindings plus a default emission policy. The birth card then renders three verifiable
sections, so verification is *recognition, not form-filling*:

1. **What** — the program frontmatter (type, title, checkpoints inferred from evidence).
2. **Where truth lives** — each proposed binding with its anchor and what was found there
   ("epic ABC-123 · 4 features · 23 units · last updated 3d ago"), linked for inspection.
3. **What it will say, and to whom** — the emission policy in plain language ("will: nudge the owner
   if a checkpoint slips ≥3 days · won't: card you on phase movement").

Accept → created, bound, policed. Adjust → inline edits to bindings/policy, then accept. Reject →
closed-with-reason. v1 implements this as the existing `recommendation` card with a new registered
**`grounding` body renderer** (the design-system-sanctioned extension point, validated by
`card_schema.py`), not a new card type; promote to a dedicated type only if per-section actions
prove necessary.

Because grounding proposals are ordinary cards in the verb system, the judge and trust ladder apply
for free: acceptance-without-edits is the quality signal, the operator's binding corrections are the
calibration data, and `propose-program`/`propose-update` climb the ladder per task-type. Program
creation is an internal Tier-1 state change, so a high-trust future where well-evidenced,
fully-grounded births auto-apply with a Keep/Undo receipt is reachable under the existing
enforcement rules — the operator's first sight of routine programs becomes the receipt, not the
proposal.

## 8. UI contract — the Cadence tab

A sibling top-level tab to Now/Activity/Quality/Engine/Schedules. This contract is a **jobs-to-be-done
and data inventory only** — layout, hierarchy, and visual treatment are the designer's domain.
Behavioral constraints:

- Rendered entirely from the registry + program files (no per-type hardcoded UI); theme tokens only
  (invariant #3).
- Programs group into `family` shelves. Only families containing active programs render; the profile
  can rename, regroup, and reorder shelves.
- What varies per program is keyed by the type's **state model** — never by `family`, which is
  presentation-only furniture (§3). Perceived shelf differences ("EOS reads differently than
  Roadmap") fall out of the models each shelf contains.
- The only actions mirror the system grammar: open related cards, refresh-now (§5.2), pause/resume,
  archive (version-suffixed, never deleted), and the per-program kill switch (instant stop of its
  emitters — generalizing the Quality-tab brake). Nothing on this tab performs an external action
  directly. Ever.
- Intake candidates need no special UI: `program-intake` is itself a register-model program and
  renders as an ordinary row.

### 8.1 The shelf

**Job to be done:** group programs into the operator's own categories so each area of custody can be
scanned on its own.

**Data available:** the shelf label (profile override, else type default) and the programs it
contains. Nothing else exists at shelf level — no rollup stats, no aggregate health (cross-program
synthesis is the portfolio rollup card on Now, not this tab).

### 8.2 The row (collapsed program)

**Job to be done:** answer, at a glance, *"is this program okay, and does it need me?"* The specific
question varies by state model:

| Model | The row's question |
|---|---|
| **pipeline** | where is this in the flow, and is it on time? |
| **cycle** | did the last beat happen and get followed through — and when is the next? |
| **target** | is the actual tracking the expected? |
| **register** | how much is open, and is anything aging past policy? |

**Data available to every row:**

| Field | Source |
|---|---|
| title | `title` frontmatter |
| type label | registry |
| status | `active · paused · archived` |
| drift verdict | `holding · drifting · broken · blind` (not computed while paused) |
| needs-you count | open cards on Now linked via `program_id` backlink |

**Additional data per model:**

| Model | Fields (sources) |
|---|---|
| **pipeline** | current phase (`phase`, label from registry) · time in phase (`phase_entered`) vs. window (registry `max_age_days`) · next checkpoint + due (`checkpoints`) |
| **cycle** | current period · last artifact + produced/sent timestamp (cycle log) · follow-through count, declared items verified done (reconciler) · next cadence tick (registry `cadence`) |
| **target** | current value · expected value · recent series (checkpoint instruments) · measurement window end (`checkpoints`) |
| **register** | open-item count · items past aging thresholds + oldest age · next policy-breach date (item deadlines) |

Which fields appear and how is design's call; the fence (§8.4) only says nothing outside these lists
exists.

### 8.3 The expanded view

**Job to be done:** answer *"why is it in this state, what has the loop been doing about it, and what
is it watching?"* — enough to audit the program and adjust its behavior without leaving the tab.

**Data available:**

| Group | Fields (sources) |
|---|---|
| Intent | the program's `## Intent` paragraph (instance body) |
| Model history | pipeline → phase history with entered dates vs. windows · cycle → per-period status history (cycle log) · target → predicted-vs-actual series (checkpoint instruments) · register → item list: item, owner, age, closure condition |
| Checkpoints | label · due · instrument · status (pending/met/missed), from `checkpoints` |
| Cycle log | entries: checked · observed · emitted · verdict (append-only) |
| Observations | ledger entries: timestamp · kind · claim · sentinel · source link (append-only) |
| Emissions | when · action · linked card · outcome (pending/approved/sent/declined) |
| Bindings | role · kind · anchor (linkable out) · health (ok/unreachable/ambiguous, from the cycle health check) · last verified |
| Emission policy | muted kinds · audience overrides · rate caps · send windows (§7.4) — the one editable surface on the tab (mute/unmute writes to the instance) |
| Actions | open related cards · refresh-now (re-run truth sync + reconcile for this program, §5.2) · pause/resume · archive · kill emitters |

### 8.4 Explicitly not renderable (the fence)

No data exists for — and no mockup may invent — percent-complete bars, velocity/burndown charts
(beyond the `target` series above), people avatars or workload views, priority scores, activity feeds
beyond the ledger/log data above, aggregate analytics across programs (portfolio rollup card only),
or inline editing beyond the emission-policy toggle. The review rule for design: **if an element
can't cite its row in §8.1–8.3, remove it.**

## 9. Personas emerge from the portfolio

There is no persona configuration. The program-type registry is small and **instance-driven** — a
type with no programs is inert — so personas come for free from the existing architecture:

- **Activation is implicit.** A type is *live* (for intake scanning and UI) iff the operator has ≥1
  active program of that type, or has explicitly enabled it. The portfolio is the activation state.
- **The UI adapts by rendering the portfolio** (§8): only non-empty family shelves appear. An exec
  who holds rocks and a scorecard digest sees exactly that view; no persona flag exists to configure
  or to get wrong.
- **Cold start** is an onboarding concern, not a runtime one: `cadence/starter-sets.yaml` holds
  curated bundles the onboarding concierge offers ("you run EOS — want the L10-prep and scorecard
  programs seeded?"). Consumed once at setup; never consulted at runtime.
- **Dormant-type discovery**: intake may propose *activating* a non-live type at a strictly higher
  evidence threshold — a type-activation recommendation card ("recurring quarterly rock-shaped
  commitments in leadership meetings — start tracking rocks?"). The registry can ship rich; nothing
  surfaces until proposed and accepted.
- A teammate forking the engine gets the types and starter sets; their programs, sources, distros,
  and channels are theirs (profile + datasets). The asymmetry stays private; the machinery ships.

## 10. Build slices (vertical, each independently verifiable)

1. **Substrate**: program file format (incl. bindings + emission-policy schema, §7) +
   `program_lib.py` CRUD + registry + `program_schema.py` gate wired into the green gates. Seed
   `weekly-priorities` + `roadmap-initiative` types. No UI, no agents — create/read programs via
   CLI, gate goes green.
2. **Cadence tab v1**: read-only table from program files. Manual frontmatter edits show up. (Board
   e2e verification per conventions.)
3. **First cycle, deterministic only**: cron-fired reconcile for `pipeline` — date/aging checks, drift
   verdicts, cycle logs, `escalate` emitter → human-queue card. No sentinels yet. *This alone is
   already a useful product: declared dates audited weekly.*
4. **Weekly digest loop end-to-end**: `cycle` model + `produce-artifact` + `draft-message` emitters →
   collab queue → existing send path. The Monday priorities drumbeat goes live.
5. **Sentinels**: `movement-watch` over transcripts (observations with citations), `tracker-truth`
   over the project-management adapter (mechanical fact updates). Observation ledger renders in UI.
6. **Proposal door + ladder**: `propose-update` recommendation cards; register the new action type
   with `ladder_lib`; shadow tier by default.
7. **`register` + `target` models**: promise ledger and did-it-work scoreboard types; Pendo/metric
   instruments for `target`.
8. **Lifecycle + grounding**: intake sentinel + `program-intake` register + the grounding pass and
   `grounding` body renderer (§7.5) + birth proposal cards with bootstrap emissions + archive
   proposals + the `portfolio-health` janitor program. Binding health checks land here too. (Needs
   slices 5–7: sentinels, the proposal door, and the register model.)
9. **Attachments slice**: task `attachments:` + messaging adapter bundling + graceful degradation.
10. **EOS types + starter set**: read-only sheet source, L10-prep cycle type, pre-L10 nudge emitters
    with rate caps; first entry in `starter-sets.yaml`.
11. **Factory**: `meta-create-program-type` under `meta-factory-core`; portfolio rollup card (weekly
    cross-program digest) last, once ≥2 families run.

## 11. Open questions for the design agent

- Single-file vs. directory-per-program threshold and migration (invariant #6-safe).
- Observation dedup/merge policy when multiple sentinels cite the same evidence.
- Reconciler determinism boundary: enumerate which checks per state model are deterministic vs.
  judged, and the cadence rubrics for the judged ones.
- Portfolio-digest scaling: at what program count does the per-document exhaust scan need digest
  compaction or sharding, and how do stale `aliases` get pruned (append-only ledger vs. curated list)?
- `phase_rules` expression grammar: how rich can `when` clauses be before they need a real evaluator —
  start with a fixed set of named predicates per adapter family?
- Cycle-program "capture inbox" UX: how mid-week captures are confirmed into next week's digest.
- Rollover conventions at period boundaries (quarterly EOS rock turnover, new-quarter roadmap
  re-seeding) — archive mechanics are specified in §6.4; the *cadence of renewal* is not.
- Candidate merging: two intake candidates that are the same initiative described differently —
  merge policy and how merged evidence is attributed.
- Birth-threshold tuning per type: are the defaults (2 sources / explicit declaration) right for
  low-volume types like EOS rocks, which are declared once in a quarterly session?
- Anchor schema generality: how provider-specific can `anchor` strings be before they need a
  per-adapter grammar (`epic:KEY` vs. board/list IDs vs. sheet+tab+range)? Where does anchor
  validation live — `program_schema.py` or the adapter contract?
- Grounding-pass economics: runs per candidate at threshold-crossing — cost cap, and what the birth
  card shows when grounding finds nothing (propose ungrounded with a `blind` badge, or hold the
  candidate?).
- Cross-program awareness: where does "these two drifts share a root cause — the same undecided
  decision" live? (Likely the portfolio rollup, not the per-program reconciler — keep the reconciler
  single-program and dumb.)

---

# Appendix — seed loops mapped to primitives

| Loop (operator language) | Program type(s) | State model | Key sentinels | Key emitters |
|---|---|---|---|---|
| Roadmap & pipeline health | `roadmap-initiative` (one program per initiative) + a `pipeline-supply` target | pipeline (+ target) | movement-watch, tracker-truth | nudge-owner, phase-stall proposal, escalate |
| Weekly prioritization & delivery | `weekly-priorities` (one standing program) | cycle | capture-watch (email/threads/transcripts), tracker-truth (is-it-done gate) | produce-artifact (digest) → draft-message (team channel, attachment), escalate |
| Did it work? | `did-it-work` (one program per shipped feature) | target | metric-watch (Pendo), mention-watch (Zendesk/Gong for closure lists) | produce-artifact (scoreboard), draft-message (marketing handoff), propose-update (verified) |
| EOS facilitation | `eos-rock` (per rock), `eos-l10-prep` (cycle), `eos-scorecard-digest` (cycle) | pipeline/target + cycle | sheet-watch (read-only), transcript-watch, thread-watch | pre-L10 nudges (rate-capped), L10 issue-list artifact, escalate |
| Customer-promise ledger (ext.) | `promise-ledger` | register | gong/transcript commitment-watch, roadmap-truth | closure draft-message, escalate on silent breaks |
| Forecast honesty (ext.) | `forecast-honesty` | target | velocity instrument (existing estimate skill), tracker-truth | propose-update (date divergence), escalate |
| Program intake (system) | `program-intake` (seeded) | register | intake sentinels over all exhaust | birth proposal (recommendation card) on threshold |
| Portfolio janitor (system) | `portfolio-health` (seeded) | register | program store + intake register | archive proposals, staleness escalations, supply check |
