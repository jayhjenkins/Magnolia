# Cadence Increment 4a - the birth path (design)

> 2026-06-18. Approved design for `/magnolia-build` Cadence inc4a = the front half of
> **slice 8** (lifecycle) of the 11-slice epic (`2026-06-12-cadence-design-brief.md`
> Sec.6, Sec.9). Builds on inc1 (substrate), inc2 (deterministic reconcile), inc3a
> (sentinels + observation ledger + propose-update door), inc3b (worker dispatch +
> emitters). This increment is **Tier-1 throughout**: the only external write is a
> bootstrap emission walked through the *existing* Tier-2 send path - no new external
> surface is built here.

## Why split slice 8, and why this is the front half

Slice 8 is roughly the size of all of inc3. Reading the seam map, inc4 is almost
entirely Tier-1 (the lifecycle creates/moves local program files and enqueues ordinary
tasks; the one external write is a bootstrap emission that rides the already-built Tier-2
confirm). So the cut is not internal-vs-external (the prior seam) - it is the brief's own
Sec.6 split: **where programs are born (Sec.6.1-6.3)** vs **where they die + portfolio
maintenance (Sec.6.4-6.5)**.

- **4a (this doc) = the birth path**: intake sentinel + `program-intake` register +
  birth proposal + birth accept (create program + enqueue bootstrap emissions). End to
  end: evidence -> candidate -> threshold -> birth proposal -> accept -> new active
  program + bootstrap tasks.
- **4b (a later increment) = death + janitor**: the two archive doors + `archive` op /
  version-suffixed file move + the `portfolio-health` janitor + blind-sentinel detection
  (and the sentinel last-run telemetry it needs) + the full grounding pass / binding-
  health renderer.

## The flow (Monday/scan tick)

1. **Intake sentinel** (`scripts/sentinels/program-intake.md`, new, `model_tier: deep`,
   LLM, strictly read-only via the existing `validate_sentinel` contract). It scans the
   same exhaust as `movement-watch` (transcripts/threads), but scoped to the **active
   program-type registry as the classification taxonomy**. It returns structured routing
   records; the **runner applies them** (the LLM never writes files - identical contract
   to movement-watch). Each item routes with the closed verb set:
   - **`observe`** - evidence about an *existing* active program -> `append_observation`
     on that program (reuses the movement path).
   - **`capture`** - an inbox item for a `cycle` program -> `capture` observation (kind
     already in `OBSERVATION_KINDS`). New things for cycle programs are captures, never
     births.
   - **`candidate`** - program-worthy for an active type -> routed to the `program-intake`
     register (Sec. below). Carries: target `program_type`, proposed `title`, an `anchor`
     key if present (tracker epic / normalized title), the `source` citation + `claim`,
     and - for merge - a matched existing-candidate id + a `confidence` when the sentinel
     recognizes one.
   - **`ignore`** - not cadence-level. (The task-extraction pipeline runs independently;
     one item can be both a task and program evidence.)

   The sentinel **never creates programs or tasks**. Its only outputs are observations
   (observe/capture) and candidate-evidence routed onto the `program-intake` program -
   which is itself a program, so the read-only "stamp observations onto programs"
   contract holds.

2. **The `program-intake` register** (new seeded program, `register` state model,
   `status: active` so the reconciler processes it - the self-hosting nursery). Each
   **candidate is a register item** accumulating append-only, source-cited evidence
   across scans. New `program_lib` seam:

   `upsert_candidate(intake_program_id, *, candidate_key, program_type, title, source, claim, anchor=None, link_to=None, confidence=None, root=None)`

   Merge policy (the approved middle option):
   - **Hard anchor match** (incoming `anchor` equals an open candidate's anchor, or a
     normalized-title-key match) -> append evidence mechanically, no judgment.
   - **Sentinel-proposed link** (`link_to` resolves to an open candidate) at **high
     confidence** -> auto-merge: append the evidence to that candidate.
   - **Similar-but-unsure** -> a new candidate item carrying `possible_duplicate_of:
     <id>` for human/janitor review. **Never a silent fuzzy auto-merge.**
   - Declined candidates stay append-only `closed-with-reason` - the memory that prevents
     re-proposing. Only material new evidence reopens one.

   A candidate item shape (register item frontmatter):
   `{ id, program_type, title, anchor, status: open|closed-with-reason|birthed, evidence: [ {date, source, claim, sentinel} ], source_count, possible_duplicate_of?, born_program_id? }`

