# Cadence Increment 4a - the birth path Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Cadence can discover and birth its own programs - an intake sentinel routes exhaust into a self-hosting candidate nursery, the reconciler proposes a birth when evidence crosses the threshold, and accepting it creates an active program plus its bootstrap emissions.

**Architecture:** All Tier-1. A new read-only `program-intake` sentinel returns routing records (observe/capture/candidate/ignore); the runner applies them. Candidates accumulate as append-only register items in a seeded `program-intake` register program. The reconciler runs a `_propose_births` producer over that register; a birth becomes an existing `recommendation`/`cadence-propose-update` card carrying `proposal {op: birth, ...}`. Accept branches in `_apply_cadence_proposal` to `program_lib.birth_program` (create + enqueue bootstrap emissions). The only external write is a bootstrap emission walked through the existing Tier-2 path - no new external surface.

**Tech Stack:** Python (program_lib, reconcile, sentinel_runner, program_schema), JSON registry, markdown program files, the existing task_server accept path, vanilla JS Cadence tab. Tests: pytest.

**Reference:** design doc `docs/plans/2026-06-18-cadence-lifecycle-birth-design.md`; brief `docs/plans/2026-06-12-cadence-design-brief.md` Sec.6, Sec.3.

**Standing contract (every task):** runtime output ASCII-safe (hyphen not em-dash, ASCII quotes); identity via `profile_lib` only (no person/team/distro literals); append-only (never delete - candidates close-with-reason); the five green gates must pass before each commit: `python3 -m pytest -q`, `python3 scripts/card_schema.py`, `python3 -m pytest tests/test_engine_no_jay.py`, `python3 scripts/portability_gate.py`, `python3 scripts/program_schema.py`.

---

### Task 1: Schema - validate the `intake` block

**Files:**
- Modify: `scripts/program_schema.py`
- Test: `tests/test_program_schema.py`

**What:** Teach the gate the `intake` block (brief Sec.3). Add a closed routing set
`INTAKE_ROUTES = {"observe", "capture", "candidate", "ignore"}`. In the per-type loop,
when a type declares `intake`, validate:
- `intake.route` in `INTAKE_ROUTES`;
- `route == "candidate"` requires a `birth_threshold` dict;
- `birth_threshold`: `min_independent_sources` (if present) is a non-negative int with
  `isinstance(v, bool)` rejected first; `or_explicit_declaration` / `explicit_declaration_only`
  (if present) are bools; at least one of the three keys present;
- `bootstrap_emissions` (if present) is a list of dicts each with `action` in the existing
  `CLOSED_ACTIONS`;
- `signals` (if present) is a list of non-empty strings.

**Step 1:** Write failing tests: a type with `intake.route: "candidate"` and a valid
`birth_threshold` passes; `route: "bogus"` fails; `route: "candidate"` with no
`birth_threshold` fails; `min_independent_sources: true` (bool) fails; a
`bootstrap_emissions` action outside `CLOSED_ACTIONS` fails.
**Step 2:** Run, confirm fail.
**Step 3:** Implement the validation block.
**Step 4:** `python3 -m pytest tests/test_program_schema.py -q` green; then
`python3 scripts/program_schema.py` still prints `programtypes OK` (existing registry has
no intake blocks yet, so it must still pass).
**Step 5:** Commit.

---

### Task 2: Registry - `program-intake` type + intake blocks + seeded nursery program

**Files:**
- Modify: `cadence/programtypes/registry.json`
- Create: `datasets/programs/PROG-0014.md` (the seeded `program-intake` register program)
- Test: `tests/test_program_schema.py` (assert the shipped registry validates)

**What:**
1. Add a new type `program-intake` (`state_model: register`, `family: "system"` - add the
   family to the families block with a high order so it shelves last; `cadence: weekly`;
   `sources: [{kind: transcripts, mode: read}]`; emitters
   `[{on: "candidate-ripe", action: "propose-update"}, {on: "drift:broken", action: "escalate"}]`;
   `presentation.chip_tokens: {}`). This type has NO `intake` block (it is the nursery, not
   a discovered type).
