# Cadence Increment 3b — the weekly prioritization digest (design)

> 2026-06-18. Approved design for `/magnolia-build` Cadence inc3b = **slice 4** of the
> 11-slice epic (`2026-06-12-cadence-design-brief.md` §9), the back half of the inc3
> split. This is the **first external write** in all of Cadence. Builds on inc1
> (substrate), inc2 (deterministic reconcile engine), inc3a (sentinels + observation
> ledger + interpretation door). Tier-2 throughout the send; everything else Tier-1.

## Goal

Make the Monday priorities drumbeat go live: a **learned, judged prioritization brain**
drafts the weekly priorities digest from the operator's whole portfolio, surfaces it for
tuning, and (once it earns the rung) sends it through the existing message path. The
digest is an **interpretation** (which priorities, in what order, what slipped, what is
new), so it climbs the existing trust ladder rather than being a deterministic assembly.

## Why a worker, not a deterministic assembler

The operator's intent (brainstorm, 2026-06-18): the digest must *assess the state of the
roadmap, EOS data, and other programs, read the trailing few weeks of prior digests,
keep priorities correct and ordered, never let one slip without saying so explicitly,
and raise new priorities for reconciliation* — and it must be **tunable with the
judge/CoS so the system learns to set priority automatically over time.** That is an
interpretation that climbs a ladder, not a fact to assemble. A **worker-produced** card
goes through `agent:complete` -> judge-spawn -> reactions -> `graduation_assess`, which
*is* the tuning loop, and it rides the existing judge/ladder (no second shipper).
Shipping at `shadow` (propose-only) is the honest first rung; "automatic over time" is
emergent from the climb, not separate code.

## Architecture — the cycle (Monday tick on a `weekly-priorities` program)

1. **Reconciler emits an instruction, not a finished digest.** On a fresh cycle period,
   `_evaluate_emitters` matches a `produce-artifact` emitter and dispatches an **agent
   task** to the new `priority-digest` worker (`task_type: priority-digest`), carrying
   the program id. Dispatch reuses the existing agent-task queue + worker-match path.
2. **The `priority-digest` worker reads the portfolio**, never hardcoded sources:
   - this program's declared `items` + its `capture` observations since the last digest;
   - the **trailing N** previous digest artifacts (default N=3);
   - **other active programs'** cached `drift` verdict + observation ledgers (the
     roadmap-initiative seeds today; EOS/`did-it-work` programs fold in for free once
     those types exist in inc5 - the worker enumerates the portfolio, never a family
     literal).
   It produces a digest that (a) confirms/reorders priorities, (b) **flags every slip
   explicitly** (a dropped priority is named, never silently gone), (c) raises new
   candidate priorities for reconciliation.
