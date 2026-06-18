# Cadence Increment 4b — death + the janitor (design)

**Date:** 2026-06-18
**Status:** approved (Jay), ready for plan
**Builds on:** inc4a (the birth path, merge `cde2a7f`). Closes slice 8 of the Cadence epic.
**Brief:** `docs/plans/2026-06-12-cadence-design-brief.md` §6.4 (completion/retirement), §6.5 (the portfolio janitor), §10 (open questions).

## Goal

Close the program lifecycle `candidate -> active -> paused -> archived` — give programs a *where they go*
— and make the portfolio maintain itself. Everything is **Tier-1**: local file moves and local cards,
no external write, no new shipper, no second ladder. Reuses the inc3a propose-update door, the
inc4a birth-branch wiring, the closed-set `apply_mutation`, and the register state model.

## The cut

Jay's call: **build all of 4b in one increment** (not split). This is deliberate — the interpretation
(silent) archive door is unsafe without the blind-sentinel telemetry that tells a *dormant program*
from a *blind sentinel* (brief §6.4 hands that distinction to the janitor, §6.5). Shipping them
together inside one increment satisfies the coupling. Build *order* still lands the death FACT path
green first, then stacks the telemetry, the interpretation door, the janitor, and grounding on top.

### Forks decided in brainstorm
- **Silent threshold (interpretation door):** per-type optional `archive_after_silent_cycles`, default
  **6** cadence periods. Measured deterministically as days-since-last-activity vs `cadence_period_days * N`.
  Only fires when telemetry shows the sentinel **ran** (ran-but-silent = dormant); a blind sentinel
  escalates instead of proposing archive.
- **Telemetry store:** a single rollup JSON, `datasets/cadence/sentinel-runs.json`, keyed by sentinel
  name -> `{last_run, last_success, last_emitted_count, last_error}`. Atomic single-file write by
  `sentinel_runner`; one read for the janitor.
- **Rollover/renewal cadence:** deferred to inc5 (archive *mechanics* ship here; the *cadence of renewal*
  does not).

## The six pieces (build order)

