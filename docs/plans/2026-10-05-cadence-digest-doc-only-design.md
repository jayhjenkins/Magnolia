# Cadence digest workers: doc only, no drafted message

**Date:** 2026-10-05 · **Status:** approved, building

## Problem
The weekly-priorities program's `priority-digest` worker writes the digest doc, then
creates a second `send-message` collab card with a Teams draft (e.g. TASK-2355). The
drafted message is not useful; the operator wants to review the doc and write their own
message. `portfolio-rollup` has the same send-card step.

## Decision
Worker-prose change only (no engine, registry, or UI change):

- `scripts/workers/priority-digest.md` and `scripts/workers/portfolio-rollup.md`:
  - Remove the "create a send-message collab card" step. Rule: never create a
    send-message card, never send. The versioned doc is the deliverable.
  - Write as a document for the operator to read, not a channel message: drop the
    channel-voice step and the "bindings tell you where to send" line.
  - Drop the trust-ladder step (it only toggled a "draft for review" banner; with no
    send, every doc is for review).
- The cadence-created agent task (queue `agent`, `agent_output` = the doc) is the review
  card; the board already renders it as "Ready for review" with a "Review & edit" tile.

Out of scope: the `draft-message` nudge action (eos-l10-prep), `reconcile.py`, board UI,
existing artifacts.

## Plan
1. Tests (red): `tests/test_priority_digest_worker.py`, `tests/test_portfolio_rollup_worker.py`
   assert the body has no `send-message`, `--message-to`, `--message-channel`,
   `tier_of`; still requires `program_lib.py write-artifact`, slip/drift naming,
   `agent:complete`, ASCII-safe.
2. Edit both worker files (green).
3. Five gates: `pytest`, `card_schema.py`, `test_engine_no_jay.py`,
   `portability_gate.py`, `program_schema.py`.
4. E2E: rerun the W41 digest for PROG-7881 on the dev board; confirm a new versioned
   doc and no send-message card.
5. Merge to `main` when green.
