# Cadence — slices 1+2 design: substrate + read-only tab

> 2026-06-16. Approved design for the first `/magnolia-build` increment of Cadence, the
> standing-loop subsystem. Source brief: [`2026-06-12-cadence-design-brief.md`](./2026-06-12-cadence-design-brief.md)
> (the full 11-slice epic). Companion: [`2026-06-12-loop-abstractions-brainstorm.md`](./2026-06-12-loop-abstractions-brainstorm.md).
> Designer front-end handoff: `Magnolia - Cadence.zip` (`design_handoff_cadence/Cadence.dc.html` + `README.md`).
>
> This document scopes **slices 1 and 2 only** — the substrate and the read-only Cadence tab.
> Slices 3–11 (reconciler, sentinels, emitters, lifecycle/intake, grounding, factory) are out of
> scope here and tracked in the brief's §10.

## Why this increment

Cadence is "the second organ of Magnolia": where the task board is the **verbs** of the operator's
life, Cadence is the **state of their programs** — standing loops that hold declared intent against
observed reality and emit a task only when a human is genuinely needed. The brief sequences it into
11 vertical, independently-verifiable slices. This build delivers the foundation the other nine
stand on:

- **Slice 1 — substrate:** program file format + `program_lib.py` CRUD + program-type registry +
  `program_schema.py` gate wired into the green gates.
- **Slice 2 — Cadence tab v1:** the designer's read-only ledger, rendered from a real
  `GET /api/cadence` over program files.

No agents, no cron, no emissions. Tier-1 throughout: the tab is read-only and there are no emitters,
so this slice performs **zero external writes**.

## Guiding discipline

Every piece mirrors an existing strict pattern — this is deliberate, per the brief's "replicate the
board's Lego quality":

| New surface | Mirrors | Pattern |
|---|---|---|
| `scripts/program_lib.py` | `scripts/task_lib.py` | frontmatter+body files, counter-allocated IDs, `platform_lib` locking |
| `scripts/program_schema.py` | `scripts/card_schema.py` | load registry → validate closed-sets + token-only → print `… OK`; a green gate |
| `cadence/programtypes/registry.json` | `ui/task-board/cardtypes/registry.json` | declarative, gated, presentation = theme tokens only |
| `datasets/programs/PROG-NNNN.md` | `datasets/tasks/**/TASK-NNNN.md` | per-person content; the engine never embeds it |
| `GET /api/cadence` | `GET /api/cron` | read endpoint in `task_server.py` `_route_request` → `_json_response` |
| `ui/task-board/js/cadence.js` | `ui/task-board/js/schedules.js` | fetch → build HTML string → `innerHTML`; module-level state |

Honors the invariants: files-based/local-first, declarative gated types, append-only evidence,
profile-driven identity (no literals), theme-token-only presentation, portability via `platform_lib`.

## 1. Layout & the engine/personal boundary

The registry(engine) / instances(datasets) split mirrors cards(engine) / tasks(datasets):

| Shared engine (gated, de-personalized) | Personal content (`datasets/`) |
|---|---|
| `cadence/programtypes/registry.json` — program-type registry + family labels/order | `datasets/programs/PROG-NNNN.md` — program instances |
| `scripts/program_lib.py` — CRUD + render mapping | `datasets/programs/_counter` — ID allocator |
| `scripts/program_schema.py` — the gate | |
| `ui/task-board/js/cadence.js` + tab markup + CSS | |

Family **labels and order** are generic engine vocabulary (Roadmap/Weekly/Outcomes/EOS), not
person/team identity, so they live in the shared gated registry — not hardcoded in JS. Per-person
rename/regroup/reorder (brief §8) is a deferred thin add.

## 2. Program-type registry — `cadence/programtypes/registry.json`

Declarative and gated. Seeds the leanest set of brief-aligned types that still renders **all four
state models across all four families** (slice 2's tab needs every row model renderable; the
*behaviors* for `target`/`register` reconciliation stay deferred — only read-only rendering is
needed here):

- `roadmap-initiative` — `pipeline` — family `roadmap`
- `weekly-priorities`, `eng-sync-prep` — `cycle` — family `weekly`
- `did-it-work` — `target` — family `outcomes`
- `eos-rock` — `pipeline` — family `eos`
- `eos-scorecard-digest`, `eos-l10-prep` — `cycle` — family `eos`
- `eos-issues` — `register` — family `eos`

