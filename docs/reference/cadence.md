# Cadence — the second organ (reference)

The agent-first map of Cadence, Magnolia's standing-loop subsystem. This is a **map, not a spec** — the canonical truth lives in code (each section links it under **Canonical source:**). The full design rationale and the 11-slice history live in [`docs/plans/2026-06-12-cadence-design-brief.md`](../plans/2026-06-12-cadence-design-brief.md); read this doc first, that one for the why. When this doc and the code disagree, the code wins.

## What Cadence is (and how it relates to the task board)

The task board is the **verbs of the operator's life** — discrete items routed through their attention. Cadence is the **state of their programs** — standing loops that hold *declared intent* against *observed reality* on a schedule, and emit a verb onto the task board **only when something genuinely needs a human**. It is an agentic TPM attached to the chief of staff: it tracks, reconciles, nudges, prepares, and verifies.

Cadence performs **zero external writes itself.** Every outward action it wants (a nudge, a digest, an escalation) is emitted as an ordinary task into the existing queues, governed by the existing judge, trust ladder, and Tier-2 confirm. No second shipper, no second ladder. Most of Cadence is **Tier-1** (local files + local cards); the only outward reach rides the existing Tier-2 send path.

## The primitive vocabulary

| Primitive | Job | Task-side analog |
|---|---|---|
| **Program** | A unit of custody: declared intent + dates + state, held over time. A markdown file with YAML frontmatter under `datasets/programs/PROG-NNNN.md`. | Task (but persistent) |
| **Program type** | Declarative shape: state model, phases/fields, cadence, sentinels, emitters, sources. One entry in `cadence/programtypes/registry.json`. | Card type in `registry.json` |
| **State model** | One of a **closed set of four** (pipeline / cycle / target / register). | (the fuck-up fence) |
| **Sentinel** | A read-only agent that READS sources and returns observation records; a deterministic runner records them. The LLM never writes. | Worker (same dispatch substrate, different contract) |
| **Observation** | Append-only, source-cited evidence on a program (`## Observations` ledger). | Activity-log entry |
| **Reconciler** | Per cycle: declared vs observed → drift verdict + mechanical state updates (facts) + proposal cards (interpretations). | Judge + enforce_lib |
| **Emitter** | Declarative `on: <trigger> → action` playbook; every action exits as a task. | shipper / card actions |
| **Cycle** | One heartbeat: observe → reconcile → emit → log. | Cron tick + receipt |

**Canonical source:** `scripts/program_lib.py` (program CRUD + `render_view` + the registry loader); `cadence/programtypes/registry.json` (the type registry).

## The four state models (closed set)

A type chooses exactly one. Drift verdicts are uniform across all four — `holding` / `drifting` / `broken` — so the UI and escalation never special-case a type; what *computes* the verdict is model-specific.

| Model | Shape | Drift means |
|---|---|---|
| **pipeline** | Ordered phases with entry/exit windows | Phase overage, date slip, sequence violation |
| **cycle** | Recurring steady-state with a cadence artifact; no phases | Artifact late/missing; captured items not reflected |
| **target** | Metric(s) vs expected trajectory | Actual diverging beyond tolerance |
| **register** | A set of items, each with an owner + closure condition | Item aging past policy; orphaned/unverified closures |

A new state model requires a design doc and a gate change — deliberately hard. **Canonical source:** `scripts/cadence/reconcile.py` (the per-model verdict functions).

## The cycle pipeline (observe → reconcile → emit → log)

