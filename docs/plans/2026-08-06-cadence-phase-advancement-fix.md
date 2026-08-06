# Cadence Phase Advancement Gaps — Fix Complete

**Date**: 2026-08-06  
**Commit**: `cd702d6` - fix(cadence): wire phase advancement for rocks + multi-attribution sentinels  
**Status**: ✅ COMPLETE (all 1294 tests passing)

## Problem Statement

Three compounding gaps prevented Cadence from acting as a real project manager:

1. **eos-rock phase advancement blocked**: `eos-rock` program type had no `exit_checkpoints` or `phase-advance-proposable` emitter, making it impossible for rocks to advance through phases based on sentinel evidence.

2. **roadmap-initiative planning → execution blocked**: The planning phase had no `exit_checkpoint`, preventing the interpretation door from proposing advancement to execution.

3. **movement-watch attribution limited**: The movement-watch sentinel attributed each signal to exactly ONE program, missing cross-program impact (e.g., "we're delaying rock X to work on initiative Y").

## Solution

### 1. Registry Updates (`cadence/programtypes/registry.json`)

**eos-rock phases** — Added exit_checkpoint definitions:
- `define` phase: added `"exit_checkpoint": "define-exit"`
- `build` phase: added `"exit_checkpoint": "build-exit"`
- `beta` phase: added `"exit_checkpoint": "beta-exit"`

**eos-rock emitters** — Added phase advancement triggers:
- `phase-advance-proposable` → `propose-update` (new)
- `silent-too-long` → `propose-update` (new)

**roadmap-initiative planning phase** — Added checkpoint:
- `"exit_checkpoint": "planning-exit"` to planning phase

### 2. Reconciler Logic (`scripts/cadence/reconcile.py`)

Relaxed `_propose_phase_advance()` to support TWO paths:

**Path A (original, mechanical advancement)**:
- Phase has an `exit_checkpoint`
- Program has a matching checkpoint object
- Checkpoint instrument is mechanical (adapter-verified)
- Mechanical advancement is still blocked (fact door)

**Path B (NEW, evidence-only advancement)**:
- Phase has no `exit_checkpoint` OR program has no matching checkpoint object
- Interpretive completion evidence is sufficient
- Proposes advancement (requires human approval, Tier-2)
- Supports program types where checkpoints are program-specific milestones (eos-rock)

Unchanged: The **fact door** (mechanical advancement) still requires both checkpoint and mechanical instrument.

### 3. Movement-Watch Sentinel (`scripts/sentinels/movement-watch.md`)

**Multi-program attribution**:
- A signal may now impact multiple programs
- Return one record per affected program when transcript explicitly names cross-program impact
- Clear rule: "do NOT duplicate signals speculatively — only multi-attribute when the transcript itself states the cross-program impact"

**Observation kind guidance** (new section):
- `status-signal` — work happening, progress updates (phase advancement independent)
- `completion` — phase/milestone/checkpoint DONE (drives phase advancement)
- `date-change` — target date moved/set/removed
- `commitment` — committed action or deliverable
- `risk` — timeline/quality/scope/resource concern
- `blocker` — actively preventing progress

### 4. Sentinel Runner (`scripts/sentinel_runner.py`)

Updated prompt instruction:
- **Old**: "attribute each signal to ONE of these ids, or drop it"
- **New**: "attribute each signal to one or more of these ids; drop unattributable signals"

### 5. Test Coverage (`tests/test_cadence_reconcile.py`)

Three new test cases:
1. `test_propose_update_works_without_exit_checkpoint` — eos-rock phase advancement with interpretive evidence
2. `test_propose_update_no_evidence_no_card_even_without_checkpoint` — no proposal without completion evidence
3. `test_propose_update_planning_exit_without_checkpoint_object` — roadmap-initiative planning → execution

Three regression tests for real registry configuration:
1. `test_registry_pipeline_types_have_phase_advance_emitter` — validates all pipeline types have phase-advance-proposable
2. `test_registry_pipeline_types_have_at_least_one_exit_checkpoint` — validates phase checkpoints exist
3. `test_registry_eos_rock_has_phase_advance_proposable` — explicit eos-rock validation
4. `test_registry_roadmap_initiative_planning_has_exit_checkpoint` — explicit planning-exit validation

## Verification

- **All 1294 tests passing**: Full test suite validates no regressions
- **31 tests in test_cadence_reconcile.py passing**: Specific phase advancement and checkpoint tests pass
- **Registry validation passing**: Real registry config validated to prevent silent failures

## Impact

✅ **eos-rock programs** can now advance phases (define → build → beta → ga) based on movement-watch completion signals  
✅ **roadmap-initiative planning** can now advance to execution when planning is complete  
✅ **movement-watch** can now capture cross-program impact (e.g., "delaying rock X to unblock initiative Y")  
✅ **Cadence act as a real project manager** — complete Tier-1 pipeline now wired end-to-end

## Files Changed

```
cadence/programtypes/registry.json    | 10 +--
scripts/cadence/reconcile.py          | 53 ++++++-------
scripts/sentinel_runner.py            |  2 +-
scripts/sentinels/movement-watch.md   | 32 ++++++--
tests/test_cadence_reconcile.py       |152 ++++++++++++++++++++++++++++++++++
────────────────────────────────────────────────────────────────────────────
5 files changed, 208 insertions(+), 41 deletions(-)
```

---

**Summary**: Cadence can now propose phase advancements for rocks and multi-initiative programs based on evidence from movement-watch sentinel signals, completing the end-to-end project management pipeline.