2. Add an `intake` block to `roadmap-initiative` (the full candidate block from brief Sec.3:
   `route: candidate`, `signals`, `birth_threshold: {min_independent_sources: 2, or_explicit_declaration: true}`,
   `bootstrap_emissions: [{action: draft-ticket, template: create-tracker-initiative}, {action: propose-update, template: add-roadmap-entry}]`).
3. Add `intake: {route: "capture"}` to the cycle types (`weekly-priorities`, `eng-sync-prep`,
   `eos-cycle`) and `intake: {route: "observe"}` is the implicit default (no block needed for
   types that only take observations - document that absence means observe).
4. Add `intake` to `eos-rock`: `route: candidate`, `birth_threshold: {explicit_declaration_only: true}`,
   minimal signals.
5. Seed `datasets/programs/PROG-0014.md`: `type: program-intake`, `status: active`,
   `title: "Program intake"`, `owner_role: product`, empty `items: []`, a `status_line`,
   `policy` (candidate-aging days, e.g. 30), a `checkpoints` entry if the register model needs
   one for reconcile (mirror PROG-0013 shape), `drift: holding`. Empty `## Observations` /
   `## Cycles` stubs.

**Step 1:** Write a test asserting `program_schema.load_and_validate()` (or the module's
public validate entry) passes on the real `registry.json` after edits, and that
`program_lib.read_program("PROG-0014")` parses with `type == "program-intake"`.
**Step 2:** Run, confirm fail (type/seed absent).
**Step 3:** Make the registry + seed edits.
**Step 4:** `python3 scripts/program_schema.py` -> `programtypes OK`; pytest green.
**Step 5:** Commit.

---

### Task 3: `program_lib.upsert_candidate` - the nursery accumulation + merge

**Files:**
- Modify: `scripts/program_lib.py`
- Test: `tests/test_program_lib.py`

**What:** Add `upsert_candidate(intake_program_id, *, candidate_key, program_type, title, source, claim, anchor=None, link_to=None, confidence=None, root=None) -> dict`.
Reads the intake program, finds/creates the candidate register item, appends source-cited
evidence, writes back (append-only within the candidate's `evidence` list). Returns
`{candidate_id, action: "opened"|"merged"|"flagged", source_count}`.

Merge logic (the approved middle option):
- If `anchor` matches an OPEN candidate's `anchor` (or a normalized-title-key match of
  `title`) -> append evidence to that candidate (`action: merged`).
- Else if `link_to` resolves to an OPEN candidate AND `confidence` >= a high threshold
  (e.g. 0.8) -> append evidence to that candidate (`action: merged`).
- Else if `link_to` resolves but confidence is below threshold -> create a NEW candidate
  with `possible_duplicate_of: <link_to>` (`action: flagged`).
- Else -> create a new candidate (`action: opened`).
- Never append to a `closed-with-reason` or `birthed` candidate (those are closed; only
  material new evidence reopens, which is out of scope here - treat as a new candidate).

Candidate item shape stored under `items`:
`{ id, program_type, title, anchor, status: "open", evidence: [ {date, source, claim, sentinel} ], source_count, possible_duplicate_of? }`.
`source_count` = count of DISTINCT `source` values in evidence (the birth-threshold input).
Mint candidate ids locally (e.g. `CAND-0001` via a counter in the intake program's fm, or
derived from the items length + 1; keep deterministic and append-only).

Add a helper `close_candidate(intake_program_id, candidate_id, *, reason, root=None)` and a
`mark_candidate_birthed(intake_program_id, candidate_id, born_program_id, root=None)` for
Tasks 6/7 to call. Normalized-title-key helper `_norm_title_key(title)` (lowercase, strip
punctuation, collapse whitespace).

**Steps (TDD):** test opened (new), merged-by-anchor, merged-by-confident-link,
flagged-by-unsure-link (`possible_duplicate_of` set), distinct-source counting, and that a
closed candidate is not appended to. Then implement minimally. Gates green. Commit.

---

### Task 4: `program_lib.birth_program` - create from a birth spec