3. **Two outputs:** a **versioned digest artifact**
   (`datasets/programs/artifacts/<program_id>/<period>-priorities-vN.md`, never
   overwritten - invariant #6) and the worker's **agent-output card on Now** carrying
   the proposed ordering / slips / new candidates. The operator tunes by editing
   (existing inline markdown editor); the judge scores the worker output.
4. **The send (Tier-2).** A `draft-message` emitter creates a **`send-message` collab
   card** whose body is the (tuned) digest, with `message_channel` / `message_to` read
   from `profile_lib`. The existing `handle_send_message` ->
   `_attempt_send_message` -> `adapters.publish("messaging")` path sends it; the
   **first-ever send raises `NeedsConfirmation`** = the one plain-language Tier-2
   confirm. **Degrades cleanly to draft-only** when mgc is unconfigured (current state):
   the card sits in the collab queue, no send attempted, no crash.
5. **Log.** The cycle entry records what was produced, the digest version, and what was
   emitted (ASCII, append-only).

## The learning loop (how it becomes "automatic over time")

- The `priority-digest` task_type defaults to **`shadow`** via `ladder_lib.tier_of`
  (unknown types -> shadow). It is **not** added to `enforce_lib.ACTION_TYPES` - that
  set is for external autoship; the digest production is internal (per the inc3a
  `cadence-propose-update` precedent). The only external action is the `send-message`
  card, which is already an ACTION_TYPE, already laddered, already Tier-2.
- At **shadow**: the worker proposes the digest; the operator reviews/edits/accepts
  before any send. At **supervised**: the judge gates it. At **autonomous**: the digest
  auto-finalizes and only the send Tier-2 remains. The worker/emitter reads
  `ladder_lib.tier_of("priority-digest")` to choose propose-vs-auto-apply.
- The training signal is the operator's reactions (accept / reorder / reject), already
  tracked; `graduation_assess` promotes the type over weeks. **No new ladder code** -
  this is the existing machinery, used.

## The rate-limit / Goodhart fence (brief §5)

- `max_nudges_per_person_per_week` is a field in the **emitter spec** (schema-validated,
  int), enforced by the reconciler: `_evaluate_emitters` counts this period's emissions
  per recipient and **suppresses + logs** when the cap is exceeded (the cycle log records
  the suppression and why). The weekly digest itself is already fenced once-per-period by
  the cycle guard; the cap governs per-recipient nudge emitters (e.g. owner nudges).
- A **response-rate counter-metric** is recorded on the program (sends vs.
  acknowledgements), so "nudge harder" is visible and fenceable rather than a hidden
  cheap path.

## Scope fence

**In this increment:** the `priority-digest` worker; `produce-artifact` + `draft-message`
emitters in `_evaluate_emitters`; the versioned artifact writer; the send wired to the
existing Tier-2 path; the nudge-cap fence + counter-metric; `program_schema` extension
(new actions, nudge-cap, `items`); the Cadence-tab digest/emission history.

**Degrades gracefully, not built here:** EOS data (no `eos-*` types until inc5); the
dedicated capture-watch intake sentinel (inc4 - 3b *consumes* existing `capture`
observations). The send degrades to draft-only when mgc is unconfigured.

**Deferred / emergent:** autonomous auto-set is the ladder *climb*, not this increment's
code. We ship the rung at shadow with the judge wired.

## Surfaces (build contract)

| Surface | Decision | Seam | Gate |
|---|---|---|---|
| worker | build-new `priority-digest`; reuse `message-writer` for send voicing | `scripts/workers/priority-digest.md` | validate-worker + pytest |
| reconciler emitter | extend `_evaluate_emitters` (produce-artifact + draft-message) | `scripts/cadence/reconcile.py` | pytest |
| messaging send | reuse, no code (`handle_send_message` / `adapters.publish` / `NeedsConfirmation`) | `scripts/adapters/messaging/` | pytest |
| card | reuse, no JS (agent-output for tuning; send-message for the send) | `registry.json` compose | `card_schema.py` |
| ladder | reuse, no code (`priority-digest` -> shadow; not in ACTION_TYPES) | `ladder_lib` (read) | pytest |
| program_schema | extend (actions, nudge-cap, items) | `scripts/program_schema.py` | `program_schema.py` |
| platform/UI | extend `cadence.js` (digest/emission history) | `ui/task-board/js/cadence.js` | `portability_gate.py` + pytest |

**Standing item:** ASCII-safe runtime output; identity via `profile_lib`.

## Constraints (inherited, non-negotiable)

Files-based, append-only (invariant #6); no identity literals (#1,
`test_engine_no_jay.py`); theme tokens only (#3); exactly one Tier-2 confirm before the
first external send (#5); the five green gates stay green (#2); declarative/closed-set
program types (#9); dev board `:8743` only (#7); branch off `main`, merge to local main
unless a PR is requested.

## E2E verification (live, :8743)

A Monday tick on a `weekly-priorities` program (with declared `items` + a seeded
`capture` observation + a roadmap-initiative in the portfolio) dispatches the
`priority-digest` worker; a versioned digest artifact is written; an agent-output card
lands on Now carrying the ordering + an explicit slip flag + the captured item; a
`send-message` collab card is created; walking it to send raises the Tier-2 confirm (or
degrades to draft-only with mgc unconfigured). Verify the nudge cap suppresses a second
per-recipient nudge in the same period and the cycle log records it. Clean up all e2e
artifacts and restore seeds afterward.