3. **Birth proposal** - the **reconciler** already processes `program-intake` (it is an
   active register program). A new producer `_propose_births(intake_fm, registry, ...)`:
   for each `open` candidate, look up its **target type's** `intake.birth_threshold`
   (not the intake program's), and if accumulated evidence crosses it, emit a
   `propose-update` recommendation card with proposal
   `{ op: "birth", program_type, title, checkpoints, citations, candidate_id }`.
   Deduped: one open birth proposal per `candidate_id`. Rides the existing ladder at
   **shadow** (unknown/internal -> shadow via `ladder_lib`; not added to
   `enforce_lib.ACTION_TYPES`).

   **Birth threshold** (per type, in the `intake` block):
   - default `{ min_independent_sources: 2, or_explicit_declaration: true }`;
   - low-volume types (e.g. `eos-rock`) declare `{ explicit_declaration_only: true }` -
     born only on an explicit "we're committing to this" declaration, never by source-
     counting (avoids premature births from a quarterly mention).

4. **The birth card** = the existing `recommendation` card (`task_type:
   cadence-propose-update`), no new card type. The diff body renders the **prefilled
   program file** (type, title, inferred checkpoints, the citations that earned it).

5. **Birth accept** - a new branch in `task_server._apply_cadence_proposal`. Today it
   calls `apply_mutation(program_id, proposal)`; a `birth` op has no existing program_id,
   it *creates* one. So before the `apply_mutation` call, branch on
   `proposal["op"] == "birth"` -> `program_lib.birth_program(spec, root)`:
   - `create_program(...)` with `status: active`, inferred `phase`/`checkpoints`, the
     citations written into `## Intent` + an origin observation under `## Observations`
     (`kind: status-signal`, `sentinel: program-intake`, source-cited);
   - enqueue the type's `bootstrap_emissions` as ordinary tasks: `draft-ticket` -> a
     collab task (ticket-creator path -> Tier-2 when walked); `propose-update` -> a
     recommendation card. These ride existing queues; the external write only happens when
     a bootstrap task is *walked to send* (existing Tier-2, no new code; degrades to
     draft-only when unconfigured).
   - mark the candidate item `birthed`, linked to the new `born_program_id`.
   - informational receipt (`receipt_kind: cadence-apply`, non-git). **Reject** -> the
     candidate is `closed-with-reason`.

## Schema work (4a)

- **Registry**: add an `intake` block to the seeded types - `roadmap-initiative` gets the
  full `candidate` block from brief Sec.3 (signals, birth_threshold, bootstrap_emissions);
  cycle types route `capture`/`observe`; `eos-rock` gets `explicit_declaration_only`.
  Add a new **`program-intake`** type entry (`register` model; emitters:
  `candidate-ripe -> propose-update` for birth, `aging -> escalate` for stale candidates).
- **`program_schema.py`**: validate the `intake` block - `intake.route` in the closed
  routing set `{observe, capture, candidate, ignore}`; `route: candidate` requires a
  `birth_threshold`; `birth_threshold` shape (`min_independent_sources` non-negative int,
  `or_explicit_declaration` bool, `explicit_declaration_only` bool - bools rejected where
  an int is required, per the inc3b precedent); `bootstrap_emissions` actions in the
  closed action set (`draft-ticket`, `propose-update` already present).

## The build contract (surfaces -> seams -> gate)

| Surface | Decision | Seam | Gate |
|---|---|---|---|
| sentinel | **build-new** `program-intake` (new routing contract; movement-watch is observation-stamping, intake is routing) | `scripts/sentinels/program-intake.md` + `sentinel_runner` intake-apply branch | `validate_sentinel` + pytest |
| program_lib | **extend** - `upsert_candidate`, `birth_program` (sibling to `apply_mutation`; birth creates, does not mutate an existing id) | `scripts/program_lib.py` | pytest |
| reconciler | **extend** `_evaluate_emitters` / add `_propose_births` producer for the intake register | `scripts/cadence/reconcile.py` | pytest |
| card | **reuse, no JS** - existing `recommendation` / `cadence-propose-update`; new `op: birth` value only | `registry.json` compose | `card_schema.py` |
| accept path | **extend** `_apply_cadence_proposal` with a `birth` branch -> `birth_program` + enqueue bootstrap | `ui/task-board/.../task_server.py` | pytest |
| ladder | **reuse, no code** - `birth` proposal -> shadow; not in `ACTION_TYPES` | `ladder_lib` (read) | pytest |
| program_schema | **extend** - intake block + birth_threshold + bootstrap_emissions | `scripts/program_schema.py` | `program_schema.py` |
| registry/seed | **extend** - intake blocks on types + new `program-intake` type + a seeded `program-intake` program | `cadence/programtypes/registry.json`, `datasets/programs/PROG-00NN.md` | `program_schema.py` |
| platform/UI | **extend** `cadence.js` - render the candidate nursery (register items) on the program-intake row | `ui/task-board/js/cadence.js` | `portability_gate.py` + pytest |

**Standing contract item (every surface):** runtime output must be **ASCII-safe** -
hyphen not em-dash, ASCII quotes (the portability gate can't catch runtime text). Identity
via `profile_lib` only - no person/team/distro literals.

## Constraints (inherited, non-negotiable)

Files-based; append-only evidence (invariant #6 - candidates close-with-reason, never
delete); no identity literals (#1, `test_engine_no_jay.py`); theme tokens only (#3);
exactly one Tier-2 confirm before the first external send (#5 - inherited via the existing
bootstrap path, no new surface); the five green gates stay green (#2); declarative/closed-
set program types + intake routing set (#9); dev board `:8743` only (#7); branch off
`main`, merge to local main unless a PR is requested.

## Deferred to 4b (explicit)

The two **archive** doors (fact: terminal/closed/verified; interpretation: N cycles
silent); the `archive` op + version-suffixed file move to `datasets/programs/archive/`;
the seeded **`portfolio-health`** janitor; **blind-sentinel detection** + the per-sentinel
last-successful-run telemetry it needs; the full **grounding pass + binding-health
renderer**. 4a only marks a newborn's inferred checkpoints honestly as `pending` - no full
grounding sweep yet. Dormant-type *activation* (Sec.8) defers to inc5 with starter-sets +
the factory; 4a intake scans only **live** types (>=1 active program, or explicitly
enabled).

## E2E verification (live, :8743)

Seed (or craft) transcript exhaust naming a new initiative across two scans. Run the
intake sentinel twice: scan 1 opens a candidate in `program-intake`; scan 2 appends a
second independent source (or a merge link). The reconciler then crosses the
birth_threshold and emits a birth `propose-update` card whose body is the prefilled
program file. Accept it: a new `status: active` program file is created with inferred
checkpoints + citations, the candidate is marked `birthed`, and the type's
bootstrap_emissions are enqueued as collab/recommendation tasks (no external send unless
walked). Verify the merge path (a reworded second mention auto-merges at high confidence;
an unsure one gets `possible_duplicate_of`) and that a declined candidate closes-with-
reason and does not re-propose. Clean up all e2e artifacts and restore seeds afterward.
The prod board (`~/pm-os`, :8742) stays untouched.
