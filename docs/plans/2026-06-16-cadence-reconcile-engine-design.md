# Cadence reconcile engine — design (Increment 2: slices 3 + 7)

> 2026-06-16. The second `/magnolia-build` increment of Cadence. Builds the **deterministic
> reconcile engine** on top of the slices-1+2 substrate (program files, `program_lib`, the
> registry + gate, the read-only tab). Brief: `2026-06-12-cadence-design-brief.md` (§2 state
> models, §5 the observe→reconcile→emit→log cycle, §9 slices 3 & 7). Substrate design:
> `2026-06-16-cadence-substrate-and-tab-design.md`.

## Goal

Per active program, on a cadence: read declared-vs-observed, compute a **drift verdict**
deterministically (all four state models), write the verdict back into the program file, append a
`## Cycles` log entry, and fire **one** emitter — `escalate` → an internal Now card. The Cadence
tab stops showing hand-authored `drift:` and starts showing **live-computed** verdicts.

**Envelope (unchanged from Increment 1):** deterministic (no agents/LLM), Tier-1 (zero external
writes — `escalate` is a local human-queue card), files-based, ASCII-safe, all five green gates.

## What this is NOT (scope fence)

Deferred to Increment 3 (slices 4/5/6) and beyond, and explicitly out of this build:

- **No automatic phase advancement, no `phase_rules` predicate grammar.** Advancing a program's
  `phase` requires *evidence that something moved*; the evidence source (movement-watch sentinels)
  is Increment 3. This increment audits drift against the **declared** phase/dates and never moves
  state without a fact. (Confirmed with the operator 2026-06-16; recorded in project memory.)
- No sentinels, no observation writing, no judged interpretation.
- No `draft-message` / `produce-artifact` / `propose-update` emitters (those write externally or
  climb the ladder — Increment 3). The cycle-program **digest producer is slice 4** — so a `cycle`
  program is judged only from data already in its file, never produced here.
- No new card type for `escalate` (reuse the human-queue task — brief §5).
- No ladder/judge wiring (escalate is a plain card, not a judged action).
- No intake, birth, archive, portfolio janitor, attachments, profile family rename.

## Architecture

### 1. `scripts/cadence/reconcile.py` — the deterministic reconciler

New package `scripts/cadence/` (`__init__.py` + `reconcile.py`). Pure functions over a program
dict (the `read_program` shape) + the registry (`program_lib.load_registry()`).

```
reconcile_program(program, registry, now=None, force=False) -> dict
    # compute verdict + computed fields (always, cheap, deterministic)
    # is_new_cycle = force or (last_cycle != current_period(cadence, now))
    # if not is_new_cycle: return {verdict, new_cycle: False} — NO writes
    # else: write drift+last_cycle+last_run, append `## Cycles` entry,
    #       evaluate emitters (escalate, deduped) -> return {verdict, emitted:[...], new_cycle: True}

reconcile_all(root=None, now=None, force=False) -> list[result]
    # list active programs, reconcile each, swallow per-program errors (log, continue)