**Files:**
- Modify: `scripts/program_lib.py`
- Test: `tests/test_program_lib.py`

**What:** Add `birth_program(spec, root=None) -> str` returning the new `program_id`.
`spec = {program_type, title, checkpoints?, citations?, owner_role?, phase?}`. It:
- validates `program_type` exists in the registry;
- calls `create_program(...)` with `status: active`, `owner_role` (default a sane role
  token, never a name), inferred `phase` (first phase for a pipeline type) and `checkpoints`
  (carried from spec, each `status: pending`);
- writes the `citations` into `## Intent` (a one-line origin paragraph) and appends ONE
  origin observation (`kind: status-signal`, `sentinel: program-intake`, `source` = the
  first citation, `claim` = "Program born from intake candidate <id>.").
- Does NOT enqueue bootstrap emissions (that is the accept path's job, Task 7) - keep
  `birth_program` pure file-creation so it is unit-testable without the task queue.

**Steps (TDD):** test that a pipeline-type birth creates an active program at phase 1 with
the carried checkpoints + an origin observation; that an unknown `program_type` raises;
that the returned id is freshly minted. Implement. Gates green. Commit.

---

### Task 5: The `program-intake` sentinel + runner apply branch

**Files:**
- Create: `scripts/sentinels/program-intake.md`
- Modify: `scripts/sentinel_runner.py`
- Test: `tests/test_sentinel_runner.py` (or the existing sentinel test file)

**What:**
1. `scripts/sentinels/program-intake.md` (frontmatter: `name: program-intake`,
   `kind: sentinel`, `sources: [{kind: transcripts, mode: read}]`,
   `observation_kinds: [status-signal, capture, completion, commitment]` (the kinds it may
   stamp via the observe/capture routes), `scope: active-programs`, `model_tier: deep`,
   read-only `allowed_tools`). Body instructs: read the active program-type registry as the
   taxonomy + the open candidates in the intake register; for each new exhaust item, return a
   JSON record `{route, program_id?, program_type?, title?, anchor?, source, claim, link_to?, confidence?}`
   with `route` in the closed set. NEVER write files. ASCII only.
2. `sentinel_runner.py`: detect the intake sentinel (by name or a `routes: true` flag) and,
   for its returned records, apply by route:
   - `observe` -> `append_observation(program_id, ...)` (drop if program_id missing/inactive);
   - `capture` -> `append_observation(program_id, kind="capture", ...)`;
   - `candidate` -> `upsert_candidate(intake_program_id, ...)` (resolve the intake program by
     `type == "program-intake"`; drop if absent);
   - `ignore` -> no-op.
   Keep the existing movement-watch/tracker-truth paths untouched. The LLM never writes; the
   runner applies deterministically (same fence as movement-watch).

**Steps (TDD):** with a patched dispatch returning canned records, assert: an `observe`
record appends an observation to the named program; a `capture` appends a capture; a
`candidate` calls `upsert_candidate` on the intake program; an `ignore` is a no-op; a
`candidate` with no intake program present is dropped without raising. Then implement.
`validate_sentinel` passes on the new file. Gates green. Commit.

---

### Task 6: Reconciler - `_propose_births` producer for the intake register

**Files:**
- Modify: `scripts/cadence/reconcile.py`
- Test: `tests/test_cadence_reconcile.py`

**What:** Add `_propose_births(intake_fm, registry, body) -> list[dict]` returning birth
proposals. For each OPEN candidate item: look up its `program_type`'s `intake.birth_threshold`;
the candidate is ripe iff `source_count >= min_independent_sources` OR (the candidate carries
an explicit-declaration marker AND `or_explicit_declaration`/`explicit_declaration_only`).
Return `{op: "birth", program_type, title, candidate_id, checkpoints: <inferred or []>, citations: [sources]}`.

Wire into `_evaluate_emitters`: when the type is `program-intake` and an emitter is
`{on: "candidate-ripe", action: "propose-update"}`, call `_propose_births`, and for each
proposal create a `recommendation`/`cadence-propose-update` card (mirror the existing
propose-update branch) tagged `[intake_program_id, "cadence"]`, `proposal = birth_dict`,
deduped by **candidate_id** (new helper `_open_birth_candidate_ids(task_lib, intake_program_id)`
scanning open cadence-propose-update cards whose `proposal.op == "birth"`, collecting
`proposal.candidate_id`). Description renders the prefilled program preview.

**Steps (TDD):** seed an intake program with one ripe candidate (2 distinct sources) and one
unripe (1 source) -> reconcile emits exactly one birth proposal carrying the ripe
`candidate_id`; a second reconcile with the proposal already open emits none (dedup); an
`explicit_declaration_only` type with one declared candidate is ripe on the declaration
marker. Implement. Gates green. Commit.

---

### Task 7: Accept path - birth branch in `_apply_cadence_proposal`

**Files:**
- Modify: `ui/task-board/server/task_server.py` (or wherever `_apply_cadence_proposal` lives)
- Test: the server/proposal test module (mirror the inc3a `_apply_cadence_proposal` tests)

**What:** In `_apply_cadence_proposal`, before the `apply_mutation` call, branch on
`proposal.get("op") == "birth"`:
- `new_id = program_lib.birth_program(spec_from_proposal)`;
- for each `bootstrap_emission` of the born type: `draft-ticket` -> `create_task(queue="agent",
  task_type="ticket-creator", ...)` (or collab, matching how ticket-creator is dispatched) and
  `_dispatch_agent_task` if that is the existing pattern; `propose-update` -> a
  `recommendation`/`cadence-propose-update` card. Tag bootstrap tasks `[new_id, "cadence"]`.
  These ride existing queues; no external write happens until a task is walked (existing Tier-2);
  degrade gracefully when a provider/worker is unconfigured.
- `program_lib.mark_candidate_birthed(intake_program_id, candidate_id, new_id)` (intake program
  resolved from the proposal card's tags);
- complete the proposal card, spawn an informational receipt (`receipt_kind: cadence-apply`, no
  git revert), return the receipt id.
Reject path (existing): also call `program_lib.close_candidate(..., reason="rejected at birth proposal")`.

**Steps (TDD):** accept a birth proposal -> a new active program exists (read_program), the
candidate is `birthed` + linked, bootstrap tasks are enqueued (count matches
bootstrap_emissions), a receipt is spawned, NO git commit. Use the inc3a test isolation
(patch `task_lib.ARCHIVE_DIR` + program root) so nothing leaks. Implement. Gates green. Commit.

---

### Task 8: UI - render the candidate nursery on the program-intake row

**Files:**
- Modify: `scripts/program_lib.py` (`render_view` register branch: pass through candidate
  fields for the program-intake projection)
- Modify: `ui/task-board/js/cadence.js`
- Test: `tests/test_program_lib.py` (render_view projects candidates) + a portability check

**What:** `render_view` register branch currently projects `{name, owner, age}`. For the
program-intake program, project candidates as `{name: title, owner: program_type, age:
source_count, status, possible_duplicate_of}` (keep the existing register shape for other
register programs; map intake items into the same `items` projection with the extra fields
tolerated). `cadence.js`: render the candidate list on the program-intake row expansion -
title, target type, source count, and a `possible-duplicate-of` marker when present. Theme
tokens only; tolerant of missing fields (mirror the inc3b `cadenceItems` tolerance).

**Steps (TDD):** test `render_view` on a program-intake program surfaces its candidates with
the mapped fields; `python3 scripts/portability_gate.py` -> `portability OK`. Implement.
Gates green. Commit.

---

### Task 9: Final integration review + live readiness

**What (no new feature code unless review finds a defect):** Dispatch a final code reviewer
over the whole branch against this plan + the design doc. Confirm: the sentinel never writes;
birth is Tier-1 (no external write except a walked bootstrap task); candidate evidence is
append-only; dedup is candidate-scoped; ASCII-safe runtime strings; no identity literals
(`tests/test_engine_no_jay.py`); all five gates green. Fix any Important/Critical findings
in-branch. Then hand off to live e2e on :8743 per the design doc's E2E section.
