# Cadence Increment 4b — death + the janitor (implementation plan)

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Close the program lifecycle to `archived` (archive op + version-suffixed move + two reconciler
archive doors), add blind-sentinel telemetry, the self-hosting portfolio-health janitor, candidate aging,
and a grounding render section. All Tier-1.

**Architecture:** Extend the core engine (`program_lib` mutations + `reconcile` doors/janitor) and reuse
the existing `recommendation`/`cadence-propose-update` card and the closed-set `apply_mutation`. New data
file for sentinel telemetry. See the design: `docs/plans/2026-06-18-cadence-lifecycle-death-janitor-design.md`.

**Tech Stack:** Python 3 (stdlib), pytest, file-backed markdown+YAML programs, vanilla JS board.

**Standing contract (every task):** ASCII-safe runtime output (hyphen not em-dash, ASCII quotes).
Profile-driven identity (no person/team literals). Append-only / never delete (invariant #6). The five
gates stay green: `python3 -m pytest -q`, `python3 scripts/card_schema.py`,
`python3 -m pytest tests/test_engine_no_jay.py`, `python3 scripts/portability_gate.py`,
`python3 scripts/program_schema.py`. Inspect history with `git show`/`git diff`, never `git checkout`.

---

### Task 1: portable file-move seam

**Files:** Modify `scripts/platform_lib.py`; Test `tests/test_platform_lib.py`.

- **Step 1 (test):** `test_move_file_renames_within_tree` — create a temp file, `move_file(src, dst)`,
  assert src gone, dst present with same content. `test_move_file_creates_parent_dir` — dst under a
  not-yet-existing subdir; move creates it.
- **Step 2:** run, see it fail (no `move_file`).
- **Step 3:** add `def move_file(src, dst):` — `os.makedirs(os.path.dirname(dst), exist_ok=True)` then
  `shutil.move(src, dst)` (shutil already imported). Docstring: the single cross-platform file-move seam
  (mirrors `lock`/`resolve_tool`); raw shell `mv`/`move` is a portability bug.
- **Step 4:** tests pass.
- **Step 5:** commit `feat(platform): portable move_file seam`.

---

### Task 2: the `archive` mutation op + archived-file lookup

**Files:** Modify `scripts/program_lib.py`; Test `tests/test_program_lib.py`.

Context: `_MUTATION_OPS = frozenset({"advance-phase", "adjust-checkpoint"})` at ~line 1145;
`apply_mutation` dispatches at ~1186. `read_program` (line 204) only joins `_program_dir`.
`list_programs` (218) walks `_program_dir` top-level only (so files moved into `archive/` vanish from it).

- **Step 1 (tests):**
  - `test_apply_archive_moves_file_and_sets_status` — seed an active program, `apply_mutation(pid,
    {"op":"archive","reason":"terminal phase reached","citations":["meeting:X"]})`. Assert: returns
    `{"applied":"archive","program_id":pid,"to":<path under archive/>}`; the file no longer exists at the
    active path; a file exists at `datasets/programs/archive/<pid>.md`; its frontmatter `status ==
    "archived"`; its body has an appended `## Observations` entry of kind `completion`, sentinel
    `reconciler`, claim containing `archived`.
  - `test_apply_archive_version_suffixes_on_collision` — pre-create `archive/<pid>.md`; archive again from
    a fresh active `<pid>.md`; assert the new file lands at `archive/<pid>-v2.md` and the first is intact.
  - `test_apply_archive_idempotent_when_already_archived` — call archive twice; second returns a
    `noop`/success status, does NOT create a `-v2`, does NOT append a second observation.
  - `test_read_program_resolves_archived_file` — after archive, `read_program(pid)` returns the archived
    file (so retried accepts / lookups still resolve).
  - `test_archive_refused_no_reason` — `{"op":"archive"}` with no reason still archives with a default
    reason (don't refuse on a missing reason; archive is human-accepted upstream). [Keep behavior simple:
    reason defaults to "archived".]
- **Step 2:** run, see fail.
- **Step 3 (impl):**
  - Add `"archive"` to `_MUTATION_OPS`.
  - In `apply_mutation`, add a dispatch branch: `if op == "archive": return _apply_archive(...)`.
  - `_apply_archive(program_id, mutation, fm, type_entry, filepath, body, root)`:
    - If `fm.get("status") == "archived"` -> return `{"applied":None,"status":"noop","reason":"already
      archived","program_id":program_id}`.
    - `reason = mutation.get("reason") or "archived"`. `citations = mutation.get("citations") or []`.
    - Set `fm["status"] = "archived"`, write the file in place first (`_write_program_file`).
    - Append observation (kind `completion`, sentinel `reconciler`, source = first citation if present else
      `proposal`, claim `Program archived: {reason}.`, date today) — re-reads/rewrites the in-place file.
    - Compute archive target: `archive_dir = os.path.join(_program_dir(root), "archive")`; base
      `<pid>.md`; if exists, `<pid>-v2.md`, `-v3` ... (helper `_archive_target_path`).
    - `platform_lib.move_file(active_path, target)`.
    - return `{"applied":"archive","program_id":program_id,"to":os.path.relpath(target, ...)}` (relpath
      under programs dir, ASCII).
  - Extend `read_program`: after the active-path `isfile` check fails, look under `archive/` (glob
    `<pid>.md` and `<pid>-v*.md`, pick the newest by suffix); only raise FileNotFoundError if neither.
  - Import `platform_lib` at top of program_lib if not already.
- **Step 4:** tests pass; run `python3 scripts/program_schema.py` (still green).
- **Step 5:** commit `feat(cadence): archive mutation op + version-suffixed move`.

---

### Task 3: schema + registry — archive fields, triggers, portfolio-health type

**Files:** Modify `scripts/program_schema.py`, `cadence/programtypes/registry.json`; Test
`tests/test_program_schema.py`.

- **Step 1 (tests):**
  - `test_archive_after_silent_cycles_accepts_nonneg_int` and `..._rejects_bool` and `..._rejects_negative`
    (mirror the `min_independent_sources` bool-before-int rule).
  - `test_completion_verified_and_silent_too_long_emitter_triggers_valid` — a type with emitters
    `{on:"completion-verified",action:"propose-update"}` and `{on:"silent-too-long",action:"propose-update"}`
    passes.
  - `test_portfolio_health_type_validates` — load the real registry; assert it parses and `portfolio-health`
    is present (register model, family system).
- **Step 2:** run, fail.
- **Step 3 (impl):**
  - In `program_schema`: where emitter `on` triggers are validated, add `completion-verified` and
    `silent-too-long` to the accepted set (find the existing closed set of triggers; if triggers are not
    currently enumerated, this is just ensuring propose-update emitters with these `on` values pass).
  - Validate optional top-level `archive_after_silent_cycles`: if present, must be a non-negative int
    (`isinstance(v,bool)` rejected first).
  - registry.json: add a `portfolio-health` type: `{id:"portfolio-health", state_model:"register",
    family:"system", order:98, label:"Portfolio health", emitters:[{on:"drift:broken",action:"escalate"}]}`.
    (No intake block.) Confirm `family "system"` already declared (added for program-intake in 4a).
  - Add `archive_after_silent_cycles` to a couple of existing types where it makes sense (e.g.
    `roadmap-initiative`) — optional; default-6 applies when absent, so this is just exercising the field.
- **Step 4:** `python3 scripts/program_schema.py` -> `programtypes OK`; pytest green.
- **Step 5:** commit `feat(cadence): schema+registry for archive triggers + portfolio-health type`.

---

### Task 4: the FACT archive door

**Files:** Modify `scripts/cadence/reconcile.py`; Test `tests/test_cadence_reconcile.py`.

Context: `_evaluate_emitters` (line 821) has branches keyed on `(action, on)`; the birth branch
(`action=="propose-update" and on=="candidate-ripe"`, ~890) is the template. `_open_propose_update_ops`
(426) dedupes by op. `_terminal_phase` lives in program_lib.

- **Step 1 (tests):**
  - `test_propose_archive_fires_on_terminal_phase` — a pipeline program whose `phase` is the terminal
    phase; `_propose_archive(fm, type_entry, body)` returns `{op:"archive", reason:..., citations:[...]}`.
  - `test_propose_archive_fires_on_did_it_work_verified` — a program with a `did-it-work` checkpoint
    `status: met`/`verified` -> archive mutation.
  - `test_propose_archive_none_when_active_midphase` — a mid-pipeline program -> None.
  - `test_evaluate_emitters_creates_archive_card_and_dedupes` — a type with
    `{on:"completion-verified",action:"propose-update"}`; on a terminal program, `_evaluate_emitters`
    creates one `recommendation`/`cadence-propose-update` card with `proposal.op=="archive"`, tagged
    `[program_id,"cadence"]`; a second evaluation creates none (op-dedupe).
- **Step 2:** run, fail.
- **Step 3 (impl):**
  - `_propose_archive(fm, type_entry, body)`: return an archive mutation when ANY fact holds —
    `_terminal_phase(type_entry, fm.get("phase"))`; OR a checkpoint with id/kind containing `did-it-work`
    has status in {`met`,`verified`}; OR a tracker-closed observation in the body (scan for a `completion`
    obs whose source cites a tracker AND claim mentions closed — keep tolerant/simple). `reason` describes
    which fact; `citations` = the supporting evidence (terminal phase name / checkpoint id / obs source).
    Return None otherwise.
  - Add branch in `_evaluate_emitters`: `elif action == "propose-update" and on == "completion-verified":`
    -> `mutation = _propose_archive(fm, type_entry, body or "")`; if None continue; lazily compute
    `open_prop_ops`; if `"archive" in open_prop_ops` continue; create the card (mirror the advance-phase
    propose-update card, title `f"{title}: archive (complete)?"`, description via new
    `_build_archive_description(mutation, program_id)`), append id, `open_prop_ops.add("archive")`.
  - `_build_archive_description(mutation, program_id)`: ASCII one-liner naming the reason + citations +
    the program backlink.
- **Step 4:** tests + `program_schema.py` green.
- **Step 5:** commit `feat(cadence): fact archive door (completion-verified)`.

---

### Task 5: blind-sentinel telemetry

**Files:** Modify `scripts/sentinel_runner.py`; Test `tests/test_sentinel_runner.py`.

Context: `run_sentinel` (464) has multiple early returns. Wrap it so telemetry stamps on every path.

- **Step 1 (tests):**
  - `test_record_and_read_sentinel_run_roundtrip` — `record_sentinel_run("movement-watch", success=True,
    emitted_count=3, root=tmp)` then `read_sentinel_runs(tmp)["movement-watch"]` has `last_run`,
    `last_success`, `last_emitted_count==3`, `last_error is None`.
  - `test_record_sentinel_run_error_sets_last_error_keeps_last_success` — record success, then record an
    errored run; `last_error` set, `last_success` still the earlier timestamp, `last_run` updated.
  - `test_run_sentinel_stamps_telemetry` — run a sentinel that no-ops (e.g. unconfigured adapter path or a
    stubbed dispatch returning nothing) with a tmp root; assert `sentinel-runs.json` now has that
    sentinel's `last_run`.
- **Step 2:** run, fail.
- **Step 3 (impl):**
  - `_sentinel_runs_path(root)` -> `datasets/cadence/sentinel-runs.json` under root.
  - `read_sentinel_runs(root)` -> dict (empty if missing/malformed).
  - `record_sentinel_run(name, *, success, emitted_count, error=None, root=None, now=None)`: read, update
    that key (`last_run`=now-iso; if success `last_success`=now; `last_emitted_count`=emitted_count;
    `last_error`=error), atomic write (temp file + `os.replace`), `os.makedirs(..., exist_ok=True)`.
  - Rename the current `run_sentinel` body to `_run_sentinel_impl(name, root, now)`; new `run_sentinel`
    wraps it in try/except: on success `record_sentinel_run(name, success=True,
    emitted_count=summary.get("appended",0), root, now)`; on exception record `success=False, error=str(e)`
    and re-raise-as-summary (preserve "never raises out" — return the summary). Return the summary.
- **Step 4:** tests green.
- **Step 5:** commit `feat(cadence): per-sentinel last-run telemetry`.

---

### Task 6: the INTERPRETATION (silent) archive door

**Files:** Modify `scripts/cadence/reconcile.py`; Test `tests/test_cadence_reconcile.py`.

- **Step 1 (tests):**
  - `test_propose_archive_silent_fires_when_silent_and_sentinel_live` — a program whose latest observation
    is > `cadence_period_days * N` ago (N from `archive_after_silent_cycles` default 6), with telemetry
    showing the relevant sentinel ran recently -> archive mutation with `reason` mentioning silent/dormant.
  - `test_propose_archive_silent_suppressed_when_sentinel_blind` — same silence, but telemetry shows the
    sentinel is blind (no `last_run` / stale / `last_error`) -> None (no proposal).
  - `test_propose_archive_silent_none_when_recently_active` — recent observation -> None.
  - `test_silent_and_fact_dedupe_to_one_archive_card` — both doors would fire; only one archive card (op
    dedupe).
- **Step 2:** run, fail.
- **Step 3 (impl):**
  - `_days_since_last_activity(fm, body, now)` — max date among latest observation entry, `last_cycle`
    week, last emission; days from `now`.
  - `_sentinel_is_live(telemetry, *, now, sentinel="movement-watch", stale_days=...)` — True iff a
    `last_run` exists, no `last_error`, and `last_run` within `stale_days`.
  - `_propose_archive_silent(fm, type_entry, body, telemetry, now)` — compute N (type
    `archive_after_silent_cycles` or 6); `period_days` from the type cadence (`_resolve_cadence`/a
    days-per-period map; weekly=7, monthly=30, quarterly=90; default 7); if days_silent >= period_days*N
    AND `_sentinel_is_live(telemetry,...)` -> `{op:"archive", reason:"dormant: silent N cycles",
    citations:[...]}`; else None.
  - Wire a branch `elif action=="propose-update" and on=="silent-too-long":` reading telemetry via
    `sentinel_runner.read_sentinel_runs(root)` (lazy import), same op-dedupe as Task 4.
  - `_evaluate_emitters` already takes `root`; pass `now` through (it has `period`; add `now` param or
    reuse). Keep the fact branch and silent branch both adding to the same `open_prop_ops`.
- **Step 4:** tests + gates green.
- **Step 5:** commit `feat(cadence): interpretation (silent) archive door, gated on sentinel liveness`.

---

### Task 7: candidate aging (4a M-3)

**Files:** Modify `scripts/program_lib.py`, `scripts/cadence/reconcile.py`; Test `tests/test_program_lib.py`,
`tests/test_cadence_reconcile.py`.

Context: `upsert_candidate` (818) creates candidate items; `_verdict_register` reads `it.get("age")`.

- **Step 1 (tests):**
  - `test_upsert_candidate_stamps_opened` — a newly opened candidate item carries an `opened` ISO date.
  - `test_intake_reconcile_ages_open_candidates` — reconcile a program-intake register whose open candidate
    was opened 20 days ago (policy 30 -> drifting at >0.8*30=24? choose dates to land drifting/broken
    deterministically); assert the candidate item gets an `age` and the verdict reflects it.
- **Step 2:** run, fail.
- **Step 3 (impl):**
  - In `upsert_candidate`, on the "opened" branch set `item["opened"] = _now_iso()[:10]`.
  - In the reconcile path for a `program-intake` register (where `_propose_births` already special-cases
    it), before computing the verdict, compute each OPEN candidate's `age` = days(now - opened) and write it
    onto the item (in-memory is enough for the verdict; persist via the existing single write). Closed/
    birthed candidates get no age (skipped by `_verdict_register`'s isinstance check).
- **Step 4:** tests + gates green.
- **Step 5:** commit `fix(cadence): age open intake candidates (4a M-3)`.

---

### Task 8: the portfolio-health janitor

**Files:** Create `datasets/programs/PROG-0015.md`, modify `datasets/programs/_counter`,
`scripts/cadence/reconcile.py`; Test `tests/test_cadence_reconcile.py`, a seed-render test in
`tests/test_program_lib.py`.

- **Step 1 (tests):**
  - `test_scan_portfolio_health_flags_blind_sentinel` — telemetry with a blind sentinel ->
    `_scan_portfolio_health` returns a finding of kind `blind-sentinel` (broken severity).
  - `test_scan_portfolio_health_flags_stale_active` — an active program silent past N with a live sentinel
    -> a `stale-active` finding.
  - `test_scan_portfolio_health_flags_aging_candidate` — an aging open candidate -> `aging-candidate`.
  - `test_scan_portfolio_health_flags_duplicates` — two active programs with near-identical titles ->
    `duplicate` finding (flag-only).
  - `test_reconcile_portfolio_health_refreshes_items_and_escalates_blind` — reconcile PROG-0015 with a
    blind sentinel; its `items` are refreshed with findings AND an `escalate` human card is created (drift
    broken). A second reconcile dedupes the escalate card.
  - `test_portfolio_health_seed_parses_active` + extend `test_all_seed_programs_render`.
- **Step 2:** run, fail.
- **Step 3 (impl):**
  - `datasets/programs/_counter` -> `16`.
  - Seed `PROG-0015.md`: `program_id: PROG-0015`, `type: portfolio-health`, `title: Portfolio health`,
    `status: active`, `owner_role: product`, `items: []`, `policy: 30`, `state_model` implied by type,
    `drift: holding`, `last_cycle`/`last_run` set to a fresh-but-past week (W24 so the first reconcile is
    fresh, mirroring the 4a PROG-0014 fix), standard `## Intent`/`## Observations`/`## Cycles`.
  - `_scan_portfolio_health(root, now)`: read `list_programs(status="active")`, the intake register's open
    candidates, and `sentinel_runner.read_sentinel_runs(root)`. Build findings list (each a dict
    `{name, owner, age?, status, severity, kind}` shaped for the register verdict + render):
    - blind-sentinel (per blind sentinel) severity broken;
    - stale-active (silent + live) severity drifting;
    - aging-candidate (open candidate age past intake policy) drifting;
    - duplicate (title similarity via a cheap normalized-token Jaccard >= threshold) holding/flag;
    - supply: count active programs per family vs a floor (e.g. < 1 refined roadmap-initiative) -> finding.
    Map severity to an `age`-like signal the existing `_verdict_register` can read, OR have the janitor set
    `drift` directly from the worst finding severity (simplest: compute verdict from findings, bypass the
    age math). Keep findings ASCII.
  - Hook reconcile: when reconciling a `portfolio-health` program, call `_scan_portfolio_health` and write
    the findings into `items` before/while computing the verdict; the existing `escalate` emitter on
    `drift:broken` then fires for a blind sentinel.
- **Step 4:** tests + all five gates green (incl. `program_schema.py` and the seed-render test).
- **Step 5:** commit `feat(cadence): portfolio-health janitor (seed + scan + escalate)`.

---

### Task 9: grounding render section + binding-health

**Files:** Modify `scripts/program_lib.py` (`render_view`); Test `tests/test_program_lib.py`.

- **Step 1 (tests):**
  - `test_render_view_includes_grounding` — render a program with observations + citations; the view has a
    `grounding` block: `citations` count, `last_observation` date, `sentinel_live` (bool/“unknown” when no
    telemetry), and `binding_warnings` (list).
  - `test_render_view_binding_warning_for_target_without_instrument` — a `target` program whose `metric`
    instrument is missing -> a binding warning.
- **Step 2:** run, fail.
- **Step 3 (impl):** in `render_view`, add a `grounding` key to the returned dict (data only, no styling):
  derive citation count (from ## Intent origin / observations), last observation date, sentinel liveness
  (read telemetry if a `root` is threaded; else "unknown"), and binding warnings (target w/o resolvable
  metric instrument; pipeline w/o tracker reference). Render-only; no external calls. ASCII strings.
- **Step 4:** tests green.
- **Step 5:** commit `feat(cadence): grounding render section + binding-health`.

---

### Task 10: board UI — janitor row, grounding, archived handling

**Files:** Modify `ui/task-board/js/cadence.js`; (data already from `/api/cadence`). Visual/e2e verify.

- **Step 1:** Render a `portfolio-health` register row: findings list (kind + severity, theme-token
  colored by drift), tolerant of the findings shape. Render the `grounding` block on row expansion
  (citations count, last-observation date, sentinel-live indicator, binding warnings). Ensure archived
  programs do not appear (they leave `list_programs`; confirm `/api/cadence` excludes them). Theme tokens
  only; ASCII text (no em-dash, no non-ASCII glyphs — mirror the 4a `cadence.js` fix).
- **Step 2:** No JS unit harness — verify via the live board in Task 11's e2e + a 6-Mood visual pass.
- **Step 3:** commit `feat(cadence): janitor row + grounding in the Cadence tab`.

---

### Task 11: accept/reject wiring + verification

**Files:** Verify/extend `scripts/task_server.py`; Test `tests/test_card_actions.py`.

Context: `_apply_cadence_proposal` (1100) routes `op=="birth"` to `_apply_cadence_birth`, else calls
`apply_mutation(program_id, proposal)`. Since `archive` is now in `_MUTATION_OPS`, accept flows through
`apply_mutation` with NO new branch. Reject of an archive proposal must just cancel the card (no candidate
to close — unlike birth).

- **Step 1 (tests):**
  - `test_accept_archive_proposal_moves_file` — create a cadence-propose-update card with
    `proposal={op:"archive",reason:...}` tagged `[pid,"cadence"]` for an active program; POST accept via
    the server handler; assert the program file moved to `archive/`, status archived, card completed, an
    informational receipt spawned (receipt_kind cadence-apply, NO git revert), HEAD unchanged.
  - `test_reject_archive_proposal_cancels_card_no_move` — reject; assert the program is untouched (still
    active, still in place) and the card is cancelled. Confirm the birth-reject `close_candidate` path is
    NOT taken for an archive op.
- **Step 2:** run, fail (or pass if the generic path already works — then these are regression locks).
- **Step 3 (impl):** ensure the reject branch (the `_apply_cadence_birth`-reject sibling at ~1463 that
  checks `op=="birth"`) does not mis-handle `op=="archive"`; archive reject = plain card cancel. Add no new
  accept branch (apply_mutation handles archive). Fix only if a test reveals a gap.
- **Step 4:** all five gates green.
- **Step 5:** commit `feat(cadence): archive accept/reject wiring + regression locks`.

---

### Final review

After all tasks: dispatch the final code-reviewer over the whole branch (spec compliance vs this plan +
the design doc, the five gates, ASCII-safe runtime output, invariant #6 archive). Then the live e2e on
:8743 per the design doc, restore seeds + `_counter` after, then `finishing-a-development-branch`.