```

`now` is injectable (`datetime`/`date`) for deterministic tests; defaults to today. `force`
bypasses the once-per-period guard (for e2e + tests).

**Determinism boundary — the verdict per model** (uniform output set `holding | drifting |
broken`; `blind` has no deterministic producer, stays out):

| Model | Deterministic inputs | `drifting` | `broken` |
|---|---|---|---|
| **pipeline** | pending checkpoints' `due` (ISO only); current phase's `max_age_days` vs `today - phase_entered[phase]` | checkpoint due within 7d; or phase past 80% of window | any pending checkpoint `due < today` (or `status: missed`); or `days_in_phase > max_age_days` |
| **register** | each item `age` vs `policy` (default 14) | any item age past 80% of policy | any item age > policy |
| **target** | `actual` vs expected = `series.pred[len(series.act)-1]`; tolerance = `metric.tolerance` (default 8) | abs(actual - expected) > tolerance | > 2× tolerance |
| **cycle** | latest entry in `periods[]` (`sent`/`late`/`missed`) | latest period `late` | latest period `missed` |

Worst signal wins. **No usable data → `holding`** (never a false alarm — e.g. a `cycle` with no
`periods`, a `target` with no series, a phase with no `max_age_days`).

**Defensive parsing (load-bearing):** checkpoint `due` is sometimes ISO (`2026-09-15`) and sometimes
a human string (`"Mon Jun 16"`). A non-parseable `due` is **skipped**, never raises. Phases without
`max_age_days` skip the phase-overage check. `phase_entered` tolerates the dict form (seeds) and the
scalar form (brief §4) — same as `render_view`.

**Write-back (minimal surface, via `program_lib._write_program_file` — parse-back validated):**
`drift`, `last_cycle` (`2026-W25`), `last_run` (ISO), and an **appended** `## Cycles` entry. No
checkpoint-status mutation. No observation rewrites (append-only, invariant #6). Runtime-written
text is **ASCII-safe** — the cycle header uses a hyphen, not an em-dash (invariant #8):

```
### 2026-W25 - broken
checks: pipeline date/phase - emitted: escalate (TASK-0123) - next: ship overdue 10d
```

### 2. The emitter — declarative `escalate` → internal Now card

Emitters are **declarative in the registry** (brief §3), not hardcoded. Add to each seed type:

```json
"emitters": [ { "on": "drift:broken", "action": "escalate" } ]
```

The reconciler reads the type's `emitters`, matches `on` against the verdict, and for `action:
escalate` calls `task_lib.create_task(queue="human", creator="cadence", ...)`:
- **title**: `"<program title> needs attention"`; **description**: program context + the ≤2-sentence
  "what I need from you" (which checkpoint/phase/item broke).
- **backlink**: the `program_id` in `tags` (`create_task` has no custom-field passthrough; `tags` is
  free — mirrors how cron tags `CRON-id`). `priority="high"`.
- **Idempotent / rate fence**: before creating, scan open human tasks; if one already carries this
  program's tag, **skip**. No duplicate card per tick. This dedupe *is* the self-escalation rate
  fence for this increment (per-person nudge caps are a later emitter-spec concern).

Only `escalate` is *acted on* this increment. Other closed-set actions, if ever declared, are
recognized by the gate but no-op'd by the reconciler (logged).

### 3. Clock — a sibling `CadenceScheduler` daemon (NOT folded into cron)

The existing cron path is *create-task → dispatch-an-LLM-agent*; a deterministic reconcile must not
ride it. New `scripts/cadence/scheduler.py` mirrors `cron_scheduler.py`'s daemon-thread pattern:
ticks **hourly**, calls `reconcile_all()` in-process. Started in `task_server.py` right after
`CronScheduler` (same lifecycle). reconcile is idempotent + once-per-period-guarded, so tick
granularity is forgiving: the first tick of a new ISO week runs every active weekly program's cycle;
later ticks that week are no-ops.

`current_period(cadence, now)`: `weekly` → `f"{iso_year}-W{iso_week:02d}"`; `daily` → ISO date;
default `weekly`.

### 4. `render_view` needs-you count

`build_cadence_payload` does **one** pass over open human-queue tasks, tallies by program-id tag,
and sets `needs_you` on each rendered program (avoids an N+1 per-program scan). `render_view` gains
an optional `needs_you=0` arg for unit-test simplicity. `task_lib` imported lazily (like `cron_lib`).

### 5. Schema gate extension (`program_schema.py`)

Chips the deferred "finish the schema gate" work for fields this increment introduces:
- **`emitters`** (if present): a list of `{on, action}`; `on` a non-empty string; `action` ∈ closed
  set `{escalate, draft-message, produce-artifact, propose-update, draft-ticket}` (brief §3).

Deferred still (no producer yet): the read-mode-source → no-write-emitter-target cross-check (waits
until emitters name target sources), sentinel tool-lists, intake block. Noted in `program_schema.py`.

## Error handling

- `reconcile_all` swallows per-program exceptions (log to stderr, continue) — one malformed program
  never stalls the portfolio or the scheduler thread.
- Non-ISO / missing dates, empty series, phases without windows → contribute nothing (→ holding),
  never raise.
- `_write_program_file` already reverts on a parse-back failure.

## Testing

- `tests/test_cadence_reconcile.py` — verdict per model (holding/drifting/broken + no-data→holding),
  defensive date parsing (human-string `due` doesn't crash), once-per-period guard, write-back
  (drift/last_cycle/last_run + appended `## Cycles`, ASCII hyphen), emitter creates a human card,
  emitter dedupe (no second card), `reconcile_all` continues past a bad program.
- `tests/test_program_schema.py` — emitter validation (valid block passes; bad action / missing
  `on` rejected). Seed registry still `programtypes OK`.
- `tests/test_program_lib.py` — `needs_you` populated from tagged open cards.
- e2e on :8743: set a checkpoint `due` in the past, run `reconcile.py --all --force`, watch the row
  flip to broken and a Now card appear; confirm a second run makes no duplicate card.

## Gates

All five stay green: `pytest` · `card_schema.py` · `test_engine_no_jay.py` · `portability_gate.py`
· `program_schema.py`. reconcile.py/scheduler.py are runtime code → ASCII-safe output, no hand-rolled
OS/shell (locking stays in `program_lib`/`platform_lib`).