(Final list trimmed during planning to the minimum that still exercises every model.)

Registry shape (per the brief §3, reduced to slice 1+2 fields):

```jsonc
{
  "families": [
    { "id": "roadmap",  "label": "Roadmap",  "order": 1 },
    { "id": "weekly",   "label": "Weekly",   "order": 2 },
    { "id": "outcomes", "label": "Outcomes", "order": 3 },
    { "id": "eos",      "label": "EOS",      "order": 4 }
  ],
  "types": [
    {
      "id": "roadmap-initiative",
      "label": "Roadmap initiative",
      "family": "roadmap",
      "state_model": "pipeline",
      "phases": [
        { "id": "discovery", "label": "Discovery", "max_age_days": 21 },
        { "id": "planning",  "label": "Planning",  "max_age_days": 14 },
        { "id": "execution", "label": "Execution" },
        { "id": "shipped",   "label": "Shipped" },
        { "id": "verified",  "label": "Verified", "terminal": true }
      ],
      "cadence": "weekly",
      "sources": [
        { "kind": "transcripts", "mode": "read" },
        { "kind": "project_management", "mode": "read" }
      ],
      "presentation": { "chip_tokens": { "discovery": "--text-dim", "execution": "--accent" } }
    }
    // … other types
  ]
}
```

## 3. Program instance file — `datasets/programs/PROG-NNNN.md` (brief §4)

Single file per program (the directory-split threshold is a deferred open question, brief §11).
Frontmatter + body exactly per brief §4:

```markdown
---
program_id: PROG-0007
type: roadmap-initiative
status: active                 # candidate | active | paused | archived
title: "Payments reconciliation revamp"
owner_role: product            # role reference, never a name (invariant #1)
created: 2026-06-12T09:00:00Z
phase: execution
phase_entered: 2026-06-01
checkpoints:
  - { id: discovery-exit, due: 2026-05-19, instrument: "human-attested", status: met }
  - { id: ship,           due: 2026-09-15, instrument: "adapter:project_management", status: pending }
bindings:
  - { id: tracker, role: truth, kind: project_management, anchor: "EPIC-204", mode: read }
drift: holding                 # holding | drifting | broken | blind (cached verdict)
last_cycle: 2026-W24
---

## Intent
One paragraph: what this program holds and why it matters.

## Observations
### 2026-06-11 — sentinel:movement-watch [status-signal]
source: datasets/meetings/…md
claim: Closed 4 of 9 stories this week.

## Cycles
### 2026-W24 — holding
checked: tracker, checkpoints · emitted: none · next: ship in 96d
```

~13 seed instances across all four families, in the prototype's voice (the prototype's
`programs = [...]` array is the canonical tone/data reference). In slice 1+2 the `drift`, `phase`,
and the Observations/Cycles are **authored**; the slice-3 reconciler will later compute and write
them into this same file with no schema rework.

## 4. `scripts/program_lib.py` — mirrors `task_lib.py`

- `_parse_program_file(path) -> (frontmatter, body)`
- `_write_program_file(path, fm, body)` — YAML round-trip parse-back validation (task_lib pattern)
- `_next_id()` — `PROG-{:04d}` via `datasets/programs/_counter`, `platform_lib.lock/unlock`
- `create_program(...)`, `read_program(program_id)`, `list_programs(status=…)`
- `load_registry()` — read + return the program-type registry
- **`render_view(program, registry) -> dict`** — maps the canonical file to the prototype's render
  contract: derives `current` phase index from `phase` + the type's `phases`; the Activity feed from
  recent `## Observations`; metric `series`/delta for `target`; `drift → tone`; age inputs for
  `register`; `health` word. Lives here (not in the server) so it is unit-testable.

## 5. `scripts/program_schema.py` — mirrors `card_schema.py`; green gate #5

Validates (slice 1+2 subset):
- `state_model` ∈ `{pipeline, cycle, target, register}`
- `phases` present **only** on `pipeline` types
- every type has a `family` that resolves to the registry `families` block
- `presentation` references theme tokens only (values start with `--`)
- every `source` has an explicit `mode`
- **no identity literals** — the denylist scan in `tests/test_engine_no_jay.py` extends to
  `cadence/**`
- prints `programtypes OK` on success