1. **Observe.** A sentinel runs on the existing `claude -p` dispatch substrate but with a distinct contract: output is **observations**, never artifacts or tasks. Two mechanical-vs-interpretive kinds today: `movement-watch` (LLM, attributes transcript signals to a program), `tracker-truth` (mechanical, reads the project-management adapter's free read), `sheet-watch` (reads the EOS sheet live via the M365 MCP), `program-intake` (routing). The runner applies records; **the LLM never writes a file.**
2. **Reconcile.** `scripts/cadence/reconcile.py` per program: deterministic checks first (dates, aging, adapter truth), judged interpretation only where determinism can't reach. Outputs a drift verdict, mechanical state updates (each backed by a cited observation), and proposals.
3. **Emit.** Match the type's emitter playbook. **Every action becomes a task:** `escalate` → a human-queue card; `draft-message` → a rate-capped collab nudge (Tier-2 send path); `produce-artifact` → dispatches a worker that writes a versioned artifact + a send card; `propose-update` → a recommendation card whose accept applies the program mutation (climbs the trust ladder, shadow by default).
4. **Log.** Append a `## Cycles` entry: what was checked, observed, emitted, and the verdict.

**Fact vs interpretation — the two doors.** Program state mutates through exactly two doors: **(a) facts** — adapter-grounded, applied mechanically with a cited observation; **(b) interpretations** — emitted as proposal cards a human approves. A reconcile never silently moves state without evidence.

**Rate fence (Goodhart guard).** Per-person nudge caps (`max_nudges_per_person_per_week`) are part of the emitter schema, enforced by the reconciler off a period-keyed counter on the program — the cheap "nudge harder" path is fenced in the schema, not left to good behavior.

**The clock.** A `CadenceScheduler` daemon (sibling to `CronScheduler`) in `task_server.py` ticks reconcile on a schedule and on board startup. Reconcile is idempotent, so granularity is forgiving. **Canonical source:** `scripts/cadence/reconcile.py`; `scripts/sentinel_runner.py` + `scripts/sentinel_lib.py` + `scripts/sentinels/*.md`.

## The program lifecycle

`candidate → active → paused → archived`. Drift is computed only for `active`; `paused` keeps observing but mutes emitters; **nothing is ever deleted** (invariant #6 — archived files move to `datasets/programs/archive/` version-suffixed).

- **Intake / birth.** Intake sentinels route new exhaust with a closed verb set (`observe` / `capture` / `candidate` / `ignore`) scoped to the active type registry (the registry doubles as the classification taxonomy). Candidates accumulate source-cited evidence in a seeded `program-intake` register (the nursery); a **birth proposal** card fires only when the type's `birth_threshold` is crossed. Accept → a new active program + the type's `bootstrap_emissions` enqueued.
- **Death.** The reconciler proposes archive through the two doors: **fact** (terminal phase / tracker epic closed / did-it-work verified) and **interpretation** (silent ≥ N cycles AND the watching sentinel is *live*). Both produce `propose-update {op: archive}`, deduped by op.
- **The janitor.** Portfolio maintenance is itself a seeded `portfolio-health` (register) program: it scans for blind sentinels, stale actives, aging candidates, duplicates, and supply, and reports (severity-aware verdict). It is governed by the same machinery it maintains.
- **Blind vs dormant.** A sentinel that *ran but found nothing* is LIVE (a dormant program is archivable); one that *couldn't run* (errored / unconfigured / def-unloadable) is BLIND (escalate, suppress archive). Per-sentinel telemetry lives in `datasets/cadence/sentinel-runs.json`.

**Canonical source:** `scripts/cadence/reconcile.py` (`_propose_archive*`, `_scan_portfolio_health`, intake routing); `scripts/program_lib.py` (`upsert_candidate`, `birth_program`, `_apply_archive`).

## The UI — the Cadence tab

A read-only, top-level board tab rendered **entirely from the registry + program frontmatter** (theme tokens only, like cards). Table-first, grouped by `family` shelves (presentation-only labels; only non-empty shelves render). One row per program: headline · state chip · drift badge · next checkpoint · last-cycle one-liner · needs-you count. Row expansion shows the time view, the observation ledger, the emission history, and a grounding block. **Nothing on this tab performs an external action.** Served by `GET /api/cadence` (`program_lib.build_cadence_payload`); rendered by `ui/task-board/js/cadence.js`.

## Extending Cadence

Adding a persona's whole world should be **dropping one program-type entry**, not writing engine code:

- **A new program type** → run **`meta-create-program-type`** (the 4th `meta-create-*` factory sibling): it composes a registry entry from the closed sets, captures team/source nuance to the profile (never the entry), runs the `program_schema.py` gate, and emits a Keep/Undo receipt. A new state model / emitter action / sentinel is engine work, not a factory job.
- **A starter bundle** for cold-start onboarding → an entry in `cadence/starter-sets.yaml` (consumed once at setup, never at runtime; validated by `scripts/starter_sets.py`).

## Invariants that bind Cadence

- **#9** — program-type definitions are declarative and closed-set; the `program_schema.py` gate (the fifth green gate) makes a malformed registry structurally impossible.
- **#1** — no person/team/channel/sheet-locator literal in any type, sentinel, or worker; identity reads via `profile_lib` (e.g. the EOS sheet locator is `profile_lib.eos_sheet`).
- **#5** — Cadence creates no new external-write surface; outward actions ride the existing Tier-2 `adapters.publish` seam.
- **#6** — observation ledgers and cycle logs are append-only; archived programs are version-suffixed, never deleted.
- **#8** — all runtime-emitted strings are ASCII-safe.

See [`invariants.md`](./invariants.md). **History / full spec:** [`docs/plans/2026-06-12-cadence-design-brief.md`](../plans/2026-06-12-cadence-design-brief.md) and the per-increment plans (`2026-06-1*-cadence-*`).
</content>
