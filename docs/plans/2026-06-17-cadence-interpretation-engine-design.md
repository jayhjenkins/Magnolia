# Cadence Increment 3a — the interpretation engine (slices 5 + 6)

> 2026-06-17. The third `/magnolia-build` increment of Cadence (the "second organ"), cut in
> half: **3a (this doc) = slices 5 + 6** — sentinels, the observation ledger, checkpoint-driven
> phase advancement, and the proposal door + ladder. **3b (next) = slice 4** — the weekly digest
> external send. Builds on inc1 (substrate + tab) and inc2 (the deterministic reconcile engine).
> Companion: `2026-06-12-cadence-design-brief.md` (§2.1 two doors, §4 observation enum, §5 cycle
> pipeline, §6 lifecycle, §9 slices).

## Goal

Give Cadence its **first agents** and its **interpretation door** — without crossing the
external-write line (that is 3b). After 3a a program's state changes through the two doors the
brief mandates:

- **Fact door:** an adapter-grounded or deterministic-instrument checkpoint flips to `met` → the
  reconciler advances the program's phase *mechanically*, stamping a source-cited observation.
- **Interpretation door:** a judged signal (a `movement-watch` observation, a phase stall) → the
  reconciler emits a `propose-update` recommendation card → a human **accept** applies the program
  mutation. This action type climbs the existing trust ladder, starting at **shadow**.

Everything in 3a is **Tier-1 internal**: read-only sentinels in, local cards + program-file
mutations out. No message leaves the building.

## Scope fence (what 3a is NOT)

- **No external write.** No `draft-message`, no `produce-artifact`, no send path, no Tier-2 send.
  That is slice 4 / inc3b.
- **No lifecycle.** No intake sentinel, no `program-intake` register, no birth/archive proposals,
  no `portfolio-health` janitor. That is inc4. The proposal-applier's closed mutation set is
  deliberately narrow: **`advance-phase` + `adjust-checkpoint` only** (no `mark-verified`, no
  `archive`).
- **No new ladder/shipper.** `propose-update` rides the existing `ladder_lib` + `enforce_lib` +
  `judge.py`. There is exactly one shipper and one ladder in the system.
- **No `register`/`target` model expansion** beyond what inc2 already computes (slice 7 of the
  brief).

## Architecture

### The two-clock separation (the load-bearing structural decision)

Cadence now has two independent rhythms, mapping onto the two doors. They share **no output path**.

| Clock | Mechanism | Cadence | Writes | Reads |
|---|---|---|---|---|
| **Sentinels** | cron → `claude -p` dispatch (agentic, async), via `sentinel_runner.py` | per sentinel-tier cron job | **observations** appended to programs (deterministic harness writes; the LLM only returns structured records) | sources (transcripts via qmd; PM adapter) |
| **Reconciler** | in-process `CadenceScheduler` (shipped inc2), deterministic | hourly tick, period-guarded | drift/phase/cycle-log + `propose-update`/`escalate` cards | program files incl. observations |

The judgment happens *at the sentinel* (confidence-scored observations). The reconciler stays
deterministic over judged inputs — it never re-invokes `judge.py` per program. `judge.py` re-enters
only via the **ladder** when a `propose-update` climbs toward auto-apply at higher tiers.

### Sentinels (`scripts/sentinels/*.md` + `sentinel_runner.py`)

A new primitive, sibling to workers — **reuses the dispatch substrate, distinct contract**. A
sentinel definition is a markdown file with YAML frontmatter declaring:

```yaml
---
name: movement-watch
kind: sentinel
sources: [{ kind: transcripts, mode: read }]   # read-only forever; gate-enforced
observation_kinds: [status-signal, completion, date-change, commitment, risk, blocker]
scope: active-programs        # handed the active programs' ids + ## Intent
---
<the prompt body: how to read sources and attribute signals to programs>
```

- **`movement-watch`** (interpretive): handed the active programs' ids + `## Intent` paragraphs +
  the in-scan transcripts; returns observations each attributed to one `program_id` (or dropped if
  unattributable — never force-fit). Emits the judged kinds (`status-signal`, `completion`, …) with
  a `confidence`.
- **`tracker-truth`** (mechanical): matches a tracker epic to a program via the program's
  `links.tracker_epic` field through the PM adapter's new **read** op; emits only adapter-grounded
  `completion`/`status-signal`/`date-change`. **Degrades to no-op when the adapter is unconfigured**
  (the current state on this box) — shipped correct, dormant until Asana/Jira is wired.

`sentinel_runner.py` mirrors `adapt_runner.py`/`chat_runner.py`: cron fires it with a sentinel name;
it loads the def, runs `claude -p` over the in-window sources, parses the returned observation
records, and hands them to the `program_lib` observation writer. The LLM **never writes files**.

**Observation write contract (extends `program_lib`):**
- Appended under `## Observations`, append-only (invariant #6), never rewritten.
- `kind` ∈ closed enum `{status-signal, date-change, completion, commitment, risk, metric, capture,
  blocker}` (brief §4) — gate-enforced.
- Every observation **cites a source** (file + location). An uncited observation is rejected.
- **Dedup:** primary = scan-window (sentinel only considers sources dated after its `last_run`);
  safety net = skip an observation whose `(kind, source, claim-hash)` already exists on the program.
  Mirrors the inc2 escalate-card dedupe.

### Checkpoint-driven phase advancement (the deferred-from-inc2 work, grounded)

No new predicate DSL. Each pipeline phase optionally names an **`exit_checkpoint`** — an id already
in the program's `checkpoints` (already instrument-bound per the no-uninstrumented-checkpoint
invariant). The reconciler, on its deterministic tick:

- **Fact door:** if a phase's `exit_checkpoint` has `status: met` AND its instrument is
  adapter/deterministic (not `human-attested`) → advance `phase` to the next phase, set
  `phase_entered`, append a `completion`-grounded fact observation + cycle note. Mechanical, no human.
- **Interpretation door:** a `human-attested` exit checkpoint cannot auto-flip. If `movement-watch`
  has stamped a `completion`/`status-signal` observation citing that checkpoint above a confidence
  threshold → emit a `propose-update` ("`<phase>` looks complete — advance to `<next>`?"). Phase
  stalls (overage, already computed in inc2) likewise emit `propose-update` (phase-stall), not just
  `escalate`.

Checkpoints flip to `met` only through those doors — never invented by the reconciler.

### The proposal door (slice 6)

- **Emitter:** `propose-update` (now acted on, alongside the inc2 `escalate`). Produces an existing
  **`recommendation` card** (no new card type) whose body is the proposed program **diff** (the
  mutation spec + the citations that earned it), tagged `[program_id, "cadence"]`, deduped like
  escalate (skip if an open proposal already covers this program+mutation).
- **Accept applies the mutation** via a new `program_lib` applier over a **closed mutation set**:
  - `advance-phase` → set `phase` + `phase_entered`, append fact observation + cycle note.
  - `adjust-checkpoint` → change a checkpoint `due` date, or set `status: met` (which may cascade to
    `advance-phase`).
  Reject → logged, no mutation, append-only record of the decline.
- **Ladder:** routed through `enforce_lib` under `task_type = "cadence-propose-update"`. Unknown
  types default to **shadow** in `ladder_lib` → propose-only out of the box. As approvals accrue it
  climbs shadow → supervised → autonomous (auto-apply + receipt) on the *existing* machinery.
  Artifact-vs-action rules unchanged. No second ladder.

### Schema gate (`program_schema.py`) — finishing the deferred §3 items

Extend the gate to validate everything 3a introduces:
- `phases[].exit_checkpoint`, when present, references a real checkpoint id; only on `pipeline`.
- Sentinel definitions: `kind: sentinel`, all `sources` are `mode: read`, `observation_kinds` ⊆ the
  closed enum, and the sentinel declares no write-capable tool.
- The `mode:read` → no-write-emitter-targeting-that-source cross-check (now meaningful: emitters
  exist that could target sources).
- The now-live emitter actions (`propose-update`) remain in `CLOSED_ACTIONS` (already present).
- Denylist scan continues to extend to `cadence/**` and the new `scripts/sentinels/**`.

## Error handling

- Sentinel dispatch failure (claude -p errors, malformed JSON) → logged, observations from that run
  dropped, program untouched. A bad sentinel run never corrupts a program.
- Unconfigured PM adapter → `tracker-truth` no-ops cleanly (logged once), no error surfaced.
- Unattributable / uncited observation → dropped at the writer, logged. Never force-attributed.
- Proposal-applier given a mutation outside the closed set → refuses, logs, leaves the program
  unchanged (defensive; the emitter only ever produces in-set mutations).
- Phase advancement past the terminal phase → no-op (terminal phases never advance).
- All reconciler additions stay inside the existing per-program try/except in `reconcile_all` — one
  bad program never stalls the run.

## Testing

- **`compute_verdict` / phase advancement:** fact-door advance on a met adapter checkpoint;
  no-advance on a `human-attested` checkpoint (→ proposal instead); terminal-phase no-op;
  advancement stamps observation + cycle note.
- **Observation writer:** append-only; kind-enum rejection; uncited rejection; scan-window +
  content-hash dedupe (re-run emits nothing new).
- **Sentinels:** `movement-watch` attributes/drops correctly given fixture transcripts + program
  intents; `tracker-truth` no-ops when adapter unconfigured, emits grounded obs when stubbed
  configured. `sentinel_runner` parses well-formed output, drops malformed.
- **Proposal door:** `propose-update` emits a recommendation card (deduped); accept applies
  `advance-phase` / `adjust-checkpoint`; reject leaves the program unchanged; mutation outside the
  closed set refused; shadow tier = propose-only (no auto-apply).
- **Schema gate:** rejects a bad `exit_checkpoint` ref, a `mode:write` sentinel source, an
  out-of-enum observation kind; accepts the extended valid registry.
- **Regression:** all inc1/inc2 cadence tests + the full suite stay green.

## Gates (invariant #2 — all green before every commit)

`python3 -m pytest` · `python3 scripts/card_schema.py` → `registry.json OK` ·
`python3 -m pytest tests/test_engine_no_jay.py` · `python3 scripts/portability_gate.py` →
`portability OK` · `python3 scripts/program_schema.py` → `programtypes OK`.

## Build sequence (refined in the implementation plan)

1. Observation write contract on `program_lib` (+ kind enum, citation, dedupe) — the foundation.
2. `scripts/sentinels/*.md` schema + gate validation + the two sentinel defs.
3. `sentinel_runner.py` + cron job entries (dispatch path).
4. PM adapter read op (`fetch_status`) + `tracker-truth` consumption + graceful degrade.
5. Checkpoint-driven phase advancement in `reconcile.py` (fact door).
6. `propose-update` emitter + recommendation card + interpretation-door advancement.
7. Proposal-applier in `program_lib` (closed mutation set) + accept wiring + `enforce_lib` routing
   at shadow.
8. UI: observation ledger + emission history in the Cadence row expansion (brief §7).

## Merge

Branch `feat/cadence-interpretation-engine` off `main`; merge to **local main** on green (not
pushed). 3b (slice 4) follows on its own branch.
