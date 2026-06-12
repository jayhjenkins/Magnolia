# Adapt harness vs /magnolia-build SKILL - keep/drop/add annotation

Side-by-side of the native SKILL (`.claude/skills/workflow-magnolia-build/SKILL.md`)
against the headless Adapt harness (`scripts/adapt_harness.py`, returned by
`build_harness_prompt()`).

The native SKILL is UNTOUCHED - it is correct for native sessions. The harness
is a sibling built by surgical subtraction: keep ALL the steering verbatim
(em-dashes and arrows converted to ASCII), drop only the environment / merge /
PR ornamentation that does not apply in a headless Adapt session, and add the
Adapt scope gate plus the ASCII rule.

Rule applied: when in doubt whether a passage is steering or ornamentation,
KEEP it.

## Section-by-section

| SKILL section | Disposition | Notes |
|---|---|---|
| Frontmatter (`name`, `description`) | DROP | Skill-discovery metadata; irrelevant to an injected system prompt. |
| Header + "trigger, not a manual" preamble | KEEP (adapted) | Kept the "trigger, not a manual - links to canonical docs, read the doc before acting" steering. Reframed the lead sentence for the headless Adapt context (no operator typing /magnolia-build). |
| "When to use" / "When NOT to use" | DROP | Routing/triggering metadata for a native skill picker; the harness is already inside an invoked build session. |
| Step 0 - Preflight (superpowers present, profile populated, git author, dev board, `gh auth status`) | DROP -> one line | Replaced with: "Assume the environment is ready; never narrate systems/environment checks." Removes the `gh auth` and `Preflight` ornamentation. |
| Step 1 - Ground in the reference layer | KEEP verbatim | All three pointers preserved (`invariants.md`, `conventions.md`, `architecture.md`) and the "honor by reference, do not re-derive" steering. Em-dashes -> hyphens. |
| Step 2 - Kickoff / merge-authority question / git-author setup | DROP -> one line | Replaced with: "Always auto-commit to main when green; never ask; never narrate git." Removes the "Merge to main when it's green, or open a PR" ornamentation and the per-user git-author setup (handled by the harness/runner context). |
| Step 3 - Take the ask | KEEP verbatim | "Accept a PRD/spec path... ask targeted clarifying questions before designing - do not guess at scope." Em-dashes -> hyphens. |
| Step 4 - Route (always scope first), incl. `meta-scope-extension` + build contract + single-surface vs multi-surface routing to the meta-create-* factories | KEEP verbatim | Full routing steering preserved: scope-first, build contract first either way, all four factory names, `meta-factory-core` read-first, scaffold -> capture -> gate -> commit -> Keep/Undo. Arrows/em-dashes -> ASCII. |
| Step 5 - Run the loop (steps 1-5: brainstorming, scope-extension, writing-plans, subagent-driven-development with two-stage review, live e2e) | KEEP verbatim | The whole loop preserved, including two-stage review (spec-compliance then code-quality), the contract brief per subagent, bind-to-the-seam, worktrees/parallel dispatch, the `git show`/`git diff` not `git checkout` instruction, ASCII-safe runtime output, and live e2e. Arrows/em-dashes -> ASCII. |
| Step 5.6 - `superpowers:finishing-a-development-branch` -> branch -> PR -> merge | DROP -> one line | The PR/branch ceremony is replaced by the factory commit + Keep/Undo. Folded into the "Finishing" line: "The factory commits and emits Keep/Undo; speak Keep/Undo, never commits/PRs." |
| Iron laws (all five) | KEEP verbatim | All five laws preserved: brainstorm-before-building, gates-green (with the four gate commands), bind-to-the-seam (platform_lib / card registry / profile_lib + ASCII-safe), engine-stays-de-personalized (invariants #1/#4), dev-board-only (#7), git-invisible/speak-Keep-Undo. Arrows/em-dashes -> ASCII. ONE deliberate, spec-mandated trim inside an iron law: the SKILL's gates law reads "Gates green ... and never commit to main - branch always"; the "never commit to main / branch always" clause is dropped here because Step 2's replacement is "auto-commit to main when green" - the two cannot coexist. That is the Adapt RUNTIME behavior (the end user's builds land on their main). The steering of the gates law - gates stay green before every commit - is fully preserved. |
| Success criteria | KEEP (trimmed) | Kept the substantive criteria: the build went through the full loop with subagents briefed from the contract, gates green, denylist-clean; reference layer read first. Dropped the two criteria that only describe a native session (Preflight passed; "operator typed /magnolia-build and never restated context") and the merge-authority "ship" criterion (superseded by the auto-commit + Keep/Undo lines). Added one Adapt criterion: the ask stayed inside the factory surfaces. |
| Related skills | DROP | Skill cross-reference metadata for a native picker; the harness already names every skill inline where it matters. |

## Added (Adapt deltas - not in the SKILL)

| Addition | Why |
|---|---|
| Scope gate ("You may only build adapters, workers, card-types, and skills through the meta-create-* factories... decline plainly and tell the user to run Claude Code natively in the Magnolia folder. You are path-confined; writes outside the factory surfaces will be refused.") | The headless Adapt session is path-confined to the four factory surfaces. This is the hard boundary that separates an Adapt build from a native build. Placed near the top so it is read first. |
| "Output discipline" block (ASCII-safe output everywhere) | The harness text and everything the session emits is runtime output; em-dashes/smart-quotes garble on Windows terminals (invariant #8). The SKILL mentions ASCII-safe inline; the harness also states it as a standing rule. |

## Verification of steering survival

Asserted present in the harness (see `tests/test_adapt_harness.py`):
- `meta-scope-extension`, `subagent-driven-development`, `two-stage`, `brainstorm`
- All four iron-law keywords: `Brainstorm before building`, `Gates green`,
  `Bind to the seam`, `de-personalized`
- Scope-gate phrase: `run Claude Code natively`

Asserted absent (ornamentation dropped):
- `merge to main when it's green, or open a PR`, `Preflight`, `gh auth`

Also asserted: byte-for-byte stable across calls, and pure ASCII.