Wired into the gate runners and recorded in `docs/reference/conventions.md` §2 + `invariants.md`
(gate #5). New unit tests assert it **rejects** bad registries (phases on a non-pipeline type, a
non-token presentation value, an unknown state_model, a missing family).

### ⚠️ DEFERRED — circle back (do not lose)
`program_schema.py` here validates only what slices 1+2 use. The **full §3 schema** —
emitter closed-action-set, read-mode sources have no write-capable emitter, sentinel read-only tool
lists, `intake.route` closed set + `route:candidate` requires `birth_threshold`, checkpoint-instrument
resolvability — must be finished as slices 3/5/6/8 land. This is recorded in this section and saved
to agent memory at ship so a future session does not assume the gate is complete.

## 6. `GET /api/cadence` — mirrors `GET /api/cron`

Read-only. Returns:

```jsonc
{ "families": [ { "id": "roadmap", "label": "Roadmap",
                  "programs": [ /* render_view dicts */ ] }, … ] }
```

Built by `list_programs()` → `render_view()` grouped by the type's family, only non-empty families,
in registry `order`. Routed in `task_server.py` `_route_request`, returned via `_json_response`.
No POST/PUT — the tab mutates nothing.

## 7. UI — `ui/task-board/js/cadence.js` mirrors `js/schedules.js`

1. Nav button in `.topbar-tabs` (between Now and Schedules) + `#tab-cadence` panel in `index.html`.
2. `js/cadence.js`: `fetchCadence()` (loads `/api/cadence`, stores, calls `renderCadence()`),
   `renderCadence()` (build HTML string → set `#cadence-view` `innerHTML`). Expanded-row state = a
   module-level `Set` of program ids; toggling re-renders / toggles a class.
3. `switchTab('cadence') → fetchCadence()` in `app.js`; add `'cadence'` to the deep-link `known`
   array; add `<script src="/js/cadence.js">` before `app.js`.
4. CSS: a token-only `/* ─── Cadence ─── */` section lifted from the prototype (every prototype rule
   is already token-based). Use `escapeHtml`/`formatDate` (core.js) and `svgIcon` (icons.js).
5. **No Mood code** — inherits all six Moods for free (token-only). The prototype's mock top-bar,
   toasts, and `cadence-mood` localStorage key are dropped; the real app chrome + `pmos-mood` apply.

All four row models recreated faithfully (`pipeline` stepper, `target` metric + SVG predicted-vs-
actual chart, `cycle` week-cells, `register` aging items) plus the expand panel
(intent / history / checkpoints / activity / bindings / footer), per the handoff README and the
prototype's `rowVM()` + `buildSeries()` math.

## 8. Gates & e2e verification

All **five** gates green before any commit: `python3 -m pytest`, `python3 scripts/card_schema.py`,
`python3 -m pytest tests/test_engine_no_jay.py`, `python3 scripts/portability_gate.py`, and the new
`python3 scripts/program_schema.py`. New unit tests cover `program_lib` (CRUD round-trip +
`render_view` mapping for each model) and `program_schema` (accepts seeds, rejects malformed types).

Live e2e: launch the dev board on **:8743**, open Cadence, confirm all four row models render and
expand against the seed programs, across multiple Moods (Chrome headless visual pass — see the
`visual-pass-technique` memory). Never the prod board (:8742) (invariant #7).

## 9. Out of scope / deferred

- **Finish `program_schema.py`** to the full §3 schema (see §5 ⚠️).
- Profile-level family rename/regroup/reorder (brief §8).
- The `blind` drift verdict — supported in `render_view`'s tone mapping, but has no producer until
  binding-health checks (slice 8); it will not appear in this slice.
- Slices 3–11: reconciler, sentinels, emitters, lifecycle/intake/grounding, `meta-create-program-type`
  factory, attachments, EOS read-only sheet source, portfolio rollup.

## 10. Build order (refined in the implementation plan)

1. `program_lib` + program file format + `_counter` + unit tests (CRUD).
2. `cadence/programtypes/registry.json` seed types + `program_schema.py` gate + denylist extension +
   gate-rejection tests; wire into gate runners.
3. Seed ~13 program instances; `render_view` mapping + its unit tests.
4. `GET /api/cadence` endpoint.
5. UI: tab markup, `js/cadence.js`, CSS, `switchTab`/deep-link wiring.
6. Five gates green → live e2e across Moods → ship per merge authority.
