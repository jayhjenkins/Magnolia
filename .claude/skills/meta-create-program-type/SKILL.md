---
name: meta-create-program-type
description: Use when the operator asks to add, build, or scaffold a new Cadence program type (a new standing-loop shape) - composes one entry in the declarative program-type registry, validates the program-schema gate, and emits a Keep/Undo receipt
---

# Create Program Type

Add a new Cadence program type by composing one entry in the declarative
program-type registry (`cadence/programtypes/registry.json`). **Read
`meta-factory-core` first** - this skill is its program-type specialization, the
fourth `meta-create-*` sibling. Reuses `meta-create-skill`'s RED-GREEN-REFACTOR spine.

The Cadence engine runs any registered program type generically (the reconciler
computes drift by `state_model`, the tab renders rows from the registry + program
frontmatter, emitters fire from the declared playbook). So a program type composed
from the **closed sets** needs **zero new engine code** - just a registry entry the
program-schema gate accepts, and the operator can start creating programs of it.

## When to Use

- The operator wants a new standing-loop shape that fits one of the four state
  models (e.g. "track our hiring pipeline", "a quarterly OKR scoreboard", "a
  vendor-renewal register").
- **Not** for a new worker (`meta-create-worker`), card type
  (`meta-create-card-type`), or external integration (`meta-create-adapter`).

## Composition-only (the hard boundary - the closed sets)

You may ONLY compose from the engine's closed sets (the program-schema gate
rejects anything else):

- **state_model**: exactly one of `pipeline`, `cycle`, `target`, `register`.
  `phases` are allowed ONLY on `pipeline`.
- **emitter `on` triggers**: `drift:broken`, `candidate-ripe`,
  `phase-advance-proposable`, `cycle-fresh`, `completion-verified`,
  `silent-too-long`.
- **emitter `action`s**: `escalate`, `draft-message`, `produce-artifact`,
  `propose-update`, `draft-ticket` (+ the bootstrap actions for an `intake` block).
- **sentinels**: reference only sentinels that already exist in
  `scripts/sentinels/` (e.g. `movement-watch`, `tracker-truth`, `sheet-watch`,
  `program-intake`). A genuinely new sentinel is a separate build, not this factory.
- **sources**: every source declares a `mode` (`read`/`write`); a manual-on-purpose
  source stays `mode: read` forever.
- **presentation**: `chip_tokens` reference theme tokens ONLY - never a raw color,
  radius, or transition.

A NEW state model, emitter action, or sentinel is engine work (a gate change / a
new sentinel def) and is **out of scope** - say so plainly and hand it to
engineering; do not invent a registry entry the gate will reject.

## Capture to profile, never the entry (invariant #1 / #4)

The registry entry is identity-free. Anything person/team/source-specific - the
operator's preferred family display label, a source LOCATOR (a sheet resource, a
channel, a distro), a tracker board - is captured into the **profile** via
`profile_lib` (e.g. `set_integration_conventions`, or the relevant integration
block), and the engine reads it at runtime. Never write a name, company, URL, or
channel into `registry.json`.

## The Gate (must be green before commit)

1. `python3 scripts/factory_lib.py validate-program-type <id>` -> `ok`.
2. `python3 scripts/program_schema.py` -> `programtypes OK` (the full closed-set +
   token-only gate over the whole registry).

## Workflow

1. **Capture the spec.** Ask: the type `<id>` + `label`, its `family` (a display
   shelf - reuse an existing one where it fits), its `state_model` (one of the
   four), `phases` if pipeline, `cadence`, `sources` (+ each `mode`), which existing
   `sentinels` it reads, and its `emitters` (from the closed sets). Capture any
   source locator / family-label nuance to the profile, not the entry.
2. **Compose** the entry. Append one object to the `types` array in
   `cadence/programtypes/registry.json`. Keep the JSON valid and existing entries
   untouched. Use theme tokens for any presentation chip.
3. **Gate** - run both checks above. Fix until green.
4. **Commit + receipt** - `python3 scripts/factory_lib.py commit-and-receipt --summary "a <id> program type" --kind program-type cadence/programtypes/registry.json`
5. **Hand back** - tell the operator: *"Built you a `<id>` program type -> you can
   start creating programs of it; there's a receipt card. Keep / Undo."* Never
   mention git. Note that the type stays inert until the operator has >=1 active
   program of it (activation is implicit / instance-driven - design brief section 8).

## Iron Laws

1. **Compose from the closed sets only** - never a state model / trigger / action /
   sentinel that doesn't already exist (the gate rejects it; new ones are engine work).
2. **Capture team/person/source specifics to the profile** - never into the entry
   (invariant #1 / #4); the entry stays denylist-clean.
3. **Gate green before commit** (`validate-program-type` + `program_schema.py`).
4. **Stage only `registry.json`** via `factory_lib` - never `git add -A`.
5. **Git is never user-facing** - speak in Keep / Undo.

## Common Mistakes

| Mistake | Fix |
|---|---|
| Inventing a new state model / emitter action / sentinel | Compose from the closed sets; new ones are engine work, hand to engineering |
| Writing a sheet URL / channel / team name into the entry | Capture it to the profile; the entry references the engine seam only |
| `phases` on a non-pipeline type | Phases are pipeline-only (the gate rejects otherwise) |
| Hardcoding a color in a presentation chip | chip_tokens reference theme tokens only |
| Telling the operator about the commit | Speak in Keep / Undo |

## Related Skills

- **meta-factory-core**: the shared lifecycle + receipt mechanism (read first).
- **meta-create-skill**: the TDD spine.
- **meta-create-worker** / **meta-create-card-type** / **meta-create-adapter**: the siblings.
</content>
