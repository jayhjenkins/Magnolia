# Cadence Interpretation Engine (3a: slices 5+6) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this
> plan task-by-task. Each task is briefed from the build contract in
> `docs/plans/2026-06-17-cadence-interpretation-engine-design.md`.

**Goal:** Give Cadence its first agents (sentinels) and its interpretation door (propose-update +
ladder), with checkpoint-driven phase advancement — all Tier-1 internal (no external write).

**Architecture:** Sentinels run on the cron→`claude -p` dispatch path and return structured
observations a deterministic harness appends to programs; the in-process reconciler reads those
observations and advances phases (fact door) or emits `propose-update` recommendation cards
(interpretation door) whose accept applies a closed set of program mutations, governed by the
existing `ladder_lib`/`enforce_lib` at shadow tier.

**Tech Stack:** Python 3, pytest, PyYAML, the existing `program_lib`/`reconcile`/`task_lib`/
`ladder_lib`/`enforce_lib`/adapter substrate. Files-based, ASCII-safe runtime output (invariant #8).

**Conventions for every task:**
- TDD: failing test first, watch it fail, minimal impl, watch it pass, commit.
- Gates green before each commit: `python3 -m pytest`, `python3 scripts/card_schema.py`
  (→ `registry.json OK`), `python3 -m pytest tests/test_engine_no_jay.py`,
  `python3 scripts/portability_gate.py` (→ `portability OK`), `python3 scripts/program_schema.py`
  (→ `programtypes OK`).
- ASCII-safe runtime strings (hyphen, never em-dash). Append-only evidence (invariant #6). No
  identity literals (invariant #1) — denylist scan covers `cadence/**`, `scripts/sentinels/**`.
- Test isolation: monkeypatch `program_lib._program_dir`/`_counter_path` (and `task_lib.TASKS_DIR`
  + `task_lib.COUNTER_FILE` together) to a tmp dir; never touch real `datasets/`.
- Inspect prior art with `git show`/`grep`, never `git checkout` (it derails the working tree).

---

### Task 1: Observation write contract on `program_lib`

**Files:**
- Modify: `scripts/program_lib.py` (add `append_observation`, near `_parse_observations` ~line 314,
  and the closed kind enum near `_OBS_HEADER_RE` ~line 270)
- Test: `tests/test_program_lib_observations.py` (new)

The foundation: a deterministic, append-only writer for `## Observations`. The LLM never writes
files — sentinels (Task 3) hand records here. Mirror `reconcile._append_cycle_entry` for the
heading-anchored insert and `program_lib._write_program_file` for the write.

**Step 1 — Failing tests.** Cover: a well-formed observation appends under `## Observations` with
header `### <date> - sentinel:<name> [<kind>]` + `source:` + `claim:` lines; append-only (a second
distinct observation keeps the first); kind outside the closed enum raises `ValueError`; a missing
source raises `ValueError`; **content-hash dedupe** — re-appending an identical `(kind, source,
claim)` is a no-op (returns `False`/`None`, file unchanged); the `## Cycles` section (if present
after Observations) is preserved verbatim.

```python
OBS_KINDS = {"status-signal", "date-change", "completion", "commitment",
             "risk", "metric", "capture", "blocker"}

def test_append_observation_writes_under_section(tmp_path, monkeypatch):
    _seed_program(tmp_path, monkeypatch, "PROG-0001")
    ok = program_lib.append_observation(
        "PROG-0001", kind="status-signal", sentinel="movement-watch",
        source="datasets/meetings/2026-06-11_x.md (#Action Items)",
        claim="Discovery spike reported complete.", root=str(tmp_path))
    assert ok is True
    prog = program_lib.read_program("PROG-0001", root=str(tmp_path))
    assert "sentinel:movement-watch [status-signal]" in prog["body"]
    assert "Discovery spike reported complete." in prog["body"]

def test_append_observation_dedupes_identical(tmp_path, monkeypatch):
    _seed_program(tmp_path, monkeypatch, "PROG-0001")
    kw = dict(kind="completion", sentinel="movement-watch",
              source="datasets/meetings/x.md", claim="Done.", root=str(tmp_path))
    assert program_lib.append_observation("PROG-0001", **kw) is True
    assert program_lib.append_observation("PROG-0001", **kw) is False  # dedupe
    body = program_lib.read_program("PROG-0001", root=str(tmp_path))["body"]
    assert body.count("Done.") == 1

def test_append_observation_rejects_bad_kind(tmp_path, monkeypatch):
    _seed_program(tmp_path, monkeypatch, "PROG-0001")
    with pytest.raises(ValueError):
        program_lib.append_observation("PROG-0001", kind="vibes",
            sentinel="x", source="s", claim="c", root=str(tmp_path))

def test_append_observation_requires_source(tmp_path, monkeypatch):
    _seed_program(tmp_path, monkeypatch, "PROG-0001")
    with pytest.raises(ValueError):
        program_lib.append_observation("PROG-0001", kind="risk",
            sentinel="x", source="", claim="c", root=str(tmp_path))
```

**Step 2 — Run, verify fail** (`append_observation` undefined).

**Step 3 — Implement.** Add `OBSERVATION_KINDS` frozenset. `append_observation(program_id, *, kind,
sentinel, source, claim, date=None, confidence=None, root=None) -> bool`: validate `kind in
OBSERVATION_KINDS` and non-empty `source`/`claim` (else `ValueError`); `read_program`; compute a
content hash over `(kind, source.strip(), claim.strip())`; if an existing observation matches
(parse via the existing `_parse_observations` plus a raw-body substring check on the claim+source),
return `False`; else build the entry (ASCII hyphen header `### {date} - sentinel:{sentinel}
[{kind}]`, `source:`/`claim:` lines, optional `confidence`), insert it under `## Observations` and
before the next `## ` heading (adapt `_split_at_next_section` logic — extract a shared helper if
clean, else replicate locally), write via `_write_program_file`, return `True`. `date` defaults to
`_now_iso()[:10]`.

**Step 4 — Run, verify pass.**

**Step 5 — Commit:** `feat(cadence): append-only observation writer with kind enum + dedupe`

---

### Task 2: Sentinel definition schema + gate validation

**Files:**
- Create: `scripts/sentinels/movement-watch.md`, `scripts/sentinels/tracker-truth.md`
- Create: `scripts/sentinel_lib.py` (load/parse/validate sentinel defs — mirror how
  `scripts/workers/` defs are loaded; find the worker loader with `grep -rn "workers" scripts/`)
- Modify: `scripts/program_schema.py` (validate sentinel defs as part of the gate)
- Test: `tests/test_sentinel_lib.py` (new), extend `tests/test_program_schema.py`

**Step 1 — Failing tests.** `sentinel_lib.load_sentinel("movement-watch")` returns a dict with
`name`, `kind == "sentinel"`, `sources` (all `mode == "read"`), `observation_kinds` (⊆
`program_lib.OBSERVATION_KINDS`), and a non-empty prompt body. `list_sentinels()` returns both.
Validation rejects: a `mode: write` source, an out-of-enum observation kind, a missing prompt body.
Extend the schema-gate test: a sentinel def with a write source / bad kind fails
`program_schema.main()`; the shipped defs pass.

**Step 2 — Run, verify fail.**

**Step 3 — Implement.** Author the two sentinel `.md` files per the design (frontmatter:
`name`, `kind: sentinel`, `sources`, `observation_kinds`, `scope`; body = the read-and-attribute
prompt — identity-free, role references only). `sentinel_lib.py`: `_SENTINEL_DIR`, `load_sentinel`,
`list_sentinels`, `validate_sentinel(def) -> list[str]` (errors). Wire `program_schema.py` to load
every `scripts/sentinels/*.md`, run `validate_sentinel`, and fail the gate on any error
(ASCII-safe messages). Keep the denylist scan covering `scripts/sentinels/**`.

**Step 4 — Run, verify pass + gate green.**

**Step 5 — Commit:** `feat(cadence): sentinel definition schema + gate validation + two sentinels`

---

### Task 3: `sentinel_runner.py` — the dispatch harness

**Files:**
- Create: `scripts/sentinel_runner.py` (mirror `scripts/adapt_runner.py` structure)
- Test: `tests/test_sentinel_runner.py` (new)

The cron→`claude -p` harness: load a sentinel def, gather in-window sources, dispatch, parse the
returned observation records, append them via `program_lib.append_observation`. The LLM call is
mocked in tests; the runner's job is orchestration + robust parsing.

**Step 1 — Failing tests.** With the `claude -p` call mocked to return well-formed JSON
(`[{"program_id","kind","source","claim","confidence"}]`), `run_sentinel("movement-watch", root=...)`
appends one observation per record to the cited program and returns a summary
(`{"sentinel","appended":N,"dropped":M}`). Malformed JSON → 0 appended, logged, no raise.
A record with an unknown `program_id` → dropped (not force-attributed). An unattributed record
(`program_id` null/empty) → dropped. `tracker-truth` when the PM adapter is unconfigured → 0
appended, clean no-op (Task 4 supplies the adapter check; here stub it).

**Step 2 — Run, verify fail.**

**Step 3 — Implement.** `run_sentinel(name, root=None, now=None)`: `sentinel_lib.load_sentinel`;
resolve scope (active programs via `program_lib.list_programs(status="active")` + their `## Intent`);
build the prompt (def body + program context + in-window source digest); call the dispatch seam
(factor the `claude -p` call into a small function the test monkeypatches, as adapt_runner does);
parse JSON defensively; for each record validate the `program_id` is a known active program and
attempt `append_observation` (its own validation/dedupe is the second fence); tally
appended/dropped; ASCII-safe logging. Never raise out of a single bad record or a bad run.

**Step 4 — Run, verify pass.**

**Step 5 — Commit:** `feat(cadence): sentinel_runner dispatch harness with defensive parsing`

---

### Task 4: PM adapter read op + `tracker-truth` grounding

**Files:**
- Modify: `scripts/adapters/project_management/_contract.py` (add `fetch_status` to the Protocol),
  `asana.py`, `jira.py` (implement or raise `NotConfigured`)
- Modify: `scripts/sentinel_runner.py` (the `tracker-truth` path consumes the adapter)
- Test: `tests/test_pm_adapter_read.py` (new), extend `tests/test_sentinel_runner.py`

**Step 1 — Failing tests.** The adapter exposes `fetch_status(issue_key, root=None) ->
{"status","title","due"} | None`; raises/returns the unconfigured signal cleanly when creds absent
(mirror how `publish` handles `is_configured`/`NotConfigured`). `tracker-truth` via `run_sentinel`
with a stubbed-configured adapter emits adapter-grounded `completion`/`status-signal`/`date-change`
observations matched to programs by `links.tracker_epic`; with the adapter unconfigured it appends
0 and logs once.

**Step 2 — Run, verify fail.**

**Step 3 — Implement.** Extend the contract + both backends (`fetch_status` returns `None`/raises
`NotConfigured` when not configured — do NOT fabricate). In `sentinel_runner`, the `tracker-truth`
branch: if `not adapter.is_configured()` → log once, return the empty summary; else for each active
program with a `links.tracker_epic`, `fetch_status`, map mechanically to an observation kind
(closed status → `completion`, due change → `date-change`, else `status-signal`), append with the
adapter as the cited source. No free interpretation.

**Step 4 — Run, verify pass.**

**Step 5 — Commit:** `feat(cadence): PM adapter read op + tracker-truth grounding (degrades when unconfigured)`

---

### Task 5: Checkpoint-driven phase advancement (fact door)

**Files:**
- Modify: `scripts/reconcile.py` → actually `scripts/cadence/reconcile.py` (add advancement to
  `reconcile_program`, helper in the pipeline section)
- Modify: `cadence/programtypes/registry.json` (add `exit_checkpoint` to `roadmap-initiative` phases
  where it makes sense; keep `eos-rock` too)
- Modify: `scripts/program_schema.py` (validate `phases[].exit_checkpoint` references a real
  per-instance checkpoint id only where statically checkable — validate it's a string here; the
  cross-check against instance checkpoints is runtime)
- Test: extend `tests/test_cadence_reconcile.py`, `tests/test_program_schema.py`

**Step 1 — Failing tests.** Given a pipeline program whose current phase declares
`exit_checkpoint: discovery-exit`: if that checkpoint has `status: met` AND an
adapter/deterministic `instrument` (not `human-attested`) → `reconcile_program` advances `phase` to
the next phase, sets `phase_entered` to today, appends a `completion` observation citing the
checkpoint + a cycle note. If the checkpoint is `human-attested` and met → **no auto-advance**
(that path is Task 6's proposal). Terminal phase → no-op. Advancement is idempotent (a second
reconcile in-period does not re-advance). Schema gate accepts `exit_checkpoint` strings.

**Step 2 — Run, verify fail.**

**Step 3 — Implement.** In the fresh-cycle branch of `reconcile_program`, before writing: a
`_maybe_advance_phase(fm, type_entry, now)` that finds the current phase's `exit_checkpoint`,
locates that checkpoint in `fm["checkpoints"]`, and if `status == "met"` and the instrument is
non-`human-attested`, mutates `fm["phase"]`/`fm["phase_entered"]`, returns an advancement record
(old→new, checkpoint id) so the caller appends a `completion` observation (via
`program_lib.append_observation`) and notes it in the cycle entry. Respect terminal phases. Keep it
inside the single program-file write where possible (observation append + frontmatter write — note
`append_observation` re-reads/writes; acceptable, or thread the body through — implementer's call,
documented).

**Step 4 — Run, verify pass + gates green.**

**Step 5 — Commit:** `feat(cadence): checkpoint-driven phase advancement (fact door)`

---

### Task 6: `propose-update` emitter + interpretation-door advancement

**Files:**
- Modify: `scripts/cadence/reconcile.py` (`_evaluate_emitters`: act on `propose-update`)
- Modify: `cadence/programtypes/registry.json` (add `propose-update` emitters: `phase_overage` /
  `phase-complete-signal` on `roadmap-initiative`)
- Test: extend `tests/test_cadence_reconcile.py`

**Step 1 — Failing tests.** A `human-attested` exit checkpoint with a high-confidence
`movement-watch` `completion`/`status-signal` observation citing it → `_evaluate_emitters` produces
a `propose-update` recommendation card (mutation = `advance-phase`) tagged `[program_id, "cadence"]`,
deduped (no second card while one is open for the same program+mutation). A phase in overage (inc2's
existing signal) → `propose-update` (phase-stall) rather than only `escalate`. Below the confidence
threshold → no proposal. The card carries a structured mutation spec the applier (Task 7) reads.

**Step 2 — Run, verify fail.**

**Step 3 — Implement.** Extend `_evaluate_emitters` to handle `action == "propose-update"`: build a
recommendation card (find the existing recommendation-card creation path — `grep -rn
"recommendation" scripts/`; reuse it, no new card type) whose body is the proposed diff + citations
and whose frontmatter carries `program_id`, `mutation` (`{op, ...}` from the closed set), and tags.
Dedupe against open cards already carrying `(program_id, mutation-op)`. Add a
`_propose_phase_advance(...)` producing the `advance-phase` mutation when interpretation-door
conditions hold (read the program's observations via `program_lib._parse_observations` or a richer
accessor). Keep `escalate` behavior intact.

**Step 4 — Run, verify pass.**

**Step 5 — Commit:** `feat(cadence): propose-update emitter + interpretation-door phase proposals`

---

### Task 7: Proposal applier + accept wiring + ladder routing

**Files:**
- Modify: `scripts/program_lib.py` (add `apply_mutation(program_id, mutation, root=None)` — closed
  set)
- Modify: the recommendation-card accept handler (find via `grep -rn "accept" scripts/task_server.py
  scripts/*.py`) to route cadence proposals through `enforce_lib` under
  `task_type="cadence-propose-update"` and call `apply_mutation` on accept
- Test: `tests/test_program_apply_mutation.py` (new), extend the accept-flow test

**Step 1 — Failing tests.** `apply_mutation(pid, {"op":"advance-phase","to":"planning"})` sets
`phase`/`phase_entered` + appends a fact observation + cycle note; `apply_mutation(pid,
{"op":"adjust-checkpoint","id":"ship","due":"2026-10-01"})` changes the date; `{"op":
"adjust-checkpoint","id":"discovery-exit","status":"met"}` marks met (and may cascade to advance via
Task 5's helper); an out-of-set op raises/refuses with no mutation; advancing past terminal no-ops.
Accept of a cadence proposal card applies the mutation; reject leaves the program unchanged and logs
the decline (append-only). At **shadow** tier `enforce_lib` does NOT auto-apply (propose-only).

**Step 2 — Run, verify fail.**

**Step 3 — Implement.** `apply_mutation` with a dispatch on `op` over the closed set
(`advance-phase`, `adjust-checkpoint`), each append-only + ASCII-safe, refusing unknown ops. Wire
the accept handler: when accepting a card tagged `cadence` carrying a `mutation`, route through
`enforce_lib` with `task_type="cadence-propose-update"` (defaults to shadow → propose-only) and call
`apply_mutation` on the apply path; record the receipt. No second shipper — reuse the existing
enforce/judge path.

**Step 4 — Run, verify pass + gates green.**

**Step 5 — Commit:** `feat(cadence): proposal applier (closed mutation set) + accept wiring at shadow`

---

### Task 8: UI — observation ledger + emission history in row expansion

**Files:**
- Modify: `scripts/program_lib.py` `render_view` (project observations + emission history into the
  view model if not already)
- Modify: the Cadence tab JS/template (find via `grep -rn "cadence" ui/task-board/`) — row expansion
  renders the observation ledger (with source links) + emission history (sent/approved/declined),
  theme tokens only
- Test: extend `tests/test_program_lib.py` (render_view projects observations); a board-render
  assertion if the harness supports it

**Step 1 — Failing tests.** `render_view` exposes an `observations` list (date, kind, sentinel,
source, claim) and an `emissions`/`needs_you` summary for the row expansion, theme-token-only
presentation fields. No identity literals.

**Step 2 — Run, verify fail.**

**Step 3 — Implement.** Extend `render_view` to project the parsed observations + emission history.
Update the Cadence tab row-expansion template to render them per brief §7 (observation ledger with
source links; emission history with outcomes), using theme tokens only — no hardcoded color/radius.

**Step 4 — Run, verify pass + gates green.**

**Step 5 — Commit:** `feat(cadence): observation ledger + emission history in Cadence row expansion`

---

## After all tasks

1. Dispatch a final whole-implementation code review (superpowers:code-reviewer) against this plan +
   the design doc + the invariants.
2. **Live e2e on :8743** (restart the board): run `movement-watch` over a real transcript → observe
   an observation land on a program with a citation → a `human-attested` checkpoint triggers a
   `propose-update` card → **accept** it → the program's `phase` advances + the cycle log records it.
   (`tracker-truth` covered by unit tests behind the unconfigured adapter.) Restore pristine seeds +
   remove e2e-created cards afterward (keep the merge pure code).
3. superpowers:finishing-a-development-branch → **merge to local main** (not pushed).
4. Update memory: `magnolia-cadence-build-sequence` (inc3a done; inc3b = slice 4 next) + a new
   `magnolia-cadence-interpretation-engine` note.
