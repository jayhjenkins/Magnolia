# Cadence reconcile engine — implementation plan (Increment 2: slices 3 + 7)

> **For Claude:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development — fresh subagent per
> task, two-stage review (spec-compliance then code-quality).

**Goal:** A deterministic, cron-fired reconciler that computes a drift verdict per active program
(all four state models), writes it back, appends a cycle log, and fires one `escalate` emitter → an
internal Now card. Tier-1, no agents, ASCII-safe, five gates green.

**Architecture:** `2026-06-16-cadence-reconcile-engine-design.md`. Builds on the slices-1+2
substrate (`program_lib`, `cadence/programtypes/registry.json`, `program_schema.py`, the tab).

**Tech stack:** Python 3, ruamel.yaml (via `program_lib`), `task_lib.create_task`, the
`platform_lib` seam (locking lives in `program_lib`), daemon thread (mirrors `cron_scheduler.py`).

**Invariants in play:** #1 (no identity literals — role-based only), #2/#9 (five gates), #6
(append-only; never rewrite observations), #8 (ASCII-safe runtime output — hyphen not em-dash; no
hand-rolled OS/shell). Tier-1 (no external writes — `escalate` is a local card).

**Cross-cutting rules for every task:** injectable `now` (never bare `date.today()` in logic — pass
it through); defensive date parsing (a non-ISO `due` is skipped, never raises); `holding` on missing
data; run the five gates before each commit; commit with the `Co-Authored-By` trailer.

---

### Task 1: package + `current_period` + the four pure verdict functions

**Files:**
- Create: `scripts/cadence/__init__.py` (empty)
- Create: `scripts/cadence/reconcile.py`
- Test: `tests/test_cadence_reconcile.py`

**Spec.** Pure, no-I/O helpers (everything takes `now`, returns data):

- `current_period(cadence, now)` → `weekly`/`None`/unknown → `f"{y}-W{w:02d}"` from
  `now.isocalendar()`; `daily` → `now.isoformat()[:10]`.
- `_parse_iso_date(value)` → `datetime.date` or `None` (accepts `date`/`datetime`/`"YYYY-MM-DD"`;
  anything else, including human strings like `"Mon Jun 16"`, returns `None` — never raises).
- `compute_verdict(program, registry, now)` → `(verdict, facts)` where `verdict ∈
  {"holding","drifting","broken"}` and `facts` is a short dict (e.g. `{"reason": "...",
  "next": "..."}`) used later for the cycle-log line and card body. Dispatches on the type's
  `state_model` (resolve via the registry, like `render_view`):
  - **pipeline**: pending checkpoints (`status` not in `{met, missed}` and a parseable ISO `due`):
    `due < now` → broken; `now <= due <= now+7d` → drifting. A `status: missed` checkpoint → broken.
    Current phase: find its `max_age_days` in the registry type's `phases`; `entered =
    phase_entered[phase]` (tolerate dict or scalar, like `render_view`); if both present:
    `days_in_phase > max_age_days` → broken; `> 0.8*max_age_days` → drifting. Worst wins.
  - **register**: `policy = fm.get("policy", 14)`; for each item `age`: `> policy` → broken;
    `> 0.8*policy` → drifting. Worst wins. No items → holding.
  - **target**: `series = fm.get("series") or {}`; `act, pred = series.get("act"/"pred")`; if both
    non-empty: `expected = pred[min(len(act)-1, len(pred)-1)]`, `actual = act[-1]`, `tol =
    (fm.get("metric") or {}).get("tolerance", 8)`; `abs(actual-expected) > 2*tol` → broken;
    `> tol` → drifting. Missing data → holding.
  - **cycle**: `periods = fm.get("periods") or []`; latest = `periods[-1].get("s")`; `missed` →
    broken; `late` → drifting; else holding. No periods → holding.
  - Unknown/empty state_model → holding.

**Steps (TDD):** write `tests/test_cadence_reconcile.py` covering each model's three verdicts +
no-data→holding + a human-string `due` not crashing (assert holding/no-raise) + `current_period`
weekly format → run (fail) → implement minimal → run (pass) → **five gates** → commit.

Build program dicts inline in tests (the `{"frontmatter": {...}, "body": ""}` shape); use the real
`program_lib.load_registry()` for the registry.

---

### Task 2: `reconcile_program` — once-per-period guard + write-back + cycle log

**Files:** Modify `scripts/cadence/reconcile.py`; Test: `tests/test_cadence_reconcile.py`.

**Spec.** `reconcile_program(program, registry, now=None, force=False, root=None) -> dict`:
- `now = now or datetime.now(timezone.utc)`; derive a `date` for date math.
- `(verdict, facts) = compute_verdict(...)`.
- `cadence = fm.get("cadence") or <type's cadence> or "weekly"`; `period = current_period(cadence, now)`.
- `is_new_cycle = force or fm.get("last_cycle") != period`.
- If **not** `is_new_cycle`: return `{"program_id", "verdict", "new_cycle": False, "emitted": []}`
  — **no writes**.
