# Conventions — the working rhythm

> How work gets done here: the timing and the process. The laws this rhythm must respect live in [`invariants.md`](./invariants.md) — this doc references them by number rather than restating them. For subsystems, see [`architecture.md`](./architecture.md).

## 1. The development loop

Feature work flows through the superpowers skills in order: `brainstorming` → `writing-plans` → `subagent-driven-development` (two-stage review: spec-compliance first, then code-quality) → live e2e verification (run the real board, observe the change) → `finishing-a-development-branch`. Each skill is canonical and auto-discovered; invoke it by name.

Git mechanics: branch off `main` (never commit to `main`). Set the git author locally to **your own identity** — `git config user.email "<your-github-noreply-email>"` and `git config user.name "<your-name>"`. Use your GitHub-provided no-reply address (GitHub → Settings → Emails → "Keep my email private") so pushes aren't rejected by email-privacy. End every commit with the trailer `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`. Open PRs with `gh pr create --base main`.

## 2. The green gates — when to run them

Run the five gates **before every commit that touches code**. Three are invariant #2 in [`invariants.md`](./invariants.md) (`python3 -m pytest` · `python3 scripts/card_schema.py` · `python3 -m pytest tests/test_engine_no_jay.py`); the fourth is `python3 scripts/portability_gate.py` (→ `portability OK`), enforcing invariant #8 — the OS/shell seam stays unbroken; the fifth is `python3 scripts/program_schema.py` (→ `programtypes OK`), enforcing invariant #9 — the Cadence program-type registry stays well-formed. Don't restate them; run them. Doc-only changes under `docs/` are not scanned by the denylist or portability gates, but they still must not break the other gates.

## 3. Capture-to-profile, not the artifact

Capture team or person nuance to `profile/` as you go — via `profile_lib.set_integration_conventions(...)` — never bake it into the generated artifact, which must stay denylist-clean. This is invariant #4 in [`invariants.md`](./invariants.md), realized as the capture step of the factory — see `meta-factory-core`.

## 4. Capability tiers

Tier-1 work — workers and card-types — performs no external writes. Tier-2 work — adapters, or anything that writes to the outside world — gets **exactly one plain-language confirm before its first external action**, per invariant #5 in [`invariants.md`](./invariants.md). Decide the tier before you build, so the confirm is armed at the right moment.

## 5. The factory spine

When extending the system — a new worker, card-type, or adapter — use the matching `meta-create-*` skill rather than hand-rolling. It runs scaffold → capture → gate-green → commit → Keep/Undo receipt. Git stays invisible to the user: changes are presented as **Keep / Undo**, never as commits or reverts. See `meta-factory-core` and [`architecture.md`](./architecture.md) §6.

## 6. Output conventions

Never delete generated artifacts — append a version suffix (`v1`, `v2`); that's invariant #6 in [`invariants.md`](./invariants.md). Default to markdown with clear headings, and use a `*-draft.md` suffix when you're unsure the artifact is final. Maintain `status.json` for processing state and `progress.md` for human notes.

## 7. Dev vs prod safety

The board runs on `localhost:8744`. The separate `~/pm-os` production install and its port `8742` are retired and no longer in use — this repo is the only install now. Invariant #7 in [`invariants.md`](./invariants.md) records this fact; its number is kept (not reused) for citation stability.