### A. The `archive` op (`program_lib`)
- Add `"archive"` to `_MUTATION_OPS`.
- `_apply_archive(program_id, mutation, fm, type_entry, filepath, body, root)`:
  - set `status: archived` in frontmatter;
  - append an archive `completion` observation (sentinel=reconciler; source = the proposal's evidence
    citation when carried, else `proposal`; claim `Program archived: <reason>.`);
  - **move** the file to `datasets/programs/archive/PROG-NNNN.md`, version-suffixed if a file already
    exists there (`PROG-NNNN-v2.md`, ...), via a new portable `platform_lib.move_file` seam (never a raw
    shell `mv`; never overwrite/delete — invariant #6);
  - **idempotent:** already archived (status archived OR file already present under `archive/`) -> no-op
    success (a retried accept must not move/append twice).
  - returns `{"applied": "archive", "program_id", "to": <archive relpath>}`.
- `list_programs` already excludes `archived` from reconcile, so drift stops computing once it lands.
  `read_program` must still resolve an archived file by id (for idempotency / the accept path) — extend
  its lookup to also search `archive/`.

### B. The FACT archive door (`reconcile`)
- `_propose_archive(fm, type_entry, body)` -> `{op:"archive", reason, citations}` when an unambiguous
  fact holds: terminal phase reached (`_terminal_phase`), a tracker-truth observation reports the epic
  closed, or a `did-it-work` checkpoint is verified.
- Wire into `_evaluate_emitters` as a new propose-update branch on `on == "completion-verified"`,
  mirroring the inc4a `candidate-ripe` birth branch. Emits the **existing** `recommendation` /
  `cadence-propose-update` card carrying `proposal={op:archive,...}`, tags `[program_id, "cadence"]`.

### C. The INTERPRETATION (silent) door (`reconcile`)
- `_propose_archive_silent(fm, type_entry, body, telemetry, now)` -> archive mutation when
  days-since-last-activity (latest observation **or** emission/cycle date) >= `cadence_period_days * N`
  (`N = type.archive_after_silent_cycles`, default 6).
- **Safety gate:** fires only when telemetry (§D) shows the program's sentinel ran recently
  (ran-but-silent). Blind sentinel -> no proposal (the janitor escalates instead).
- Both doors produce `op:archive`; **deduped by op** via `_open_propose_update_ops` -> at most one open
  archive proposal per program (fact and silent never double-propose).

### D. Blind-sentinel telemetry (`sentinel_runner`)
- After each run, stamp `datasets/cadence/sentinel-runs.json` (atomic temp-write + replace) keyed by
  sentinel name -> `{last_run, last_success, last_emitted_count, last_error}`.
- Helpers `record_sentinel_run(name, *, success, emitted_count, error=None, root, now)` and
  `read_sentinel_runs(root)`. Consumed by §C and §E.
- "blind" = `last_run` missing / errored / staler than a threshold; "ran-but-silent" = ran recently,
  emitted nothing for that program.

### E. The portfolio-health janitor (seeded program + scan)
- New `portfolio-health` registry type, `register` model, family `system` (like `program-intake`).
- Seeded active program **PROG-0015** (counter bumped to 16). Standard Cadence row, cycle log, kill
  switch — self-hosting (the maintainer is governed by the machinery it maintains).
- A janitor-specific `_scan_portfolio_health(root)` refreshes the register's **items** each cycle with
  current findings: **stale actives** (silent + sentinel-live), **blind sentinels** (from §D),
  **aging candidates** (from §F), **duplicate/overlapping programs** (title/anchor similarity, flag-only),
  and a simple **supply check** (active programs per family vs a floor). `## Cycles` keeps history.
- Broken findings (a blind sentinel) `escalate` to a human card via the existing emitter. The janitor
  **reports**; the per-program doors **propose** — it never archives directly.

### F. Candidate aging (4a M-3) + grounding
- `upsert_candidate` stamps an `opened` date; the intake reconcile computes each OPEN candidate's `age`
  from `opened` so the generic `_verdict_register` ages them (and the janitor flags aging candidates).
- A **grounding** render section in `render_view`: citation count, last-observation date, per-program
  sentinel liveness, and binding-health warnings (e.g. a `target` program whose metric instrument no
  longer resolves). Render-only, no external calls.

## Schema / gates (invariant #9)
`program_schema` gains: optional non-negative-int `archive_after_silent_cycles`; the `portfolio-health`
type validates; emitter triggers `completion-verified` / `silent-too-long` accepted. `archive` is a
mutation op (not an emitter action) -> `CLOSED_ACTIONS` unchanged. All five gates stay green.

## Build contract (surfaces)
| Surface | Decision | Seam | Gate |
|---|---|---|---|
| core engine | extend | `program_lib` (archive op, candidate age, grounding render) + `reconcile` (doors, janitor scan) | `pytest` + `program_schema.py` |
| card | **reuse** | `recommendation` / `cadence-propose-update`; accept -> `apply_mutation(op:archive)`; reject cancels | `card_schema.py` |
| telemetry | build-new (data file) | `sentinel_runner` + `datasets/cadence/sentinel-runs.json` | `pytest` |
| platform/UI | extend | `platform_lib.move_file`; `cadence.js` janitor row + grounding + archived handling | `portability_gate.py` + `pytest` |

Standing contract item, every surface: **ASCII-safe runtime output** (hyphen not em-dash, ASCII quotes).

## e2e on :8743
1. Force a program to a terminal phase -> **fact** door proposes archive -> accept via real API -> file
   moves to `datasets/programs/archive/` version-suffixed, status archived, drift stops, HEAD unchanged.
2. Make a program silent past N with its sentinel **live** -> **interpretation** door proposes; then make
   the sentinel **blind** -> no archive proposal, the janitor escalates the blind sentinel (dead-vs-blind
   proven).
3. The janitor row renders findings; an aging candidate shows a real age.
4. Restore seeds + counter after the live run (as in 4a).

## Deferred to inc5
Rollover/renewal cadence; slices 9 (attachments), 10 (EOS starter set), 11 (`meta-create-program-type`
factory + portfolio rollup card).