- If `is_new_cycle`: set `fm["drift"]=verdict`, `fm["last_cycle"]=period`, `fm["last_run"]=_now_iso()`;
  **append** a `## Cycles` entry to the body (find/create the `## Cycles` section; append a new
  `### <period> - <verdict>` block + a one-line `checks: ... - next: ...` from `facts`; **ASCII
  hyphen**). Write via `program_lib._write_program_file(filepath, fm, body)` (`filepath =
  program["filepath"]` or resolve from `program_id` + `root`). Emitters come in Task 3 — leave
  `emitted=[]` for now. Return `{..., "new_cycle": True, "emitted": []}`.

**Steps (TDD):** tests — new cycle on a broken program writes `drift/last_cycle/last_run` and the
file re-reads with an appended `### <period> - broken` (re-read via `program_lib.read_program`,
pointing `root` at a tmp datasets dir); same-period (not forced) is a no-op (file unchanged);
`force=True` re-runs in the same period; cycle header is ASCII (no `—`). → fail → implement →
pass → **five gates** → commit.

Use a tmp `root` with a seeded program file (write one via `program_lib.create_program` +
`frontmatter_extra`, or copy a fixture) so tests never mutate `datasets/programs/`.

---

### Task 3: declarative `escalate` emitter + registry `emitters` + schema gate

**Files:** Modify `cadence/programtypes/registry.json`, `scripts/program_schema.py`,
`scripts/cadence/reconcile.py`; Tests: `tests/test_program_schema.py`, `tests/test_cadence_reconcile.py`.

**Spec.**
1. **Registry**: add `"emitters": [{ "on": "drift:broken", "action": "escalate" }]` to every type.
2. **Gate** (`program_schema.py`): add `CLOSED_ACTIONS = {"escalate","draft-message",
   "produce-artifact","propose-update","draft-ticket"}`. In `validate_doc`, for each type's
   `emitters` (if present): each must be a dict with a non-empty string `on` and an `action` ∈
   `CLOSED_ACTIONS`; else append a clear error. Keep the existing module docstring note honest:
   record that the read-mode→no-write-emitter-target cross-check is still deferred (no emitter
   targets yet).
3. **Reconciler**: a helper `_evaluate_emitters(program, type_entry, verdict, facts, root)` →
   `list[task_id]`. For each emitter where `on == f"drift:{verdict}"` (i.e. matches the verdict) and
   `action == "escalate"`: **dedupe** — if any open human task already carries the `program_id` tag,
   skip; else `import task_lib` (lazy) and `task_lib.create_task(title=f"{title} needs attention",
   queue="human", priority="high", creator="cadence", tags=[program_id, "cadence"],
   description=<facts-derived ≤2-sentence context + program_id>, root=...)`. Wire it into
   `reconcile_program`'s `is_new_cycle` branch; put returned ids in `emitted` and into the cycle-log
   line. Non-`escalate` actions are recognized but skipped (no-op).

   **Dedupe scan**: read open human tasks via `task_lib`'s listing (use the existing list/read API;
   inspect `task_lib.py` for the function — likely `list_tasks`/queue read) filtered to
   `status == "open"` and `program_id in tags`. Honor a tmp `root` so tests are isolated. If
   `task_lib.create_task` lacks a `root`/queue-dir override, note it and use the smallest faithful
   approach (a tmp working dir / monkeypatched datasets path) — do **not** write into the real
   queues from a test.

**Steps (TDD):** schema tests — valid emitter passes; bad `action` and missing `on` each rejected;
seed registry still `programtypes OK`. reconcile tests — a broken program emits exactly one human
card tagged with its `program_id`; a second `force` run emits **none** (dedupe); a holding program
emits none. → fail → implement → pass → **five gates** (incl. `program_schema.py` → `programtypes
OK`) → commit.

---

### Task 4: `reconcile_all` + CLI + error resilience

**Files:** Modify `scripts/cadence/reconcile.py`; Test: `tests/test_cadence_reconcile.py`.

**Spec.**
- `reconcile_all(root=None, now=None, force=False) -> list[dict]`: `registry =
  program_lib.load_registry()`; `programs = program_lib.list_programs(status="active", root=root)`;
  for each call `reconcile_program(...)` inside `try/except` (on exception: log to stderr, append
  `{"program_id", "error": str(e)}`, continue). Return the result list.
- `__main__`: `argparse` with `--all` (required for now), `--force`, `--now ISO` (parsed to a
  `datetime`; bad value → friendly error). Print a one-line summary per program
  (`PROG-0001 broken (new cycle, emitted TASK-0123)`), ASCII only.

**Steps (TDD):** test — `reconcile_all` over a tmp `root` with 2 good + 1 deliberately malformed
program returns 3 results, the malformed one carrying `error`, the good ones reconciled (no raise).
CLI smoke: invoke `main(["--all","--force"])` (factor a `main(argv)` so it's testable) over a tmp
root, assert exit 0. → fail → implement → pass → **five gates** → commit.

---

### Task 5: `CadenceScheduler` daemon + wire into `task_server.py`

**Files:** Create `scripts/cadence/scheduler.py`; Modify `scripts/task_server.py`; Test:
`tests/test_cadence_scheduler.py`.

**Spec.** Mirror `cron_scheduler.py`:
- `class CadenceScheduler` with `__init__(self, tick_interval=3600)`, `start()` (daemon thread,
  initial tick on startup), `stop()`, `_loop()`, `tick()`. `tick()` calls
  `reconcile.reconcile_all()` inside `try/except` (log + swallow — a reconcile error must never kill
  the thread). Stderr logging tagged `[cadence-scheduler]`. No LangFuse needed.
- `task_server.py`: `from cadence.scheduler import CadenceScheduler` (ensure `scripts/` is on
  `sys.path` so `import cadence.scheduler` resolves — it already inserts the scripts dir; the package
  `__init__.py` from Task 1 makes `cadence` importable). After the existing `scheduler =
  CronScheduler(); scheduler.start()`, add `cadence_scheduler = CadenceScheduler();
  cadence_scheduler.start()` and a matching `.stop()` in the shutdown path.

**Steps (TDD):** test — instantiate `CadenceScheduler`, monkeypatch `reconcile.reconcile_all` with a
counter, call `tick()` directly (don't sleep), assert it was invoked; a `tick()` where
`reconcile_all` raises does not propagate. → fail → implement → pass → **five gates** → commit.

Do **not** start a real daemon in tests. Verify the `task_server.py` wiring by import + the live e2e
(Task 7), not by booting the server in a unit test.

---

### Task 6: needs-you count in `build_cadence_payload` / `render_view`

**Files:** Modify `scripts/program_lib.py`; Test: `tests/test_program_lib.py`.

**Spec.**
- `render_view(program, registry, needs_you=0)` — add the optional arg; set `vm["needs_you"] =
  needs_you`. (Default keeps existing call sites + unit tests valid.)
- `build_cadence_payload(root=None)` — before rendering, build a count map: one pass over **open
  human-queue tasks** (lazy `import task_lib`; reuse its listing API), tally by any tag matching a
  `PROG-\d{4}` program id. Pass `needs_you=counts.get(program_id, 0)` into each `render_view`. Keep
  it resilient: if `task_lib` listing fails, default all counts to 0 (never break the payload).

**Steps (TDD):** test — with a tmp `root` holding one active program and one open human task tagged
with that `program_id`, `build_cadence_payload` returns that program with `needs_you == 1`; with no
tagged task, `needs_you == 0`. `render_view` default `needs_you == 0`. → fail → implement → pass →
**five gates** → commit.

---

### Task 7 (controller, not a subagent): live e2e on :8743 + finish

- Restart the dev board on :8743 (it caches `program_lib`/route/new modules).
- Pick an active program, set a pending checkpoint `due` to a past date (edit the seed file, or via
  a scratch program), run `python3 scripts/cadence/reconcile.py --all --force`.
- Verify on the Cadence tab: the row's drift flips to **broken**, a Now card appears with the program
  context, and `needs-you` shows 1. Run `--force` again → **no** duplicate card (dedupe). Confirm a
  `## Cycles` entry was appended with an ASCII hyphen.
- Final whole-implementation code review (superpowers:code-reviewer) → then
  superpowers:finishing-a-development-branch. Merge authority (per kickoff): **merge to local main,
  not pushed**, unless the operator says PR.
